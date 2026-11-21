"""Global dashboard API + WebSocket fan-out. Stateless; reads each tenant from its home region."""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import orjson
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel
from tl_common.config import home_region, iso, load_yaml, now, parse_ts, tenants_file
from tl_common.db import tenant_tx

from . import investigator
from .auth import User, admin_user, current_user, decode, login, tenant_user
from .regions import REGIONS

log = logging.getLogger("api")
SIM_URL = os.environ.get("SIMULATOR_URL", "http://simulator:8090")
CLIENT_STALL_S = 30  # push -> paint longer than this is a frozen or hidden tab, not the pipeline
SLO_TARGET_MS = {"dash": 10000, "alert": 15000, "offline": 60000}  # the brief: p95 <= 10 s, alerts <= 15 s, offline <= 60 s


# ====================================================================== live fan-out
class Hub:
    def __init__(self):
        self.clients: set["Client"] = set()
        self.timing: OrderedDict[str, dict] = OrderedDict()  # envelope id -> timing (for render acks)

    def remember(self, tenant: str, kind: str, env: dict) -> None:
        if not env.get("id") or kind not in ("metric", "incident", "finding", "health"):
            return
        key = f"{tenant}|{env['id']}"
        self.timing[key] = {"kind": kind, **(env.get("timing") or {})}
        self.timing.move_to_end(key)
        while len(self.timing) > 5000:
            self.timing.popitem(last=False)

    async def on_live(self, region: str, msg) -> None:
        try:
            _, tenant, kind = msg.subject.split(".", 2)
        except ValueError:
            return
        env = orjson.loads(msg.data)
        self.remember(tenant, kind, env)
        for c in list(self.clients):
            if c.user.is_ops:
                if kind in ("collector", "delivery"):
                    c.send(msg.data)
            elif c.user.tenant == tenant:
                c.send(msg.data)


HUB = Hub()


class Client:
    def __init__(self, ws: WebSocket, user: User):
        self.ws, self.user = ws, user
        self.q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2000)
        self.offset_ms = 0.0  # server_now - client_now, from ping/pong

    def send(self, data: bytes) -> None:
        try:
            self.q.put_nowait(data)
        except asyncio.QueueFull:  # slow client: drop oldest
            try:
                self.q.get_nowait()
                self.q.put_nowait(data)
            except Exception:
                pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    task = asyncio.create_task(REGIONS.connect(HUB.on_live))
    yield
    task.cancel()
    await REGIONS.close()


app = FastAPI(title="TrustLine API", lifespan=lifespan, default_response_class=ORJSONResponse,
              docs_url="/api/docs", openapi_url="/api/openapi.json")


def tx(user: User):
    return tenant_tx(REGIONS.pool_for(user.tenant), user.tenant)


def rows(rs) -> list[dict]:
    return [dict(r) for r in rs]


def window(from_: str | None, to: str | None, default_min: int = 15) -> tuple[datetime, datetime]:
    t1 = parse_ts(to) or now()
    t0 = parse_ts(from_) or (t1 - timedelta(minutes=default_min))
    return t0, t1


# ====================================================================== auth
class LoginReq(BaseModel):
    email: str
    password: str


@app.post("/api/auth/login")
def do_login(req: LoginReq):
    tok, u = login(req.email, req.password)
    return {"token": tok, "user": {"email": u.email, "tenant": u.tenant, "role": u.role,
                                   "region": home_region(u.tenant) if u.tenant != "*" else None}}


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    t = (tenants_file().get("tenants") or {}).get(user.tenant, {})
    return {"email": user.email, "tenant": user.tenant, "role": user.role, "tenant_name": t.get("name"),
            "region": t.get("home_region"), "regions_connected": sorted(REGIONS.pools)}


@app.get("/api/healthz")
def healthz():
    return {"ok": True, "regions": sorted(REGIONS.pools), "nats": sorted(REGIONS.nats), "ws_clients": len(HUB.clients)}


