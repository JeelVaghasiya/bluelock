"""TrustLine simulator: realistic site telemetry + fault injection API (:8090).

Emits every second, per site:
  * JSON lines  -> POST {collector}/v1/push   (metrics, probes, checks, app logs)
  * syslog UDP  -> {collector}:5514           (sshd auth lines)
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .engine import SiteRuntime
from .faults import CATALOGUE, DEFAULT_TARGETS, Fault
from .sites import SITES

GROUND_TRUTH = Path(os.environ.get("GROUND_TRUTH", "/data/ground_truth.jsonl"))


def parse_map(s: str) -> dict[str, str]:
    out = {}
    for part in filter(None, (p.strip() for p in s.split(","))):
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


PUSH = parse_map(os.environ.get("SITES", "acme=http://127.0.0.1:8081"))
SYSLOG = parse_map(os.environ.get("SYSLOG_TARGETS", "acme=127.0.0.1:5514"))
SEED = int(os.environ.get("SIM_SEED", "42"))

RUNTIMES: dict[str, SiteRuntime] = {
    name: SiteRuntime(SITES[name], SEED + i) for i, name in enumerate(PUSH) if name in SITES
}
STATS = {name: {"sent_json": 0, "sent_syslog": 0, "push_errors": 0, "last_error": None} for name in RUNTIMES}


def ground_truth(entry: dict) -> None:
    try:
        GROUND_TRUTH.parent.mkdir(parents=True, exist_ok=True)
        with GROUND_TRUTH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


async def emitter() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    async with httpx.AsyncClient(timeout=2.0) as client:
        while True:
            now = time.time()
            for name, rt in RUNTIMES.items():
                recs, lines = rt.tick(now)
                body = "\n".join(json.dumps(r, separators=(",", ":")) for r in recs).encode()
                try:
                    r = await client.post(f"{PUSH[name]}/v1/push", content=body,
                                          headers={"Content-Type": "application/x-ndjson"})
                    r.raise_for_status()
                    STATS[name]["sent_json"] += len(recs)
                except Exception as e:  # collector down: devices drop, like real ones
                    STATS[name]["push_errors"] += 1
                    STATS[name]["last_error"] = f"{type(e).__name__}: {e}"[:200]
                if name in SYSLOG and lines:
                    host, port = SYSLOG[name].rsplit(":", 1)
                    for line in lines:
                        try:
                            sock.sendto(line.encode(), (host, int(port)))
                            STATS[name]["sent_syslog"] += 1
                        except OSError:
                            pass
            # align to the next wall-clock second
            await asyncio.sleep(max(0.05, 1.0 - (time.time() % 1.0)))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(emitter())
    yield
    task.cancel()


app = FastAPI(title="TrustLine simulator", lifespan=lifespan)


class Inject(BaseModel):
    site: str = "acme"
    kind: str
    target: str | None = None
    magnitude: float | None = None
    duration_s: int | None = None


class Clear(BaseModel):
    site: str = "acme"
    kind: str | None = None
    target: str | None = None


class Action(BaseModel):
    site: str = "acme"
    key: str
    target: str | None = None


def rt_for(site: str) -> SiteRuntime:
    if site not in RUNTIMES:
        raise HTTPException(404, f"unknown site {site}; running: {list(RUNTIMES)}")
    return RUNTIMES[site]


@app.get("/scenarios")
def scenarios():
    return {k: {"target": t, "magnitude": m, "duration_s": d, "what": w}
            for k, (t, m, d, w) in CATALOGUE.items()}


@app.get("/status")
def status():
    now = time.time()
    return {name: {"faults": rt.book.summary(now), "failed_over": sorted(rt.book.failed_over),
                   "blocked_ips": sorted(rt.book.blocked_ips), **STATS[name]}
            for name, rt in RUNTIMES.items()}


@app.post("/inject")
def inject(req: Inject):
    if req.kind not in CATALOGUE:
        raise HTTPException(400, f"unknown kind; see /scenarios")
    rt = rt_for(req.site)
    ttype, mag, dur, _ = CATALOGUE[req.kind]
    target = req.target or (DEFAULT_TARGETS.get(ttype) if ttype else None)
    now = time.time()
    fid = uuid.uuid4().hex[:8]
    if req.kind == "secret_in_log":
        rt.pending_secret.append(req.target)
    else:
        rt.book.add(Fault(req.kind, target, req.magnitude or mag,
                          now, req.duration_s if req.duration_s is not None else dur, fid))
    entry = {"id": fid, "inject_ts": now, "site": req.site, "kind": req.kind, "target": target,
             "expected_src_kind": ttype or ("ip" if "login" in req.kind or "force" in req.kind or "fail" in req.kind else None)}
    ground_truth(entry)
    return {"ok": True, **entry}


@app.post("/clear")
def clear(req: Clear):
    rt = rt_for(req.site)
    n = rt.book.clear(req.kind, req.target)
    if req.kind is None and req.target is None:
        rt.book.failed_over.clear()
        rt.book.blocked_ips.clear()
    return {"ok": True, "cleared": n}


@app.post("/action")
def action(req: Action):
    """Called by the on-site collector after a human-approved remediation."""
    res = rt_for(req.site).action(req.key, req.target)
    ground_truth({"inject_ts": time.time(), "site": req.site, "kind": f"action:{req.key}", "target": req.target})
    return res


@app.get("/ground_truth")
def get_ground_truth():
    if not GROUND_TRUTH.exists():
        return []
    return [json.loads(x) for x in GROUND_TRUTH.read_text(encoding="utf-8").splitlines() if x.strip()]


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="warning")


if __name__ == "__main__":
    run()
