"""Simulation control plane for the laptop: live telemetry taps, a timeline, scripted stories,
collector debug proxies and trust-boundary tests. Powers the Simulator dashboard at :8090.

Everything here is ON-SITE: real device names are visible, exactly as a site operator would see
their own equipment. None of it is reachable from the SaaS.
"""
from __future__ import annotations

import asyncio
import itertools
import os
import time
from collections import deque
from typing import Any, Callable

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import boundary, sla
from .faults import CATALOGUE
from .logs import SECRETS

COLLECTOR_LOCAL = os.environ.get("COLLECTOR_LOCAL", "http://127.0.0.1:9090")

# ---------------------------------------------------------------- scenario metadata for the UI
# kind -> (category, title, what you should see in TrustLine)
META: dict[str, tuple[str, str, str]] = {
    "cpu_spike": ("Infrastructure", "CPU spike", "Host CPU breaches its threshold and the host turns Poor. A short spike under the persistence rule is suppressed as noise."),
    "mem_leak": ("Infrastructure", "Memory leak", "Memory climbs steadily; L2/L3 models corroborate; incident on the host. Fix: restart_service."),
    "disk_fill": ("Infrastructure", "Disk filling", "Disk forecast shows time-to-full on the Overview; P3 incident. Fix: rotate_logs."),
    "latency_ramp": ("Network", "Uplink latency ramp", "RTT ramps over 60 s; after ~75 s dependent services error. ONE correlated incident, root cause = the link. Fix: failover_uplink."),
    "packet_loss": ("Network", "Packet loss", "Loss bursts on the link; link health drops to Fair/Poor."),
    "link_down": ("Network", "Link down", "Probes fail, dependent services error immediately; P1/P2 incident rooted at the link."),
    "service_errors": ("Application", "Service errors", "Service error rate jumps; service turns Poor; error-rate anomaly with evidence."),
    "login_spray": ("Security", "Password spray", "One IP tries many usernames: L0 'spray' finding at the edge, P2 security incident. Fix: block_source_ip."),
    "brute_force": ("Security", "Brute force", "One IP hammers root: 'brute force' finding."),
    "login_flood": ("Security", "Login flood (>100/s)", "150 failed logins/s from 64 bot IPs. The collector aggregates them into ONE 'login flood' finding (per-IP alerts suppressed); the SaaS correlates everything into ONE incident within 15 s. Fix: enable_auth_rate_limit (reversible)."),
    "success_after_failures": ("Security", "Success after failures", "8 failures then a successful login: critical 'success after failures' finding (likely compromise)."),
    "secret_in_log": ("Privacy", "Secret in a log line", "Redacted ON-SITE before egress. The SaaS only sees <secret:rule>; the collector's Egress Proof shows the red span."),
    "dup_burst": ("Delivery", "Duplicate burst", "Identical event ids re-sent: the collector dedupes them (dup_source_events); dashboards never double count."),
    "out_of_order": ("Delivery", "Out-of-order", "Records arrive 2-8 s late and shuffled: windows are revised (late_revisions), nothing is lost."),
    "clock_skew": ("Delivery", "Clock skew", "Device clocks run fast: the collector measures the skew; heartbeats report it."),
}
EXTRA = {  # collector-side knobs, shown alongside the simulator faults
    "leak_field": ("Privacy", "Developer leak bug", "The next record carries a raw field. The egress allow-list BLOCKS the whole payload; nothing leaves the site."),
    "outage": ("Delivery", "SaaS unreachable", "The collector cannot reach the SaaS: the buffer grows, then drains oldest-first with 0 loss and 0 duplicates."),
}

