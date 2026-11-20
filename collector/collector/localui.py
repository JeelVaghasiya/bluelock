"""Collector local UI API (:9090) - ON-SITE ONLY (bound to localhost by compose, hop H9).

This is the only place raw data and the pseudonym reverse map can be seen. It powers the
Egress Proof panel: for any payload, the raw originals next to the exact JSON that left,
its sha256, and the verified hash chain.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import Counters
from .redact.pseudonym import Pseudonymizer
from .settings import VERSION, Settings
from .stages.s1_collect import Collect
from .stages.s6_policy import Policy
from .stages.s7_transmit import Transmit
from .stores import Stores


def build_app(s: Settings, stores: Stores, c: Counters, tx: Transmit, policy: Policy, collect: Collect,
              pseudo: Pseudonymizer, organiser_stats: dict, action_log: list, queues: dict) -> FastAPI:
    app = FastAPI(title="TrustLine collector - on-site UI", docs_url="/local/docs", openapi_url="/local/openapi.json")

    @app.get("/local/status")
    async def status():
        ob = await asyncio.to_thread(stores.outbox.stats)
        now = time.time()
        return {
            "site": s.site, "collector_id": s.collector_id, "region": s.region, "version": VERSION,
            "config_hash": s.config_hash, "ingest_base": s.ingest_base, "src_tz": s.src_tz,
            "saas": {"reachable": tx.reachable, "last_ack_ts": tx.last_ack_ts, "last_error": tx.last_error,
                     "fail_streak": tx.fail_streak, "next_try_in_s": max(0, round(tx.next_try - now, 1))},
            "outbox": {**ob, "oldest_age_s": round(now - ob["oldest_created_ts"], 1) if ob["oldest_created_ts"] else 0},
            "counters": c.as_dict(), "egress_counts": await asyncio.to_thread(stores.egress.counts),
            "raw": await asyncio.to_thread(stores.raw.stats), "organiser": organiser_stats,
            "queues": {k: q.qsize() for k, q in queues.items()}, "overflow": collect.overflow,
            "leak_field_armed": policy.leak_next, "window_s": s.window_s, "batch_ms": s.batch_ms,
        }

    @app.get("/local/raw")
    async def raw(limit: int = Query(200, le=1000), q: str | None = None, secrets: bool = False):
        rows = await asyncio.to_thread(stores.raw.query, limit, q, secrets)
        for r in rows:
            r["spans"] = json.loads(r["spans"]) if r.get("spans") else []
        return rows

    @app.get("/local/egress")
    async def egress(limit: int = Query(100, le=1000), status: str | None = None, before_id: int | None = None):
        return await asyncio.to_thread(stores.egress.list, limit, status, before_id)

    @app.get("/local/egress/entry/{entry_id}")
    async def egress_entry(entry_id: int):
        e = await asyncio.to_thread(stores.egress.by_id, entry_id)
        if not e:
            raise HTTPException(404)
        return await _detail(e)

    @app.get("/local/egress/{payload_id}")
    async def egress_payload(payload_id: str):
        rows = await asyncio.to_thread(stores.egress.by_payload, payload_id)
        if not rows:
            raise HTTPException(404, "unknown payload_id")
        sealed = next((r for r in rows if r["json_text"]), rows[0])
        d = await _detail(sealed)
        d["history"] = [{k: r[k] for k in ("id", "status", "detail", "ts", "entry_hash", "prev_hash")} for r in rows]
        return d

    async def _detail(e: dict) -> dict:
        refs = json.loads(e["raw_refs"]) if e.get("raw_refs") else []
        raws = await asyncio.to_thread(stores.raw.get_many, refs[:50])
        for r in raws:
            r["spans"] = json.loads(r["spans"]) if r.get("spans") else []
        out = {k: v for k, v in e.items() if k != "raw_refs"}
        out["json"] = json.loads(e["json_text"]) if e.get("json_text") else None
        out.pop("json_text", None)
        out["raw_sources"] = raws
        return out

    @app.post("/local/egress/verify")
    async def verify():
        return await asyncio.to_thread(stores.egress.verify)

    @app.get("/local/outbox")
    async def outbox(limit: int = Query(50, le=500)):
        rows = await asyncio.to_thread(stores.outbox.recent, limit)
        return rows

    @app.get("/local/reidentify")
    async def reidentify(code: str | None = None, q: str | None = None):
        if code:
            hit = await asyncio.to_thread(stores.state.lookup, code.strip("<>"))
            if not hit:
                raise HTTPException(404, "code not known on this site")
            return hit
        return await asyncio.to_thread(stores.state.search_codes, q or "", 50)

    @app.get("/local/actions")
    async def actions():
        return list(reversed(action_log))

    @app.post("/local/debug/leak-field")
    async def leak_field():
        policy.leak_next = True
        return {"armed": True, "detail": "the next metric record will carry a raw field 'raw_msg' - "
                                          "stage 6 must block it and log it as BLOCKED"}

    @app.websocket("/local/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        last = 0
        try:
            while True:
                rows = await asyncio.to_thread(stores.egress.list, 20)
                new = [r for r in rows if r["id"] > last]
                if new:
                    last = max(r["id"] for r in new)
                    await sock.send_json({"type": "egress", "items": list(reversed(new))})
                await asyncio.sleep(1)
        except (WebSocketDisconnect, RuntimeError):
            return

    static = Path(__file__).resolve().parent.parent / "static"
    if static.exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            f = static / path
            if path and f.is_file():
                return FileResponse(f)
            return FileResponse(static / "local.html")

    return app
