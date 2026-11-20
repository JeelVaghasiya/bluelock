"""Organiser shared-stream adapter (MANDATORY for the hackathon).

The organiser's format is unknown until the event, so ingestion is configuration, not code:
`config/organiser_mapping.yaml` picks a transport and maps organiser fields onto the
simulator-style JSON our stage-2 parsers already understand.

Transports: http_push (organiser POSTs to :8081/v1/organiser), http_poll, sse, udp, file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from ..models import RawEvent

log = logging.getLogger("collector.organiser")


def dig(obj: Any, path: str | None) -> Any:
    if not path:
        return None
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def to_iso(v: Any, fmt: str = "auto") -> str:
    if v is None:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if fmt in ("epoch_s", "epoch_ms") or (fmt == "auto" and isinstance(v, (int, float))):
        x = float(v)
        if fmt == "epoch_ms" or (fmt == "auto" and x > 1e11):
            x /= 1000
        return datetime.fromtimestamp(x, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    s = str(v).strip()
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Mapper:
    """Map one organiser record onto our internal simulator-style dict(s)."""

    def __init__(self, mapping: dict, site: str):
        self.m = mapping or {}
        self.f = self.m.get("fields") or {}
        self.site_map = self.m.get("site_map") or {}
        self.metric_map = self.m.get("metric_map") or {}
        self.site = site

    def map(self, rec: dict) -> list[dict]:
        f = self.f
        site_val = dig(rec, f.get("site"))
        site = self.site_map.get(str(site_val), site_val) if site_val is not None else self.site
        if site and site != self.site:
            return []  # another tenant's record: not ours to collect
        base = {"ts": to_iso(dig(rec, f.get("ts")), f.get("ts_format", "auto")), "site": self.site,
                "eid": dig(rec, "id") or dig(rec, "event_id") or dig(rec, "eid")}
        kind = str(dig(rec, f.get("kind")) or "").lower()
        host = dig(rec, f.get("host"))
        out: list[dict] = []
        if kind in ("metric", "metrics", "gauge") or (dig(rec, f.get("metric")) and dig(rec, f.get("value")) is not None):
            name = str(dig(rec, f.get("metric")) or "")
            name = self.metric_map.get(name, name)
            val = dig(rec, f.get("value"))
            if val is not None:
                d = {**base, "kind": "metric", "name": name, "value": float(val), "unit": dig(rec, f.get("unit"))}
                svc = dig(rec, f.get("service"))
                if svc and not host:
                    d["service"] = svc
                else:
                    d["host"] = host or "unknown-host"
                out.append(d)
        elif kind in ("probe", "network", "ping") or dig(rec, f.get("rtt_ms")) is not None:
            out.append({**base, "kind": "probe", "link": dig(rec, f.get("link")) or host or "link",
                        "rtt_ms": float(dig(rec, f.get("rtt_ms")) or 0), "loss": float(dig(rec, f.get("loss")) or 0),
                        "ok": bool(dig(rec, f.get("ok")) if dig(rec, f.get("ok")) is not None else True)})
        elif kind in ("check", "http_check", "availability"):
            out.append({**base, "kind": "check", "service": dig(rec, f.get("service")) or host,
                        "ok": bool(dig(rec, f.get("ok"))), "latency_ms": float(dig(rec, f.get("rtt_ms")) or 0)})
        elif kind in ("auth", "login"):
            outcome = str(dig(rec, f.get("outcome")) or "failure").lower()
            out.append({**base, "kind": "auth", "host": host or "gw", "user": dig(rec, f.get("user")),
                        "src_ip": dig(rec, f.get("src_ip")),
                        "outcome": "success" if outcome in ("success", "ok", "accepted") else "fail"})
        else:  # logs and anything else with a message
            msg = dig(rec, f.get("message"))
            if msg:
                out.append({**base, "kind": "log", "host": host or "unknown-host",
                            "app": dig(rec, f.get("app")) or "app", "level": str(dig(rec, f.get("level")) or "info").lower(),
                            "msg": str(msg)})
        return out


class OrganiserSource:
    def __init__(self, mapping: dict, site: str, emit: Callable[[RawEvent], None]):
        self.mode = (mapping.get("mode") or "off").strip()
        self.url = (mapping.get("url") or "").strip()
        self.token = (mapping.get("token") or "").strip()
        self.fmt = mapping.get("format", "jsonl")
        self.interval = float(mapping.get("poll_interval_s", 1))
        self.mapper = Mapper(mapping, site)
        self.emit = emit
        self.stats = {"mode": self.mode, "received": 0, "mapped": 0, "errors": 0, "last_error": None}

    def handle_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.fmt == "json_array" or text.startswith("["):
            items = json.loads(text)
        else:
            items = [json.loads(line) for line in text.splitlines() if line.strip()]
        for item in items:
            self.handle_obj(item)

    def handle_obj(self, item: dict) -> None:
        self.stats["received"] += 1
        try:
            for d in self.mapper.map(item):
                self.stats["mapped"] += 1
                self.emit(RawEvent(source="organiser", body=json.dumps(item)[:4000], collect_ts=time.time(),
                                   data=d, eid=d.get("eid")))
        except Exception as e:  # malformed organiser record: count, never crash the collector
            self.stats["errors"] += 1
            self.stats["last_error"] = f"{type(e).__name__}: {e}"[:200]

    async def run(self) -> None:
        if self.mode in ("off", "", "http_push"):
            return  # http_push arrives via the ingress API
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        log.info("organiser stream mode=%s url=%s", self.mode, self.url)
        while True:
            try:
                if self.mode == "http_poll":
                    async with httpx.AsyncClient(timeout=10, headers=headers) as c:
                        while True:
                            r = await c.get(self.url)
                            r.raise_for_status()
                            self.handle_text(r.text)
                            await asyncio.sleep(self.interval)
                elif self.mode == "sse":
                    async with httpx.AsyncClient(timeout=None, headers=headers) as c:
                        async with c.stream("GET", self.url) as r:
                            async for line in r.aiter_lines():
                                if line.startswith("data:"):
                                    self.handle_text(line[5:])
                elif self.mode == "file":
                    await self._tail(Path(self.url))
                elif self.mode == "udp":
                    await self._udp()
                else:
                    log.warning("organiser mode %s not supported", self.mode)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(e).__name__}: {e}"[:200]
                await asyncio.sleep(2)

    async def _tail(self, path: Path) -> None:
        pos = 0
        while True:
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    self.handle_text(chunk)
            await asyncio.sleep(self.interval)

    async def _udp(self) -> None:
        host, _, port = self.url.rpartition(":")
        loop = asyncio.get_running_loop()
        src = self

        class P(asyncio.DatagramProtocol):
            def datagram_received(self, data, addr):
                try:
                    src.handle_text(data.decode(errors="replace"))
                except Exception:
                    src.stats["errors"] += 1

        await loop.create_datagram_endpoint(P, local_addr=(host or "0.0.0.0", int(port or 5515)))
        await asyncio.Event().wait()