# ====================================================================== overview
@app.get("/api/overview")
async def overview(user: User = Depends(tenant_user)):
    async with tx(user) as c:
        states = rows(await c.fetch("SELECT state, kind, count(*) n FROM health_states GROUP BY state, kind"))
        incidents = rows(await c.fetch(
            "SELECT incident_id, status, priority, severity, title, root_cause_src, alert_count, opened_ts,"
            " (evidence->>'confidence')::float AS confidence FROM incidents WHERE status <> 'resolved'"
            " ORDER BY priority, opened_ts DESC LIMIT 20"))
        alerts = await c.fetchval("SELECT count(*) FROM alerts WHERE onset_ts > now() - interval '24 hours'")
        inc24 = await c.fetchval("SELECT count(*) FROM incidents WHERE opened_ts > now() - interval '24 hours'")
        red = await c.fetchrow(
            "SELECT COALESCE(sum(redactions),0) total, count(*) payloads, COALESCE(sum(records),0) records"
            " FROM received_payloads WHERE received_at > now() - interval '24 hours'")
        by_rule = rows(await c.fetch(
            "SELECT k AS rule, sum(v::int) n FROM received_payloads, jsonb_each_text(redactions_by_rule) AS j(k, v)"
            " WHERE received_at > now() - interval '24 hours' GROUP BY k ORDER BY n DESC"))
        slo = rows(await c.fetch(
            "SELECT kind, count(*) n, percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) p50,"
            " percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) p95, max(total_ms) mx FROM latency_samples"
            " WHERE at > now() - interval '15 minutes' AND excluded IS NULL GROUP BY kind"))
        fleet = rows(await c.fetch("SELECT collector_id, state, buffer_depth, last_hb_event_ts FROM collectors"))
        findings = await c.fetchval("SELECT count(*) FROM findings WHERE event_ts > now() - interval '24 hours'")
        ev24 = await c.fetchrow("SELECT COALESCE(sum(count),0) lines, count(DISTINCT template_id) templates FROM events"
                                " WHERE event_ts > now() - interval '24 hours'")
        blocked = await c.fetchval("SELECT COALESCE(sum(blocked_total),0) FROM collectors")
    counts = {s: 0 for s in ("excellent", "good", "fair", "poor")}
    for r in states:
        counts[r["state"]] += r["n"]
    return {"states": counts, "states_by_kind": states, "incidents": incidents,
            "noise": {"raw_alerts_24h": alerts, "incidents_24h": inc24,
                      "reduction": round(1 - inc24 / alerts, 3) if alerts else 0.0},
            "privacy": {"redactions_24h": red["total"], "payloads_24h": red["payloads"],
                        "records_24h": red["records"], "by_rule": by_rule, "lines_24h": ev24["lines"],
                        "templates_24h": ev24["templates"], "blocked_total": blocked},
            "slo": {r["kind"]: {"n": r["n"], "p50": r["p50"], "p95": r["p95"], "max": r["mx"],
                                "target": SLO_TARGET_MS.get(r["kind"])} for r in slo},
            "fleet": fleet, "findings_24h": findings}


@app.get("/api/series/overview")
async def overview_series(user: User = Depends(tenant_user), minutes: int = 60):
    """Small time series for KPI sparklines and the overview charts (bucketed per minute)."""
    t0 = now() - timedelta(minutes=minutes)
    step = timedelta(seconds=max(10, minutes))
    async with tx(user) as c:
        red = rows(await c.fetch(
            "SELECT date_bin($2, received_at, $1) t, k AS rule, sum(v::int) n FROM received_payloads,"
            " jsonb_each_text(redactions_by_rule) AS j(k, v) WHERE received_at > $1 GROUP BY 1, 2 ORDER BY 1",
            t0, step))
        sec = rows(await c.fetch(
            "SELECT date_bin($2, event_ts, $1) t, severity, count(*) n FROM findings WHERE event_ts > $1"
            " GROUP BY 1, 2 ORDER BY 1", t0, step))
        ev = rows(await c.fetch(
            "SELECT date_bin($2, event_ts, $1) t, level, sum(count) n FROM events WHERE event_ts > $1"
            " GROUP BY 1, 2 ORDER BY 1", t0, step))
        pay = rows(await c.fetch(
            "SELECT date_bin($2, received_at, $1) t, count(*) payloads, sum(records) records FROM received_payloads"
            " WHERE received_at > $1 GROUP BY 1 ORDER BY 1", t0, step))
        lat = rows(await c.fetch(
            "SELECT date_bin($2, at, $1) t, kind, percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) p95"
            " FROM latency_samples WHERE at > $1 AND excluded IS NULL GROUP BY 1, 2 ORDER BY 1", t0, step))
        top = rows(await c.fetch(
            "SELECT DISTINCT ON (src, metric) src, metric, p95, avg FROM metrics WHERE event_ts > now() - interval"
            " '1 minute' AND metric IN ('cpu','mem','disk','rtt','error_rate') ORDER BY src, metric, event_ts DESC"))
        disk = rows(await c.fetch(
            "SELECT src, avg FROM (SELECT DISTINCT ON (src) src, avg FROM metrics WHERE metric='disk_hours_to_full'"
            " AND event_ts > now() - interval '5 minutes' ORDER BY src, event_ts DESC) d ORDER BY avg LIMIT 5"))
    return {"step_s": int(step.total_seconds()), "redactions": red, "security": sec, "events": ev,
            "payloads": pay, "latency": lat, "latest": top, "disk_forecast": disk}


# ====================================================================== sources / metrics
@app.get("/api/sources")
async def sources(user: User = Depends(tenant_user), state: str | None = None, kind: str | None = None,
                  role: str | None = None, collector: str | None = None):
    q = "SELECT * FROM health_states WHERE true"
    args: list = []
    for col, val in (("state", state), ("kind", kind), ("role", role), ("collector_id", collector)):
        if val:
            args.append(val)
            q += f" AND {col} = ${len(args)}"
    async with tx(user) as c:
        return {"sources": rows(await c.fetch(q + " ORDER BY src", *args))}