# ---------------------------------------------------------------- stories
S = dict
STORIES: list[dict] = [
    S(id="full", title="Full judge demo", minutes=7, blurb="Privacy, security, a cascading network fault and the trust boundary, in one run.", steps=[
        S(at=0, op="inject", kind="secret_in_log", target="github_token", label="A developer logs a GitHub token"),
        S(at=8, op="leak", label="A code bug adds a raw field to telemetry"),
        S(at=20, op="inject", kind="login_spray", label="Password spray from 203.0.113.7"),
        S(at=70, op="inject", kind="latency_ramp", target="uplink-1", label="uplink-1 starts degrading"),
        S(at=240, op="boundary", test="spoof_identity", label="Attacker tries to spoof another tenant"),
        S(at=250, op="boundary", test="rogue_ca", label="Rogue collector tries to connect"),
        S(at=270, op="inject", kind="mem_leak", target="api-01", label="Memory leak on api-01"),
    ]),
    S(id="privacy", title="Privacy proof", minutes=1, blurb="Every kind of secret hits the logs; one leak bug; one smuggled raw field at the SaaS.", steps=[
        *[S(at=i * 3, op="inject", kind="secret_in_log", target=rule, label=f"Secret in log: {rule}") for i, (rule, _) in enumerate(SECRETS)],
        S(at=28, op="leak", label="Leak bug: raw field in a record"),
        S(at=36, op="boundary", test="bad_schema", label="Smuggled raw field straight at the SaaS"),
    ]),
    S(id="cascade", title="Network cascade -> one root cause", minutes=4, blurb="Uplink degrades, web and api fail after it. AIOps must raise ONE incident rooted at the link.", steps=[
        S(at=0, op="inject", kind="latency_ramp", target="uplink-1", label="uplink-1 latency ramp (9x)"),
        S(at=30, op="inject", kind="packet_loss", target="uplink-1", magnitude=4, duration_s=200, label="packet loss joins in"),
        S(at=90, op="note", label="Dependents should be erroring now - open the incident"),
    ]),
    S(id="attack", title="Attack chain", minutes=2, blurb="Spray, brute force, then a successful login from an attacker IP.", steps=[
        S(at=0, op="inject", kind="login_spray", label="Password spray"),
        S(at=30, op="inject", kind="brute_force", label="Brute force on root"),
        S(at=60, op="inject", kind="success_after_failures", label="Success after failures"),
    ]),
    S(id="resilience", title="Delivery resilience", minutes=3, blurb="SaaS outage, duplicates, reordering and clock skew - the dashboards must stay exact.", steps=[
        S(at=0, op="outage", seconds=60, label="SaaS unreachable for 60 s"),
        S(at=10, op="inject", kind="dup_burst", magnitude=3, label="Duplicate burst"),
        S(at=20, op="inject", kind="out_of_order", label="Out-of-order delivery"),
        S(at=40, op="inject", kind="clock_skew", magnitude=8, label="Device clocks 8 s fast"),
        S(at=90, op="note", label="Outage over: check Fleet > delivery ledger (0 loss, 0 dup)"),
    ]),
    S(id="gauntlet", title="Trust-boundary gauntlet", minutes=1, blurb="Every attack on the SaaS ingest, one after another. All must be refused.", steps=[
        S(at=i * 4, op="boundary", test=t, label=boundary.TESTS[t][0]) for i, t in enumerate(boundary.TESTS)
    ]),
    S(id="noise", title="Noisy day (alert fatigue)", minutes=3, blurb="Short blips that should NOT page anyone, then one real problem that should.", steps=[
        S(at=0, op="inject", kind="cpu_spike", target="web-01", duration_s=8, label="8 s CPU blip on web-01"),
        S(at=20, op="inject", kind="service_errors", target="web", magnitude=0.04, duration_s=10, label="10 s error blip on web"),
        S(at=40, op="inject", kind="packet_loss", target="uplink-2", magnitude=2, duration_s=12, label="12 s loss blip on uplink-2"),
        S(at=70, op="inject", kind="disk_fill", target="db-01", label="Real problem: db-01 disk filling"),
    ]),
]


