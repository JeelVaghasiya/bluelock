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

from collections import deque

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .control import Taps, build_router

from .engine import SiteRuntime
from .faults import CATALOGUE, DEFAULT_TARGETS, KIND_TARGETS, Fault
from . import signals
from .sites import PROFILES, SITES

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
# standard = the 5-host demo site; large = 24 hosts / 7 services / 4 links with a compressed day (see sites.py)
PROFILE = os.environ.get("SIM_PROFILE", "standard") if os.environ.get("SIM_PROFILE") in PROFILES else "standard"
signals.DAY_S = float(os.environ.get("SIM_DAY_SECONDS") or (2400 if PROFILE == "large" else 86400))

RUNTIMES: dict[str, SiteRuntime] = {
    name: SiteRuntime(PROFILES[PROFILE](name), SEED + i) for i, name in enumerate(PUSH) if name in SITES
}
STATS = {name: {"sent_json": 0, "sent_syslog": 0, "push_errors": 0, "last_error": None} for name in RUNTIMES}
TAPS = {name: Taps() for name in RUNTIMES}
TIMELINE: deque = deque(maxlen=400)
STATIC = Path(os.environ.get("SIM_STATIC", Path(__file__).resolve().parents[1] / "static"))


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
                TAPS[name].capture(now, rt.last_records, lines, len(recs) + len(lines))
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


def do_inject(site: str, kind: str, target: str | None = None, magnitude: float | None = None,
              duration_s: int | None = None, source: str = "api") -> dict:
    if kind not in CATALOGUE:
        raise HTTPException(400, "unknown kind; see /scenarios")
    rt = rt_for(site)
    ttype, mag, dur, _ = CATALOGUE[kind]
    target = target or KIND_TARGETS.get(kind) or (DEFAULT_TARGETS.get(ttype) if ttype else None)
    now = time.time()
    fid = uuid.uuid4().hex[:8]
    duration = duration_s if duration_s is not None else dur
    if kind == "secret_in_log":
        rt.pending_secret.append(target)
    else:
        rt.book.add(Fault(kind, target, magnitude or mag, now, duration, fid))
    entry = {"id": fid, "inject_ts": now, "site": site, "kind": kind, "target": target,
             "expected_src_kind": ttype or ("ip" if "login" in kind or "force" in kind or "fail" in kind else None)}
    ground_truth(entry)
    TIMELINE.append({"ts": now, "kind": "inject", "title": f"{kind}{' on ' + target if target else ''}",
                     "detail": f"magnitude {magnitude or mag}, {duration} s, via {source}", "ok": True, "fault": kind})
    return {"ok": True, **entry}


def do_clear(site: str = "acme", kind: str | None = None, target: str | None = None) -> dict:
    rt = rt_for(site)
    n = rt.book.clear(kind, target)
    if kind is None and target is None:
        rt.book.failed_over.clear()
        rt.book.blocked_ips.clear()
    TIMELINE.append({"ts": time.time(), "kind": "clear", "title": f"cleared {kind or 'all faults'}{' on ' + target if target else ''}",
                     "detail": f"{n} fault(s) removed", "ok": True})
    return {"ok": True, "cleared": n}


@app.post("/inject")
def inject(req: Inject):
    return do_inject(req.site, req.kind, req.target, req.magnitude, req.duration_s)


@app.post("/clear")
def clear(req: Clear):
    return do_clear(req.site, req.kind, req.target)


@app.post("/action")
def action(req: Action):
    """Called by the on-site collector after a human-approved remediation."""
    res = rt_for(req.site).action(req.key, req.target)
    TIMELINE.append({"ts": time.time(), "kind": "action", "title": f"remediation {req.key}{' on ' + req.target if req.target else ''}",
                     "detail": res.get("detail", ""), "ok": bool(res.get("ok"))})
    ground_truth({"inject_ts": time.time(), "site": req.site, "kind": f"action:{req.key}", "target": req.target})
    return res


@app.get("/ground_truth")
def get_ground_truth():
    if not GROUND_TRUTH.exists():
        return []
    return [json.loads(x) for x in GROUND_TRUTH.read_text(encoding="utf-8").splitlines() if x.strip()]


app.include_router(build_router(RUNTIMES, STATS, TAPS, do_inject, do_clear, TIMELINE))

if STATIC.is_dir():  # the Simulator dashboard (dashboard/ -> npm run build:sim)
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (STATIC / path).resolve()
        if path and f.is_file() and STATIC.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(STATIC / "sim.html")


def run() -> None:
    import uvicorn
    uvicorn.run(app, host=os.environ.get("BIND_HOST", "0.0.0.0"), port=8090, log_level="warning")


if __name__ == "__main__":
    run()