@app.get("/api/sources/{src}")
async def source(src: str, user: User = Depends(tenant_user)):
    async with tx(user) as c:
        h = await c.fetchrow("SELECT * FROM health_states WHERE src=$1", src)
        if not h:
            raise HTTPException(404, "unknown source")
        tr = rows(await c.fetch("SELECT old_state, new_state, reason, at FROM health_transitions WHERE src=$1"
                                " ORDER BY at DESC LIMIT 50", src))
        latest = rows(await c.fetch(
            "SELECT DISTINCT ON (metric) metric, event_ts, avg, p95, max, unit FROM metrics WHERE src=$1 AND"
            " event_ts > now() - interval '10 minutes' ORDER BY metric, event_ts DESC", src))
        inc = rows(await c.fetch("SELECT incident_id, title, priority, status FROM incidents WHERE root_cause_src=$1"
                                 " OR affected ? $1 ORDER BY opened_ts DESC LIMIT 10", src))
    return {"health": dict(h), "transitions": tr, "latest": latest, "incidents": inc}


@app.get("/api/metrics/query")
async def metric_query(user: User = Depends(tenant_user), src: str = Query(...), metric: str = Query(...),
                       stat: str = "avg", from_: str | None = Query(None, alias="from"), to: str | None = None,
                       step: int = 5):
    if stat not in ("min", "avg", "p50", "p95", "max"):
        raise HTTPException(400, "bad stat")
    t0, t1 = window(from_, to)
    step = max(5, step)
    async with tx(user) as c:
        pts = rows(await c.fetch(
            f"SELECT date_bin($4, event_ts, $2) t, avg({stat}) v, max(rev) rev FROM metrics WHERE src=$1 AND"
            f" metric=$5 AND event_ts BETWEEN $2 AND $3 GROUP BY 1 ORDER BY 1",
            src, t0, t1, timedelta(seconds=step), metric))
        bl = await c.fetchrow("SELECT state FROM baselines WHERE key=$1", f"{src}|{metric}")
        al = rows(await c.fetch("SELECT onset_ts, z, incident_id FROM alerts WHERE src=$1 AND metric=$2 AND"
                                " onset_ts BETWEEN $3 AND $4", src, metric, t0, t1))
    band = None
    if bl:
        import numpy as np
        arr = np.array(bl["state"].get("ring") or [], float)
        if arr.size >= 10:
            med = float(np.median(arr))
            mad = float(np.median(np.abs(arr - med)))
            w = 3 * (1.4826 * mad + max(0.01, abs(med) * 0.01))
            band = {"median": med, "lo": med - w, "hi": med + w}
    th = (load_yaml("thresholds.yaml").get("signals") or {}).get(metric)
    return {"src": src, "metric": metric, "stat": stat, "points": pts, "band": band, "thresholds": th,
            "alerts": al}


@app.get("/api/topology")
async def topology(user: User = Depends(tenant_user)):
    async with tx(user) as c:
        t = await c.fetchrow("SELECT nodes, edges, updated_ts FROM topology")
        hs = {r["src"]: r["state"] for r in await c.fetch("SELECT src, state FROM health_states")}
    if not t:
        return {"nodes": [], "edges": []}
    nodes = [{**n, "state": hs.get(n["src"], "excellent")} for n in t["nodes"]]
    return {"nodes": nodes, "edges": t["edges"], "updated_ts": t["updated_ts"]}


# ====================================================================== events / security
@app.get("/api/events")
async def events(user: User = Depends(tenant_user), q: str | None = None, level: str | None = None,
                 category: str | None = None, src: str | None = None, from_: str | None = Query(None, alias="from"),
                 to: str | None = None, limit: int = 200):
    t0, t1 = window(from_, to, 60)
    sql, args = "SELECT * FROM events WHERE event_ts BETWEEN $1 AND $2", [t0, t1]
    for col, val in (("level", level), ("category", category), ("src", src)):
        if val:
            args.append(val)
            sql += f" AND {col} = ${len(args)}"
    if q:
        args.append(f"%{q}%")
        sql += f" AND template ILIKE ${len(args)}"
    args.append(min(limit, 1000))
    async with tx(user) as c:
        return {"events": rows(await c.fetch(sql + f" ORDER BY event_ts DESC LIMIT ${len(args)}", *args))}


@app.get("/api/events/facets")
async def facets(user: User = Depends(tenant_user)):
    async with tx(user) as c:
        out = {}
        for col in ("level", "category", "src"):
            out[col] = rows(await c.fetch(f"SELECT {col} AS value, sum(count) n FROM events WHERE event_ts > now() -"
                                          f" interval '1 hour' GROUP BY 1 ORDER BY 2 DESC LIMIT 20"))
        return out


@app.get("/api/security/findings")
async def findings(user: User = Depends(tenant_user), category: str | None = None, limit: int = 200):
    sql, args = "SELECT * FROM findings WHERE event_ts > now() - interval '24 hours'", []
    if category:
        args.append(category)
        sql += " AND category = $1"
    async with tx(user) as c:
        return {"findings": rows(await c.fetch(sql + f" ORDER BY event_ts DESC LIMIT {min(limit, 1000)}", *args))}