# SLA scenarios from the brief: each one is triggered here and measured by the SaaS (SLA tab).
SLA_STORIES: list[dict] = [
    S(id="sla_offline", sla=True, title="Connection lost -> offline <=60 s, resync 0 loss", minutes=3,
      blurb="The site loses the SaaS for 90 s: heartbeats stop, metrics go STALE, the collector is marked Offline "
            "(<=60 s), then the outbox drains live-first at 50/s with 0 loss and 0 duplicates.", steps=[
        S(at=0, op="outage", seconds=90, label="SaaS unreachable for 90 s (heartbeats stop)"),
        S(at=35, op="note", label="Sources should show STALE on the SaaS dashboard"),
        S(at=62, op="note", label="Collector should be OFFLINE (collector_offline) by now"),
        S(at=95, op="note", label="Link back: live data first, backlog drains at 50/s"),
        S(at=150, op="note", label="Check: 0 gaps, re-sends deduplicated (stored once)"),
    ]),
    S(id="sla_flood", sla=True, title="Alert flood: >100 failed logins/s -> ONE incident", minutes=1,
      blurb="150 failed logins/s from 64 bot IPs for 45 s. The collector aggregates the storm into one login_flood "
            "finding; the SaaS opens ONE incident within 15 s with a reversible rate-limit action awaiting approval.", steps=[
        S(at=0, op="inject", kind="login_flood", magnitude=150, duration_s=45, label="Botnet: 150 failed logins/s"),
        S(at=15, op="note", label="One P1 incident should be open (alert <=15 s)"),
    ]),
    S(id="sla_storm", sla=True, title="Latency storm: ~50 breaches in 2 min -> one root cause", minutes=3,
      blurb="Both uplinks degrade and services behind them error: dozens of threshold breaches. Correlation must "
            "collapse them into few incidents, each with a single root cause and blame shares.", steps=[
        S(at=0, op="inject", kind="latency_ramp", target="uplink-1", label="uplink-1 latency ramp"),
        S(at=5, op="inject", kind="packet_loss", target="uplink-1", magnitude=8, duration_s=150, label="uplink-1 loss"),
        S(at=10, op="inject", kind="latency_ramp", target="uplink-2", magnitude=6, label="uplink-2 latency ramp"),
        S(at=20, op="inject", kind="service_errors", target="web", magnitude=0.12, duration_s=120, label="web errors"),
        S(at=25, op="inject", kind="service_errors", target="api", magnitude=0.12, duration_s=120, label="api errors"),
        S(at=120, op="note", label="Compare raw alerts vs incidents (noise reduction)"),
    ]),
    S(id="sla_multi", sla=True, title="Multi-metric anomaly -> blame shares + approval queue", minutes=3,
      blurb="CPU, memory and errors on api-01 at once: |z|>3 for 3 windows across L1/L2/L3, weighted blame per "
            "signal, playbook action queued for human approval. Then ask the AI Investigator.", steps=[
        S(at=0, op="inject", kind="cpu_spike", target="api-01", magnitude=40, duration_s=180, label="api-01 CPU +40"),
        S(at=3, op="inject", kind="mem_leak", target="api-01", magnitude=0.25, duration_s=180, label="api-01 memory leak"),
        S(at=6, op="inject", kind="service_errors", target="api", magnitude=0.08, duration_s=180, label="api errors"),
        S(at=60, op="note", label="Open the incident -> Investigate -> approve the queued action"),
    ]),
]
STORIES.extend(SLA_STORIES)


class StoryRun:
    def __init__(self, story: dict):
        self.story = story
        self.start = time.time()
        self.done: dict[int, dict] = {}
        self.task: asyncio.Task | None = None
        self.finished = False

    def view(self) -> dict:
        now = time.time()
        steps = [{**s, "state": ("done" if i in self.done and self.done[i].get("ok", True) else "error" if i in self.done else
                                 "pending"), "result": self.done.get(i)} for i, s in enumerate(self.story["steps"])]
        return {"id": self.story["id"], "title": self.story["title"], "elapsed_s": round(now - self.start, 1),
                "finished": self.finished, "steps": steps}


class Taps:
    """Rolling per-second view of what the devices are emitting (for live charts + raw feed)."""

    def __init__(self) -> None:
        self.points: deque[dict] = deque(maxlen=900)
        self.feed: deque[dict] = deque(maxlen=250)
        self._ids = itertools.count(1)

    def capture(self, now: float, recs: list[dict], sys_lines: list[str], sent: int) -> None:
        h: dict[str, list] = {}
        links: dict[str, list] = {}
        svcs: dict[str, list] = {}
        for r in recs:
            k = r.get("kind")
            if k == "metric" and "host" in r:
                h.setdefault(r["host"], [None, None, None])
                i = {"cpu": 0, "mem": 1, "disk": 2}.get(r["name"])
                if i is not None:
                    h[r["host"]][i] = r["value"]
            elif k == "probe":
                links[r["link"]] = [r.get("rtt_ms"), r.get("loss"), r.get("ok")]
            elif k == "check":
                svcs.setdefault(r["service"], [None, None, None])
                svcs[r["service"]][1] = r.get("latency_ms")
                svcs[r["service"]][2] = r.get("ok")
            elif k == "metric" and "service" in r and r.get("name") == "error_rate":
                svcs.setdefault(r["service"], [None, None, None])
                svcs[r["service"]][0] = r["value"]
            elif k == "log":
                self.feed.append({"id": next(self._ids), "ts": now, "src": f"{r.get('host')}/{r.get('app')}",
                                  "level": r.get("level", "info"), "text": r.get("msg", "")})
        for line in sys_lines:
            attack = any(ip in line for ip in ("203.0.113.7", "198.51.100.23", "192.0.2.44", "198.18.0."))
            self.feed.append({"id": next(self._ids), "ts": now, "src": "gw-01/sshd", "level": "warning" if attack else "info",
                              "text": line.split(": ", 1)[-1], "tag": "attack" if attack else None})
        self.points.append({"t": round(now, 1), "n": sent, "h": h, "l": links, "s": svcs})


