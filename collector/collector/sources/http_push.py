"""Ingress HTTP API on :8081 (site network only): simulator and organiser push."""
from __future__ import annotations

import json
import time
from typing import Callable

from fastapi import FastAPI, Request

from ..models import RawEvent
from .organiser import OrganiserSource


def build_app(emit: Callable[[RawEvent], None], organiser: OrganiserSource) -> FastAPI:
    app = FastAPI(title="TrustLine collector ingress", docs_url=None, redoc_url=None)

    @app.post("/v1/push")
    async def push(req: Request):
        body = (await req.body()).decode("utf-8", errors="replace")
        now = time.time()
        n = 0
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                d = None
            emit(RawEvent(source="sim", body=line, collect_ts=now, data=d, eid=(d or {}).get("eid")))
            n += 1
        return {"accepted": n}

    @app.post("/v1/organiser")
    async def organiser_push(req: Request):
        organiser.handle_text((await req.body()).decode("utf-8", errors="replace"))
        return {"ok": True, **organiser.stats}

    @app.get("/healthz")
    async def health():
        return {"ok": True}

    return app