# ====================================================================== incidents + remediation
@app.get("/api/incidents")
async def incidents(user: User = Depends(tenant_user), status: str | None = None):
    sql = ("SELECT incident_id, status, priority, severity, title, root_cause_src, affected, alert_count, breach_ts,"
           " opened_ts, alert_ts, resolved_ts, (evidence->>'confidence')::float confidence, evidence->>'rule' rule"
           " FROM incidents")
    args = []
    if status:
        sql += " WHERE status = $1"
        args.append(status)
    async with tx(user) as c:
        return {"incidents": rows(await c.fetch(sql + " ORDER BY opened_ts DESC LIMIT 200", *args))}


@app.get("/api/incidents/{iid}")
async def incident(iid: str, user: User = Depends(tenant_user)):
    async with tx(user) as c:
        i = await c.fetchrow("SELECT * FROM incidents WHERE incident_id=$1", iid)
        if not i:
            raise HTTPException(404, "no such incident")
        al = rows(await c.fetch("SELECT * FROM alerts WHERE incident_id=$1 ORDER BY onset_ts", iid))
        rem = rows(await c.fetch("SELECT * FROM remediations WHERE incident_id=$1 ORDER BY proposed_ts", iid))
        aud = rows(await c.fetch("SELECT actor, role, action, detail, at FROM audit_log WHERE target=$1"
                                 " ORDER BY at", iid))
    return {"incident": dict(i), "alerts": al, "remediations": rem, "audit": aud}


async def audit(c, user: User | str, action: str, target: str | None, detail: dict) -> None:
    actor, role = (user.email, user.role) if isinstance(user, User) else (user, "system")
    await c.execute("INSERT INTO audit_log(tenant_id, actor, role, action, target, detail) VALUES"
                    " (current_setting('app.tenant_id'), $1, $2, $3, $4, $5)", actor, role, action, target, detail)


# Explicit paths: a generic /{op} route would shadow /investigate and /remediations below.
@app.post("/api/incidents/{iid}/ack")
async def incident_ack(iid: str, user: User = Depends(tenant_user)):
    return await incident_op(iid, "ack", user)


@app.post("/api/incidents/{iid}/resolve")
async def incident_resolve(iid: str, user: User = Depends(tenant_user)):
    return await incident_op(iid, "resolve", user)


async def incident_op(iid: str, op: str, user: User):
    if op == "resolve" and user.role != "tenant-admin":
        raise HTTPException(403, "tenant-admin role required")
    status = "acknowledged" if op == "ack" else "resolved"
    async with tx(user) as c:
        r = await c.execute("UPDATE incidents SET status=$2, updated_ts=now(),"
                            " resolved_ts=CASE WHEN $2='resolved' THEN now() ELSE resolved_ts END"
                            " WHERE incident_id=$1", iid, status)
        if r.endswith(" 0"):
            raise HTTPException(404)
        await audit(c, user, f"incident.{op}", iid, {})
    return {"incident_id": iid, "status": status}


ROLE_RANK = {"tenant-viewer": 0, "tenant-admin": 1}


@app.get("/api/incidents/{iid}/investigation")
async def investigation(iid: str, user: User = Depends(tenant_user)):
    return {"investigation": investigator.CACHE.get((user.tenant, iid)),
            "engine_available": investigator.engine_available()}


@app.post("/api/incidents/{iid}/investigate")
async def investigate(iid: str, user: User = Depends(tenant_user)):
    """Run the AI Investigator: read-only tools over metadata -> grounded diagnosis -> at most one
    playbook action queued as `proposed` (a tenant-admin still has to approve it)."""
    async with tx(user) as c:
        if not await c.fetchval("SELECT 1 FROM incidents WHERE incident_id=$1", iid):
            raise HTTPException(404, "no such incident")

        async def enqueue(incident_id: str, by: str):
            pending = await c.fetchval("SELECT action_id FROM remediations WHERE incident_id=$1 AND state='proposed'",
                                       incident_id)
            if pending:
                return {"action_id": str(pending), "state": "proposed", "note": "already awaiting approval"}
            try:
                return await _propose(c, user, incident_id, by)
            except HTTPException as e:
                return {"action_id": None, "state": "not_queued", "note": e.detail}

        out = await investigator.investigate(c, user.tenant, iid, enqueue)
        d = out["diagnosis"]
        await audit(c, user, "investigation.completed", iid, {
            "engine": out["engine"], "model": out["model"], "root_cause": d.get("root_cause"),
            "recommended_action": d.get("recommended_action"), "confidence": d.get("confidence"),
            "tools": [t["tool"] for t in out["trace"] if t.get("step") == "tool"],
            "queued": out["queued"], "policy_note": out["policy_note"], "fallback_reason": out["fallback_reason"]})
    return out


@app.post("/api/incidents/{iid}/remediations")
async def propose(iid: str, user: User = Depends(tenant_user)):
    """Propose the playbook action. The policy check runs here and is stored with the proposal."""
    async with tx(user) as c:
        return await _propose(c, user, iid, user.email)