class InjectReq(BaseModel):
    site: str = "acme"
    kind: str
    target: str | None = None
    magnitude: float | None = None
    duration_s: int | None = None


class OutageReq(BaseModel):
    seconds: int = 60


class SaasReq(BaseModel):
    url: str
    email: str | None = None
    password: str | None = None


def build_router(runtimes: dict, stats: dict, taps: dict[str, Taps], do_inject: Callable[..., dict],
                 do_clear: Callable[..., dict], timeline: deque) -> APIRouter:
    r = APIRouter(prefix="/api")
    state: dict[str, Any] = {"story": None, "boundary": {}}
    saas = sla.Saas()

    def log(kind: str, title: str, detail: str = "", ok: bool = True, **extra) -> None:
        timeline.append({"ts": time.time(), "kind": kind, "title": title, "detail": detail, "ok": ok, **extra})

    async def collector(method: str, path: str, **kw) -> dict:
        try:
            async with httpx.AsyncClient(base_url=COLLECTOR_LOCAL, timeout=5) as c:
                resp = await c.request(method, path, **kw)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"collector local API unreachable at {COLLECTOR_LOCAL}: {type(e).__name__}") from e

    async def leak() -> dict:
        res = await collector("POST", "/local/debug/leak-field")
        log("collector", "Leak bug armed", "next record carries raw_msg - egress policy must block it")
        return res

    async def outage(seconds: int) -> dict:
        res = await collector("POST", "/local/debug/outage", params={"seconds": seconds})
        log("collector", f"SaaS outage {seconds} s", "collector buffers, then drains oldest-first")
        return res

    async def run_boundary(test: str) -> dict:
        res = await asyncio.to_thread(boundary.run, test)
        state["boundary"][test] = res
        verdict = "refused as designed" if res["passed"] and test != "replay" else "deduplicated" if res["passed"] else "UNEXPECTED"
        log("boundary", f"{res['title']}: {verdict}", f"{res['status'] or 'no HTTP'} - {res['detail'][:140]}", ok=res["passed"], test=test)
        return res

    async def step(s: dict) -> dict:
        op = s["op"]
        if op == "inject":
            return do_inject(site="acme", kind=s["kind"], target=s.get("target"), magnitude=s.get("magnitude"),
                             duration_s=s.get("duration_s"), source="story")
        if op == "leak":
            return await leak()
        if op == "outage":
            return await outage(s.get("seconds", 60))
        if op == "boundary":
            return await run_boundary(s["test"])
        log("story", s["label"])
        return {"ok": True}

    async def runner(run: StoryRun) -> None:
        log("story", f"Story started: {run.story['title']}")
        for i, s in enumerate(run.story["steps"]):
            wait = run.start + s["at"] - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                res = await step(s)
                run.done[i] = {"ok": bool(res.get("ok", res.get("passed", True))), **{k: v for k, v in res.items() if k in ("detail", "status", "passed", "id")}}
            except Exception as e:  # noqa: BLE001
                run.done[i] = {"ok": False, "detail": str(getattr(e, "detail", e))[:200]}
        run.finished = True
        log("story", f"Story finished: {run.story['title']}")

    # ------------------------------------------------------------ read
    @r.get("/catalogue")
    def catalogue():
        site = next(iter(runtimes.values())).site
        items = []
        for kind, (ttype, mag, dur, what) in CATALOGUE.items():
            cat, title, expect = META.get(kind, ("Other", kind, what))
            targets = {"host": list(site.hosts), "link": list(site.links), "svc": list(site.services)}.get(ttype or "", [])
            if kind == "secret_in_log":
                targets = [s[0] for s in SECRETS]
            items.append({"kind": kind, "source": "simulator", "category": cat, "title": title, "what": what, "expect": expect,
                          "target_type": ttype or ("rule" if kind == "secret_in_log" else None), "targets": targets,
                          "magnitude": mag, "duration_s": dur})
        for kind, (cat, title, expect) in EXTRA.items():
            items.append({"kind": kind, "source": "collector", "category": cat, "title": title, "what": expect, "expect": expect,
                          "target_type": None, "targets": [], "magnitude": None, "duration_s": 60 if kind == "outage" else None})
        return {
            "sites": {n: {"hosts": rt.site.hosts, "services": rt.site.services, "links": rt.site.links,
                          "service_links": rt.site.service_links, "attacker_ips": rt.site.attacker_ips, "tz": rt.site.tz}
                      for n, rt in runtimes.items()},
            "scenarios": items,
            "boundary": [{"test": k, "title": t, "what": w, "expect": e} for k, (t, w, e) in boundary.TESTS.items()],
            "stories": [{k: v for k, v in s.items()} for s in STORIES],
            "ingest_base": boundary.INGEST_BASE, "collector_local": COLLECTOR_LOCAL,
        }

    @r.get("/state")
    def get_state():
        now = time.time()
        run: StoryRun | None = state["story"]
        return {
            "now": now,
            "sites": {n: {"faults": rt.book.summary(now), "failed_over": sorted(rt.book.failed_over),
                          "blocked_ips": sorted(rt.book.blocked_ips), **stats[n]} for n, rt in runtimes.items()},
            "story": run.view() if run else None,
            "timeline": list(timeline)[-120:][::-1],
            "boundary": state["boundary"],
        }

    @r.get("/live")
    def live(site: str = "acme", since: float = 0):
        t = taps.get(site)
        if not t:
            raise HTTPException(404, "unknown site")
        pts = [p for p in t.points if p["t"] > since] if since else list(t.points)[-300:]
        return {"now": time.time(), "points": pts}

    @r.get("/feed")
    def feed(site: str = "acme", after: int = 0):
        t = taps.get(site)
        return [x for x in t.feed if x["id"] > after][-120:] if t else []

    @r.get("/collector")
    async def collector_status():
        try:
            s = await collector("GET", "/local/status")
        except HTTPException as e:
            return {"reachable": False, "error": e.detail}
        c = s.get("counters", {})
        keep = ("raw_in", "redactions_total", "identities_coded", "payloads_sealed", "blocked_total", "acked",
                "dup_source_events", "late_revisions", "findings", "send_failures", "duplicates_acked",
                "findings_aggregated", "flood_ips")
        return {"reachable": True, "collector_id": s.get("collector_id"), "ingest_base": s.get("ingest_base"),
                "saas": s.get("saas"), "outbox": s.get("outbox"), "counters": {k: c.get(k, 0) for k in keep},
                "outage_until": s.get("outage_until"), "leak_field_armed": s.get("leak_field_armed")}

    @r.get("/sla")
    async def sla_status(minutes: int = 15):
        if not saas.url:
            return {"configured": False, "targets": sla.TARGETS}
        try:
            d = await saas.get({"slo": f"/api/slo?minutes={minutes}", "fleet": "/api/fleet",
                                "delivery": "/api/delivery", "incidents": "/api/incidents", "sources": "/api/sources"})
        except Exception as e:  # noqa: BLE001
            saas.last_error = f"{type(e).__name__}: {e}"[:300]
            return {"configured": True, "url": saas.url, "email": saas.email, "reachable": False,
                    "error": saas.last_error, "targets": sla.TARGETS}
        try:
            col = await collector("GET", "/local/status")
        except HTTPException:
            col = None
        return {"configured": True, "url": saas.url, "email": saas.email, "reachable": True, "targets": sla.TARGETS,
                "at": time.time(), **sla.evaluate(d, col, minutes)}

    @r.post("/saas")
    def saas_config(req: SaasReq):
        saas.configure(req.url, req.email, req.password)
        log("sla", "SaaS link set", saas.url)
        return {"ok": True, "url": saas.url, "email": saas.email}

    # ------------------------------------------------------------ act
    @r.post("/inject")
    async def inject(req: InjectReq):
        if req.kind == "leak_field":
            return await leak()
        if req.kind == "outage":
            return await outage(req.duration_s or 60)
        if req.kind not in CATALOGUE:
            raise HTTPException(400, "unknown scenario")
        return do_inject(site=req.site, kind=req.kind, target=req.target, magnitude=req.magnitude,
                         duration_s=req.duration_s, source="dashboard")

    @r.post("/clear")
    def clear(site: str = "acme", kind: str | None = None, target: str | None = None):
        return do_clear(site=site, kind=kind, target=target)

    @r.post("/boundary/{test}")
    async def boundary_test(test: str):
        if test not in boundary.TESTS:
            raise HTTPException(404, "unknown test")
        return await run_boundary(test)

    @r.post("/stories/{sid}/start")
    async def story_start(sid: str):
        story = next((s for s in STORIES if s["id"] == sid), None)
        if not story:
            raise HTTPException(404, "unknown story")
        cur: StoryRun | None = state["story"]
        if cur and cur.task and not cur.task.done():
            cur.task.cancel()
        run = StoryRun(story)
        run.task = asyncio.create_task(runner(run))
        state["story"] = run
        return run.view()

    @r.post("/stories/stop")
    async def story_stop():
        cur: StoryRun | None = state["story"]
        if cur and cur.task and not cur.task.done():
            cur.task.cancel()
            cur.finished = True
            log("story", f"Story stopped: {cur.story['title']}")
        return {"ok": True}

    return r
