"""Background tasks: heartbeat, pseudonymised topology, remediation action pull, purge."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
import psutil

from .models import Counters
from .redact.pseudonym import Pseudonymizer
from .settings import VERSION, Settings
from .stages.s7_transmit import Transmit, ssl_context
from .stores import Stores

log = logging.getLogger("collector.tasks")
START = time.time()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def heartbeat(q: asyncio.Queue, s: Settings, stores: Stores, c: Counters, tx: Transmit) -> None:
    proc = psutil.Process(os.getpid())
    proc.cpu_percent(None)
    while True:
        await asyncio.sleep(s.heartbeat_s)
        st = await asyncio.to_thread(stores.outbox.stats)
        await q.put({
            "type": "heartbeat", "event_ts": iso(time.time()), "src_tz": s.src_tz, "version": VERSION,
            "config_hash": s.config_hash, "cpu": round(proc.cpu_percent(None), 2),
            "mem_mb": round(proc.memory_info().rss / 1e6, 1), "buffer_depth": st["depth"],
            "buffer_bytes": st["bytes"], "last_acked_seq": st["last_acked_seq"],
            "last_assigned_seq": stores.state.peek_seq(), "redactions_total": c.redactions_total,
            "blocked_total": c.blocked_total, "dup_source_events": c.dup_source_events,
            "late_beyond_horizon": c.late_beyond_horizon, "uptime_s": int(time.time() - START),
            "saas_reachable": tx.reachable is not False,
        })


def topology_record(s: Settings, pseudo: Pseudonymizer) -> dict | None:
    topo = s.topology or {}
    nodes = topo.get("nodes") or []
    if not nodes:
        return None
    prefix = {"host": "h", "svc": "svc", "link": "link"}
    kinds = {n["name"]: n.get("kind", "host") for n in nodes}
    roles = {"web", "api", "db", "gateway", "uplink", "worker", "other"}

    def code(name: str) -> str:
        return pseudo.code(prefix.get(kinds.get(name, "host"), "h"), name)

    return {
        "type": "topology", "event_ts": iso(time.time()),
        "nodes": [{"src": code(n["name"]), "kind": n.get("kind", "host"),
                   "role": n.get("role") if n.get("role") in roles else "other"} for n in nodes],
        "edges": [{"dependent": code(e["dependent"]), "dependency": code(e["dependency"]),
                   "kind": e.get("kind", "depends_on")} for e in (topo.get("edges") or [])
                  if e.get("dependent") in kinds and e.get("dependency") in kinds],
    }


async def topology(q: asyncio.Queue, s: Settings, pseudo: Pseudonymizer) -> None:
    while True:
        rec = topology_record(s, pseudo)
        if rec:
            await q.put(rec)
        await asyncio.sleep(300)


async def actions(q: asyncio.Queue, s: Settings, pseudo: Pseudonymizer, stores: Stores, events: list) -> None:
    """Pull human-approved remediation actions OUTBOUND through the same mTLS door.
    The SaaS can never push into the site; the collector decides, against a local allow-list."""
    allow = set(s.cfg.get("actions_allowlist") or [])
    ctx = ssl_context(str(s.ca_file), s.client_cert)
    done: set[str] = set(stores.state.get("done_actions", []))
    async with httpx.AsyncClient(base_url=s.ingest_base, verify=ctx, timeout=5) as saas, \
            httpx.AsyncClient(base_url=s.simulator_url, timeout=5) as site:
        while True:
            await asyncio.sleep(2)
            try:
                r = await saas.get("/v1/actions", params={"collector": s.collector_id})
                if r.status_code != 200:
                    continue
                items = r.json().get("actions", [])
            except Exception:
                continue
            for a in items:
                aid = a["action_id"]
                if aid in done:
                    continue
                key = a["action_key"]
                code = a.get("target")
                real = pseudo.reverse.get(code) or (stores.state.lookup(code) or {}).get("real") if code else None
                entry = {"ts": time.time(), "action_id": aid, "key": key, "target_code": code, "target_real": real}
                if key not in allow:
                    status, detail = "refused_local_policy", f"{key} not in local actions_allowlist"
                else:
                    try:
                        res = (await site.post("/action", json={"site": s.site, "key": key, "target": real})).json()
                        status = "executed" if res.get("ok") else "failed"
                        detail = res.get("detail", "")
                        if key.startswith(("failback", "unblock", "scale_in")) and status == "executed":
                            status = "rolled_back" if a.get("rollback_of") else status
                    except Exception as e:
                        status, detail = "failed", f"{type(e).__name__}"
                entry.update(status=status, detail=detail)
                events.append(entry)
                del events[:-100]
                done.add(aid)
                stores.state.set("done_actions", list(done)[-500:])
                await q.put({"type": "action_result", "event_ts": iso(time.time()), "action_id": aid,
                             "status": status, "code": key[:32]})
                log.info("action %s %s -> %s (%s)", key, real, status, detail)


async def purge(s: Settings, stores: Stores) -> None:
    ret = s.cfg.get("retention") or {}
    while True:
        await asyncio.to_thread(stores.raw.purge, float(ret.get("raw_days", 7)))
        await asyncio.to_thread(stores.egress.purge, float(ret.get("egress_days", 30)))
        await asyncio.to_thread(stores.outbox.purge_acked, 3600)
        await asyncio.sleep(3600)


def new_id() -> str:
    return str(uuid.uuid4())