async def _propose(c, user: User, iid: str, proposed_by: str) -> dict:
    i = await c.fetchrow("SELECT evidence, root_cause_src, status FROM incidents WHERE incident_id=$1", iid)
    if not i:
        raise HTTPException(404)
    ev = i["evidence"] or {}
    act = ev.get("action") or {}
    if not act.get("executable"):
        raise HTTPException(409, "this playbook rule has no executable action (investigate manually)")
    coll = await c.fetchval("SELECT collector_id FROM health_states WHERE src=$1", i["root_cause_src"])
    checks = [
        {"check": "reversible", "ok": bool(act.get("reversible")) or act.get("key") == "restart_service",
         "detail": f"rollback: {act.get('rollback_key') or 'none'}"},
        {"check": "blast_radius", "ok": (ev.get("blast_radius") or 1) <= (act.get("max_blast_radius") or 99),
         "detail": f"{ev.get('blast_radius')} <= {act.get('max_blast_radius')}"},
        {"check": "approver_role", "ok": True, "detail": f"requires {act.get('requires_role')} to approve"},
        {"check": "collector_known", "ok": coll is not None, "detail": coll or "no collector owns this source"},
        {"check": "local_allowlist", "ok": True, "detail": "enforced again on site by the collector"},
    ]
    ok = all(x["ok"] for x in checks)
    aid = uuid.uuid4()
    await c.execute(
        "INSERT INTO remediations(tenant_id, action_id, incident_id, action_key, params, policy, state,"
        " collector_id, proposed_by) VALUES (current_setting('app.tenant_id'),$1,$2,$3,$4,$5,$6,$7,$8)",
        aid, iid, act["key"], {"target": i["root_cause_src"]}, {"checks": checks, "ok": ok},
        "proposed" if ok else "refused", coll, proposed_by)
    await audit(c, user, "remediation.proposed" if ok else "remediation.refused_policy", iid,
                {"action_id": str(aid), "action_key": act["key"], "checks": checks, "proposed_by": proposed_by,
                 "rollback": act.get("rollback_key") or "none"})
    return {"action_id": str(aid), "state": "proposed" if ok else "refused", "checks": checks}


@app.post("/api/remediations/{aid}/{op}")
async def remediation_op(aid: uuid.UUID, op: str, user: User = Depends(admin_user)):
    async with tx(user) as c:
        r = await c.fetchrow("SELECT * FROM remediations WHERE action_id=$1", aid)
        if not r:
            raise HTTPException(404)
        if op == "approve":
            if r["state"] != "proposed":
                raise HTTPException(409, f"cannot approve from state {r['state']}")
            await c.execute("UPDATE remediations SET state='approved', approved_by=$2, approved_ts=now()"
                            " WHERE action_id=$1", aid, user.email)
            state = "approved"
        elif op == "reject":
            if r["state"] != "proposed":
                raise HTTPException(409, f"cannot reject from state {r['state']}")
            await c.execute("UPDATE remediations SET state='rejected' WHERE action_id=$1", aid)
            state = "rejected"
        elif op == "rollback":
            if r["state"] not in ("executed", "verified", "failed"):
                raise HTTPException(409, f"cannot roll back from state {r['state']}")
            ev = await c.fetchval("SELECT evidence FROM incidents WHERE incident_id=$1", r["incident_id"]) or {}
            rk = (ev.get("action") or {}).get("rollback_key")
            if not rk or rk == "none":
                raise HTTPException(409, "action is not reversible")
            nid = uuid.uuid4()
            await c.execute(
                "INSERT INTO remediations(tenant_id, action_id, incident_id, action_key, params, policy, state,"
                " collector_id, proposed_by, approved_by, approved_ts, rollback_of) VALUES"
                " (current_setting('app.tenant_id'),$1,$2,$3,$4,'{}','approved',$5,$6,$6,now(),$7)",
                nid, r["incident_id"], rk, r["params"], r["collector_id"], user.email, aid)
            await c.execute("UPDATE remediations SET state='rolled_back' WHERE action_id=$1", aid)
            state = "rolled_back"
            await audit(c, user, "remediation.rollback", r["incident_id"],
                        {"action_id": str(aid), "rollback_action_id": str(nid), "action_key": rk})
            return {"action_id": str(aid), "state": state, "rollback_action_id": str(nid)}
        else:
            raise HTTPException(404)
        await audit(c, user, f"remediation.{state}", r["incident_id"], {"action_id": str(aid),
                                                                        "action_key": r["action_key"]})
    return {"action_id": str(aid), "state": state}


# ====================================================================== fleet / delivery / trust
@app.get("/api/fleet")
async def fleet(user: User = Depends(current_user)):
    out = []
    if user.is_ops:
        for region, pool in REGIONS.pools.items():
            async with pool.acquire() as c:
                for r in await c.fetch("SELECT * FROM fleet_overview()"):
                    d = dict(r)
                    out.append({k: d.get(k) for k in (
                        "tenant_id", "collector_id", "region", "state", "state_reason", "last_hb_event_ts", "version",
                        "config_hash", "expected_config_hash", "cpu", "mem_mb", "buffer_depth", "buffer_bytes",
                        "last_acked_seq", "last_assigned_seq", "skew_ms", "cert_serial")})
        return {"collectors": out, "scope": "all tenants (health metadata only)"}
    async with tx(user) as c:
        return {"collectors": rows(await c.fetch("SELECT * FROM collectors ORDER BY collector_id")),
                "scope": user.tenant}


