"""SLA benchmarks, run from the site.

The Lab triggers the scenarios locally; the SaaS measures itself (latency_samples, delivery ledger,
collector state). This module logs in to the SaaS dashboard API as a tenant user and scores those
measurements against the SLA, so a judge can fire a scenario on the laptop and watch the verdict.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

# SLA targets (ms) from the brief; the SaaS uses the same targets (api SLO_TARGET_MS).
TARGETS = {"e2e": 10_000, "alert": 15_000, "offline": 60_000}


class Saas:
    def __init__(self) -> None:
        self.url = os.environ.get("SAAS_URL", "").rstrip("/")
        self.email = os.environ.get("SAAS_EMAIL", "admin@acme.test")
        self.password = os.environ.get("SAAS_PASSWORD") or os.environ.get("DEMO_PASSWORD", "trustline-demo")
        self.token: str | None = None
        self.last_error: str | None = None

    def configure(self, url: str | None, email: str | None, password: str | None) -> None:
        if url is not None:
            self.url = url.strip().rstrip("/")
        if email:
            self.email = email
        if password:
            self.password = password
        self.token = None

    async def _login(self, c: httpx.AsyncClient) -> None:
        r = await c.post("/api/auth/login", json={"email": self.email, "password": self.password})
        if r.status_code != 200:
            raise RuntimeError(f"login as {self.email} failed: HTTP {r.status_code}")
        self.token = r.json()["token"]

    async def get(self, paths: dict[str, str]) -> dict[str, Any]:
        if not self.url:
            raise RuntimeError("SaaS URL not set")
        async with httpx.AsyncClient(base_url=self.url, timeout=15, follow_redirects=True) as c:
            if not self.token:
                await self._login(c)
            out = {}
            for name, path in paths.items():
                r = await c.get(path, headers={"Authorization": f"Bearer {self.token}"})
                if r.status_code == 401:  # token expired: log in again once
                    await self._login(c)
                    r = await c.get(path, headers={"Authorization": f"Bearer {self.token}"})
                r.raise_for_status()
                out[name] = r.json()
            return out


def _p95(slo: dict, kind: str) -> tuple[float | None, int, float | None]:
    k = (slo.get("kinds") or {}).get(kind) or {}
    return k.get("p95"), int(k.get("n") or 0), k.get("max")


def _check(cid, title, target, measured, n, unit, scenario, detail, ok=None) -> dict:
    if ok is None:
        ok = None if measured is None or n == 0 else measured <= target
    return {"id": cid, "title": title, "target": target, "measured": measured, "n": n, "unit": unit,
            "pass": ok, "scenario": scenario, "detail": detail}


def evaluate(d: dict, collector: dict | None, minutes: int) -> dict:
    slo, fleet, dlv, inc, src = d["slo"], d["fleet"], d["delivery"], d["incidents"], d["sources"]
    e2e, n_e2e, mx_e2e = _p95(slo, "dash")
    al, n_al, _ = _p95(slo, "alert")
    off, n_off, mx_off = _p95(slo, "offline")
    checks = [
        _check("e2e", "End-to-end latency (event -> dashboard), p95", TARGETS["e2e"], e2e, n_e2e, "ms", None,
               f"{n_e2e} samples in {minutes} min; worst {round(mx_e2e) if mx_e2e else '—'} ms; backlog replays excluded"),
        _check("alert", "Alert creation (first breach -> alert), p95", TARGETS["alert"], al, n_al, "ms", "sla_flood",
               f"{n_al} alerts measured" if n_al else "no alert yet - run 'Login flood' or 'Latency storm'"),
        _check("offline", "Collector offline detection", TARGETS["offline"], mx_off if n_off else None, n_off, "ms",
               "sla_offline", f"{n_off} offline events (worst case shown)" if n_off else
               "not measured yet - run 'Connection lost' (heartbeats stop for 90 s)"),
    ]
    # resync: every assigned seq arrived exactly once; nothing left buffered
    led = dlv.get("ledger") or []
    gaps = sum(int(r.get("gaps") or 0) for r in led)
    gaps_open = sum(int(r.get("gaps_open") or 0) for r in led)
    deduped = sum(int(r.get("js_duplicates") or 0) + int(r.get("db_conflicts") or 0) for r in led)
    received = sum(int(r.get("received") or 0) for r in led)
    lag = sum(max(0, int(r.get("last_assigned_seq") or 0) - int(r.get("last_acked_seq") or 0)) for r in led)
    buf = (collector or {}).get("outbox", {}).get("depth")
    buffered = bool(buf) or lag > 0
    loss_ok = gaps == 0 and gaps_open == 0
    checks.append({
        "id": "resync", "title": "Outage resync: 100% delivery, 0 loss, 0 duplicates", "target": 0, "unit": "gaps",
        "measured": gaps + gaps_open, "n": received, "scenario": "sla_offline",
        "pass": None if buffered and loss_ok else loss_ok,
        "detail": (f"{received} payloads stored once each; {deduped} re-sends deduplicated (stored 0 times); "
                   f"{gaps} sequence gaps" + (f"; {buf} still buffered on site - draining at 50/s" if buffered else "")),
    })
    cols = fleet.get("collectors") or []
    srcs = src.get("sources") or []
    stale = [s for s in srcs if s.get("stale")]
    incs = inc.get("incidents") or []
    now = time.time()

    def recent(i):
        try:
            from datetime import datetime
            return now - datetime.fromisoformat(str(i["opened_ts"]).replace("Z", "+00:00")).timestamp() < minutes * 60
        except Exception:  # noqa: BLE001
            return False

    rec = [i for i in incs if recent(i)]
    raw = sum(int(i.get("alert_count") or 0) for i in rec)
    return {
        "checks": checks,
        "collectors": [{k: c.get(k) for k in ("collector_id", "state", "state_reason", "buffer_depth", "last_acked_seq",
                                               "last_assigned_seq", "last_hb_event_ts")} for c in cols],
        "stale_sources": len(stale), "sources": len(srcs),
        "noise": {"incidents": len(rec), "alerts": raw,
                  "reduction": round(1 - len(rec) / raw, 3) if raw else None},
        "recent_incidents": [{k: i.get(k) for k in ("incident_id", "title", "priority", "status", "alert_count",
                                                     "opened_ts", "confidence", "rule")} for i in rec[:8]],
        "hops_ms": slo.get("hops_ms") or {}, "saas_targets_ms": slo.get("targets") or {}, "minutes": minutes,
    }