@app.get("/api/delivery")
async def delivery(user: User = Depends(tenant_user)):
    async with tx(user) as c:
        led = rows(await c.fetch(
            "SELECT p.collector_id, min(seq) min_seq, max(seq) max_seq, count(*) received,"
            " max(seq) - min(seq) + 1 - count(DISTINCT seq) gaps, max(received_at) last_received"
            " FROM received_payloads p GROUP BY p.collector_id"))
        cnt = {r["collector_id"]: dict(r) for r in await c.fetch("SELECT * FROM delivery_counters")}
        col = {r["collector_id"]: dict(r) for r in await c.fetch(
            "SELECT collector_id, last_assigned_seq, last_acked_seq, buffer_depth FROM collectors")}
        recent = rows(await c.fetch(
            "SELECT payload_id, seq, result, lane, attempt, received_at, skew_ms FROM ingest_audit"
            " ORDER BY received_at DESC LIMIT 50"))
    for r in led:
        r.update({k: v for k, v in cnt.get(r["collector_id"], {}).items() if k != "tenant_id"})
        r.update({k: v for k, v in col.get(r["collector_id"], {}).items() if k != "collector_id"})
    return {"ledger": led, "recent": recent}


@app.get("/api/trust/audit")
async def trust_audit(user: User = Depends(tenant_user), payload_id: uuid.UUID = Query(...)):
    async with tx(user) as c:
        a = rows(await c.fetch("SELECT * FROM ingest_audit WHERE payload_id=$1 ORDER BY received_at", payload_id))
        p = await c.fetchrow("SELECT * FROM received_payloads WHERE payload_id=$1", payload_id)
        m = await c.fetchval("SELECT count(*) FROM metrics WHERE payload_id=$1", payload_id)
    return {"payload_id": str(payload_id), "attempts": a, "stored": dict(p) if p else None, "metric_rows": m,
            "home_region": home_region(user.tenant)}


ISOLATION_SQL = "SELECT tenant_id, count(*) FROM metrics WHERE tenant_id <> current_setting('app.tenant_id') GROUP BY 1"


@app.post("/api/trust/isolation-test")
async def isolation_test(user: User = Depends(tenant_user)):
    """Deliberately asks for OTHER tenants' rows, as this user's DB role. RLS must return 0."""
    t = time.perf_counter()
    async with tx(user) as c:
        other = rows(await c.fetch(ISOLATION_SQL))
        all_rows = await c.fetchval("SELECT count(*) FROM metrics")
        own = await c.fetchval("SELECT count(*) FROM metrics WHERE tenant_id = $1", user.tenant)
        role = await c.fetchval("SELECT current_user")
        bypass = await c.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        pol = rows(await c.fetch("SELECT tablename, policyname, qual FROM pg_policies WHERE tablename IN"
                                 " ('metrics','incidents','events') ORDER BY 1"))
        forced = await c.fetchval("SELECT relforcerowsecurity FROM pg_class WHERE relname='metrics'")
    async with REGIONS.pool_for(user.tenant).acquire() as c:  # no tenant context at all: fails closed
        unset = await c.fetchval("SELECT count(*) FROM metrics")
    async with tx(user) as c:
        await audit(c, user, "trust.isolation_test", None, {"other_rows": len(other), "unset_rows": unset})
    return {"tenant": user.tenant, "db_role": role, "role_bypasses_rls": bypass, "force_rls": forced,
            "sql": ISOLATION_SQL, "other_tenant_rows": len(other), "rows_visible_total": all_rows,
            "rows_own_tenant": own, "rows_without_tenant_context": unset, "policies": pol,
            "pass": len(other) == 0 and unset == 0 and all_rows == own and not bypass,
            "ms": round((time.perf_counter() - t) * 1000, 1)}


@app.get("/api/trust/residency")
async def residency(user: User = Depends(current_user)):
    cfg = tenants_file()
    out = []
    for region in cfg.get("regions") or {}:
        pool = REGIONS.pools.get(region)
        if not pool:
            out.append({"region": region, "connected": False, "tenants": []})
            continue
        async with pool.acquire() as c:
            counts = rows(await c.fetch("SELECT * FROM residency_counts()"))
        if not user.is_ops:  # a tenant sees its own counts, plus whether others exist here (not who)
            counts = [r if r["tenant_id"] == user.tenant else {"tenant_id": "other", "metrics": None} for r in counts]
        out.append({"region": region, "connected": True, "tenants": counts})
    homes = {t: v["home_region"] for t, v in (cfg.get("tenants") or {}).items()}
    if not user.is_ops:
        homes = {user.tenant: homes.get(user.tenant)}
    return {"regions": out, "home_regions": homes}


@app.get("/api/slo")
async def slo(user: User = Depends(tenant_user), minutes: int = 15):
    async with tx(user) as c:
        agg = rows(await c.fetch(
            "SELECT kind, count(*) n, percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms) p50,"
            " percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms) p95, max(total_ms) mx FROM latency_samples"
            " WHERE at > now() - make_interval(mins => $1) AND excluded IS NULL GROUP BY kind", minutes))
        excl = rows(await c.fetch(
            "SELECT excluded, count(*) n FROM latency_samples WHERE at > now() - make_interval(mins => $1)"
            " AND excluded IS NOT NULL GROUP BY 1", minutes))
        hops = await c.fetchrow(
            "SELECT avg(extract(epoch FROM collect_ts - event_ts))*1000 window_wait,"
            " avg(extract(epoch FROM sent_ts - collect_ts))*1000 seal,"
            " avg(extract(epoch FROM recv_ts - sent_ts))*1000 wire,"
            " avg(extract(epoch FROM stored_ts - recv_ts))*1000 store,"
            " avg(extract(epoch FROM push_ts - stored_ts))*1000 push,"
            " avg(extract(epoch FROM render_ts - push_ts))*1000 render FROM latency_samples"
            " WHERE kind='dash' AND excluded IS NULL AND at > now() - make_interval(mins => $1)", minutes)
        hist = rows(await c.fetch(
            "SELECT kind, width_bucket(total_ms, 0, 12000, 24) b, count(*) n FROM latency_samples"
            " WHERE at > now() - make_interval(mins => $1) AND excluded IS NULL GROUP BY 1, 2 ORDER BY 1, 2", minutes))
        recent = rows(await c.fetch(
            "SELECT kind, total_ms, at FROM latency_samples WHERE excluded IS NULL AND at > now() -"
            " make_interval(mins => $1) ORDER BY at DESC LIMIT 300", minutes))
    return {"targets": SLO_TARGET_MS, "kinds": {r["kind"]: {k: r[k] for k in ("n", "p50", "p95")} | {"max": r["mx"]}
                                                for r in agg},
            "excluded": {r["excluded"]: r["n"] for r in excl}, "hops_ms": dict(hops) if hops else {},
            "histogram": hist, "recent": recent, "bucket_ms": 500}


@app.get("/api/thresholds")
async def get_thresholds(user: User = Depends(tenant_user)):
    base = load_yaml("thresholds.yaml")
    async with tx(user) as c:
        o = await c.fetchrow("SELECT doc, updated_by, updated_ts FROM thresholds_override")
    return {"defaults": base.get("signals"), "file_overrides": (base.get("overrides") or {}).get(user.tenant, {}),
            "tenant_overrides": o["doc"] if o else {}, "updated_by": o["updated_by"] if o else None}


@app.put("/api/thresholds")
async def put_thresholds(doc: dict, user: User = Depends(admin_user)):
    sig = load_yaml("thresholds.yaml").get("signals") or {}
    for metric, vals in doc.items():
        if metric not in sig or not isinstance(vals, dict) or \
                not set(vals) <= {"excellent", "good", "fair"} or not all(isinstance(v, (int, float)) for v in vals.values()):
            raise HTTPException(422, f"bad override for {metric}")
    async with tx(user) as c:
        await c.execute("INSERT INTO thresholds_override(tenant_id, doc, updated_by) VALUES"
                        " (current_setting('app.tenant_id'), $1, $2) ON CONFLICT (tenant_id) DO UPDATE SET"
                        " doc=EXCLUDED.doc, updated_by=EXCLUDED.updated_by, updated_ts=now()", doc, user.email)
        await audit(c, user, "thresholds.update", None, doc)
    return {"ok": True}


@app.get("/api/audit")
async def audit_log(user: User = Depends(tenant_user), limit: int = 200):
    async with tx(user) as c:
        return {"audit": rows(await c.fetch(f"SELECT * FROM audit_log ORDER BY at DESC LIMIT {min(limit, 1000)}"))}


# ====================================================================== demo control (simulator)
def site_of(user: User) -> str:
    return user.tenant


@app.get("/api/sim/scenarios")
async def sim_scenarios(user: User = Depends(tenant_user)):
    async with httpx.AsyncClient(timeout=3) as h:
        try:
            sc = (await h.get(f"{SIM_URL}/scenarios")).json()
            st = (await h.get(f"{SIM_URL}/status", params={"site": site_of(user)})).json()
        except Exception as e:
            raise HTTPException(503, f"simulator unreachable: {e}") from e
    return {"scenarios": sc, "status": st}


class Inject(BaseModel):
    kind: str
    target: str | None = None
    duration_s: int | None = None


@app.post("/api/sim/inject")
async def sim_inject(req: Inject, user: User = Depends(admin_user)):
    async with httpx.AsyncClient(timeout=3) as h:
        r = await h.post(f"{SIM_URL}/inject", json={"site": site_of(user), **req.model_dump(exclude_none=True)})
    async with tx(user) as c:
        await audit(c, user, "demo.inject", req.kind, req.model_dump())
    return r.json()


@app.post("/api/sim/clear")
async def sim_clear(user: User = Depends(admin_user)):
    async with httpx.AsyncClient(timeout=3) as h:
        r = await h.post(f"{SIM_URL}/clear", json={"site": site_of(user)})
    return r.json()


# ====================================================================== websocket
@app.websocket("/ws")
async def ws(sock: WebSocket, token: str = ""):
    try:
        user = decode(token)
    except HTTPException:
        await sock.close(code=4401)
        return
    await sock.accept()
    client = Client(sock, user)
    HUB.clients.add(client)
    try:
        await sock.send_bytes(orjson.dumps({"v": 1, "type": "hello", "tenant": user.tenant,
                                            "data": await snapshot(user), "timing": {"push_ts": iso(now())}},
                                           default=str))
        sender = asyncio.create_task(_pump(client))
        while True:
            msg = orjson.loads(await sock.receive_text())
            typ = msg.get("type")
            if typ == "ping":
                await sock.send_text(orjson.dumps({"v": 1, "type": "pong", "data": {
                    "t0": msg.get("t0"), "server_ts": iso(now())}}).decode())
            elif typ == "offset":
                client.offset_ms = float(msg.get("offset_ms") or 0)
            elif typ == "render_ack" and not user.is_ops:
                await render_ack(client, msg.get("items") or [])
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:
        log.exception("ws error")
    finally:
        HUB.clients.discard(client)
        try:
            sender.cancel()
        except Exception:
            pass


async def _pump(client: Client) -> None:
    while True:
        data = await client.q.get()
        await client.ws.send_text(data.decode() if isinstance(data, bytes) else data)


async def snapshot(user: User) -> dict:
    try:
        if user.is_ops:
            return {"fleet": (await fleet(user))["collectors"]}
        async with tx(user) as c:
            return {"health": rows(await c.fetch("SELECT * FROM health_states")),
                    "incidents": rows(await c.fetch("SELECT incident_id, status, priority, severity, title,"
                                                    " root_cause_src, alert_count, opened_ts FROM incidents"
                                                    " WHERE status <> 'resolved'")),
                    "fleet": rows(await c.fetch("SELECT * FROM collectors"))}
    except HTTPException as e:
        return {"error": e.detail}


# An incident is re-published on every update (alert absorbed, remediation, investigation) with the
# same breach_ts; only its FIRST render is the alert-latency sample, later renders are not "alert raised".
ALERT_RENDERED: "OrderedDict[str, None]" = OrderedDict()


async def render_ack(client: Client, items: list[dict]) -> None:
    """Measured end-to-end latency: start = event/breach timestamp, end = corrected render time."""
    samples = []
    for it in items[:200]:
        key = f"{client.user.tenant}|{it.get('id')}"
        t = HUB.timing.get(key)
        render = parse_ts(it.get("render_ts"))
        if not t or not render:
            continue
        render = render + timedelta(milliseconds=client.offset_ms)
        kind = it.get("kind", "dash")
        if kind == "alert":
            if key in ALERT_RENDERED:
                continue
            ALERT_RENDERED[key] = None
            while len(ALERT_RENDERED) > 20000:
                ALERT_RENDERED.popitem(last=False)
        start = parse_ts(t.get("breach_ts") if kind == "alert" else t.get("event_ts"))
        if not start:
            continue
        excluded = "backlog" if t.get("lane") == "backlog" else ("revision" if t.get("revision") else None)
        push = parse_ts(t.get("push_ts"))
        if not excluded and push and (render - push).total_seconds() > CLIENT_STALL_S:
            excluded = "client_stall"  # browser held it (tab hidden / laptop asleep): not pipeline latency
        samples.append((client.user.tenant, kind, it.get("id"), t.get("lane"), start, parse_ts(t.get("collect_ts")),
                        parse_ts(t.get("sent_ts")), parse_ts(t.get("recv_ts")), parse_ts(t.get("stored_ts")),
                        parse_ts(t.get("push_ts")), render, (render - start).total_seconds() * 1000, excluded))
    if not samples:
        return
    async with tx(client.user) as c:
        await c.executemany(
            "INSERT INTO latency_samples(tenant_id, kind, ref_id, lane, event_ts, collect_ts, sent_ts, recv_ts,"
            " stored_ts, push_ts, render_ts, total_ms, excluded) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
            samples)
    payload = {"v": 1, "type": "latency", "tenant": client.user.tenant, "data": {
        "samples": [{"kind": s[1], "ms": round(s[11]), "excluded": s[12]} for s in samples]}}
    client.send(orjson.dumps(payload))


@app.get("/api/time")
def server_time():
    return {"server_ts": iso(datetime.now(timezone.utc))}


# ====================================================================== optional: serve the built dashboard
# (used by the no-Docker / Colab runner so one tunnel exposes UI + API + WebSocket)
_DASH = os.environ.get("DASHBOARD_DIR")
if _DASH and os.path.isdir(_DASH):
    from pathlib import Path

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _root = Path(_DASH)
    if (_root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = (_root / path).resolve()
        if path and f.is_file() and _root.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(_root / "index.html")
