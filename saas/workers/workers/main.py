"""Regional workers: writer + health + AIOps + sweeper + delivery ledger.

One durable JetStream pull consumer ("workers") on ingest.<region>.> . Scale out by running
more copies: they share the durable consumer (queue semantics). Per message:
  1. INSERT received_payloads ON CONFLICT DO NOTHING  -> conflict = duplicate past the
     JetStream window: count it, ack, skip (exactly-once in storage)
  2. write records under the tenant's RLS context, update health
  3. commit, ack, then AIOps + live push (live.<tenant>.<kind>)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import nats
import orjson
from nats.js.api import AckPolicy, ConsumerConfig, DeliverPolicy
from tl_common.config import iso, load_yaml, now, parse_ts
from tl_common.db import create_pool, tenant_tx
from tl_common.live import publish

from .aiops.engine import TenantEngine
from .clock import ClockSync, shift_payload
from .health import RANK, SourceHealth, merged_thresholds

log = logging.getLogger("workers")
REGION = os.environ.get("REGION", "ap-south")
STALE_S = int(os.environ.get("STALE_AFTER_SECONDS", "30"))
OFFLINE_S = int(os.environ.get("OFFLINE_TIMEOUT_SECONDS", "45"))
WARMUP_S = int(os.environ.get("WARMUP_SECONDS", "300"))


def ts(s):
    return parse_ts(s) if isinstance(s, str) else s


class Tenant:
    def __init__(self, name: str, thresholds: dict, playbook: dict):
        self.name = name
        self.th = merged_thresholds(thresholds, name)
        self.engine = TenantEngine(name, playbook, warmup_windows=max(6, WARMUP_S // 5))
        self.health: dict[str, SourceHealth] = {}
        self.src_collector: dict[str, str] = {}
        self.src_kind: dict[str, tuple[str, str]] = {}
        self.last_data: dict[str, datetime] = {}
        self.collectors: dict[str, dict] = {}  # id -> {last_hb_recv, last_hb_event_ts, state}
        self.incident_of_src: dict[str, str] = {}
        self.clock = ClockSync()  # per-collector clock offset (site clocks may drift)


class Workers:
    def __init__(self):
        self.thresholds = load_yaml("thresholds.yaml")
        self.playbook = load_yaml("playbook.yaml")
        self.tenants: dict[str, Tenant] = {}
        self.pool = None
        self.nc = None

    def tenant(self, name: str) -> Tenant:
        if name not in self.tenants:
            self.tenants[name] = Tenant(name, self.thresholds, self.playbook)
        return self.tenants[name]

    # ------------------------------------------------------------------ startup
    async def start(self) -> None:
        self.pool = await retry(lambda: create_pool(os.environ["DATABASE_URL"], max_size=8), "postgres")
        self.nc = await retry(lambda: nats.connect(os.environ["NATS_URL"], max_reconnect_attempts=-1), "nats")
        js = self.nc.jetstream()
        for _ in range(60):
            try:
                await js.stream_info("INGEST")
                break
            except Exception:
                log.info("waiting for INGEST stream (created by ingest-api)")
                await asyncio.sleep(2)
        sub = await js.pull_subscribe(
            f"ingest.{REGION}.>", durable="workers",
            config=ConsumerConfig(ack_policy=AckPolicy.EXPLICIT, ack_wait=30, max_ack_pending=1000,
                                  deliver_policy=DeliverPolicy.ALL))
        await self.restore()
        log.info("workers ready for region %s (warm-up %ss)", REGION, WARMUP_S)
        await asyncio.gather(self.consume(sub), self.sweeper(), self.ledger(), self.persist_baselines())

    async def restore(self) -> None:
        """Reload topology, incident sequence and baselines so a restart does not reset learning."""
        async with self.pool.acquire() as conn:
            tenants = [r[0] for r in await conn.fetch("SELECT active_tenants()")]
        for name in tenants:
            t = self.tenant(name)
            async with tenant_tx(self.pool, name) as conn:
                topo = await conn.fetchrow("SELECT nodes, edges FROM topology WHERE tenant_id=$1", name)
                if topo:
                    t.engine.on_topology({"nodes": topo["nodes"], "edges": topo["edges"]})
                    for n in topo["nodes"]:
                        t.src_kind[n["src"]] = (n["kind"], n["role"])
                n = await conn.fetchval("SELECT count(*) FROM incidents WHERE tenant_id=$1", name)
                t.engine.corr.seq = __import__("itertools").count(int(n or 0) + 1)
                from .aiops.baseline import Baseline
                for r in await conn.fetch("SELECT key, state FROM baselines WHERE tenant_id=$1", name):
                    src, _, metric = r["key"].rpartition("|")
                    t.engine.bank.b[(src, metric)] = Baseline.from_state(r["state"])
                # Open incidents live in the correlator's memory, which a restart empties: left open they could
                # never auto-resolve. Close them; baselines restart un-raised, so a signal that is still abnormal
                # is raised again within two windows as a fresh incident.
                for r in await conn.fetch("SELECT incident_id FROM incidents WHERE tenant_id=$1 AND status <> 'resolved'",
                                          name):
                    await self.resolve(conn, t, r["incident_id"],
                                       "workers restarted: re-raised as a new incident if still abnormal")
                # clock offsets: start from the last skew ingest measured, so a backlog drained right after a
                # restart is filed on the SaaS clock before the first live attempt arrives
                for r in await conn.fetch("SELECT collector_id, skew_ms FROM collectors WHERE skew_ms IS NOT NULL"):
                    t.clock.observe(r["collector_id"], float(r["skew_ms"]))

    # ------------------------------------------------------------------ consume
    async def consume(self, sub) -> None:
        while True:
            try:
                msgs = await sub.fetch(50, timeout=0.2)
            except (nats.errors.TimeoutError, asyncio.TimeoutError):
                continue
            except Exception as e:
                log.warning("fetch failed: %s", e)
                await asyncio.sleep(1)
                continue
            for msg in msgs:
                try:
                    await self.handle(msg)
                except Exception:
                    log.exception("message failed; will be redelivered")
                    await msg.nak(delay=2)

    async def handle(self, msg) -> None:
        h = msg.headers or {}
        tenant = h.get("TL-Tenant")
        p = orjson.loads(msg.data)
        recv = ts(h.get("TL-Recv-Ts")) or now()
        lane = h.get("TL-Lane", "live")
        t = self.tenant(tenant)
        cid = p["collector_id"]
        # file everything on the SaaS clock: a site laptop whose clock is 100 s fast must not produce
        # negative latencies, "future" windows or late offline detection (see clock.py)
        skew = h.get("TL-Skew-Ms")
        p = shift_payload(p, t.clock.observe(cid, float(skew) if skew else None))
        stored = now()
        live_events: list[tuple[str, dict, dict]] = []
        async with tenant_tx(self.pool, tenant) as conn:
            fresh = await conn.fetchval(
                "INSERT INTO received_payloads(payload_id, tenant_id, collector_id, seq, sha256, records, redactions,"
                " redactions_by_rule, received_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING RETURNING 1",
                p["payload_id"], tenant, cid, p["seq"], h.get("TL-Sha256", ""), len(p["records"]),
                p.get("redactions", 0), p.get("redactions_by_rule") or {}, recv)
            if not fresh:
                await conn.execute(
                    "INSERT INTO delivery_counters(tenant_id, collector_id, db_conflicts) VALUES ($1,$2,1)"
                    " ON CONFLICT (tenant_id, collector_id) DO UPDATE SET db_conflicts = delivery_counters.db_conflicts+1",
                    tenant, cid)
                fresh = False
            else:
                live_events = await self.write(conn, t, p, recv, stored, lane)
        await msg.ack()
        if fresh is False:
            await publish(self.nc, "delivery", tenant, REGION, {"collector_id": cid, "duplicate_db": True,
                                                                  "payload_id": p["payload_id"]})
            return
        timing = {"sent_ts": p["sent_ts"], "recv_ts": iso(recv), "stored_ts": iso(stored)}
        for kind, data, extra in live_events:
            await publish(self.nc, kind, tenant, REGION, data, timing={**timing, **extra}, id=data.get("id"))
        await self.analyse(t, p, lane)

    async def write(self, conn, t: Tenant, p: dict, recv, stored, lane: str) -> list:
        cid, pid, tenant = p["collector_id"], p["payload_id"], t.name
        metrics, findings, events, live = [], [], [], []
        for r in p["records"]:
            typ = r["type"]
            if typ == "metric":
                metrics.append(r)
            elif typ == "finding":
                findings.append(r)
            elif typ == "event":
                events.append(r)
            elif typ == "heartbeat":
                await self.write_heartbeat(conn, t, cid, r, recv)
                live.append(("collector", {"id": cid, "collector_id": cid, **self.collector_view(t, cid, r)}, {}))
            elif typ == "topology":
                await conn.execute(
                    "INSERT INTO topology(tenant_id, nodes, edges, updated_ts) VALUES ($1,$2,$3,now())"
                    " ON CONFLICT (tenant_id) DO UPDATE SET nodes=EXCLUDED.nodes, edges=EXCLUDED.edges, updated_ts=now()",
                    tenant, r["nodes"], r["edges"])
                t.engine.on_topology(r)
                for n in r["nodes"]:
                    t.src_kind[n["src"]] = (n["kind"], n["role"])
                    t.src_collector.setdefault(n["src"], cid)
            elif typ == "action_result":
                state = {"executed": "executed", "rolled_back": "rolled_back", "failed": "failed",
                         "refused_local_policy": "refused"}[r["status"]]
                row = await conn.fetchrow(
                    "UPDATE remediations SET state=$1, executed_ts=$2, result_code=$3 WHERE action_id=$4"
                    " RETURNING action_id, incident_id, action_key, state", state, ts(r["event_ts"]), r["status"],
                    r["action_id"])
                if row:
                    await audit(conn, tenant, f"collector:{cid}", "remediation." + state, row["incident_id"],
                                {"action_id": r["action_id"], "action_key": row["action_key"]})
                    live.append(("remediation", {"id": r["action_id"], "action_id": r["action_id"],
                                                 "incident_id": row["incident_id"], "state": state}, {}))
        if metrics:
            await conn.executemany(
                "INSERT INTO metrics(tenant_id, collector_id, src, metric, event_ts, win_s, rev, n, min, avg, p50, p95,"
                " max, unit, src_tz, collect_ts, recv_ts, payload_id, late) VALUES"
                " ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)"
                " ON CONFLICT (tenant_id, src, metric, event_ts) DO UPDATE SET rev=EXCLUDED.rev, n=EXCLUDED.n,"
                " min=EXCLUDED.min, avg=EXCLUDED.avg, p50=EXCLUDED.p50, p95=EXCLUDED.p95, max=EXCLUDED.max,"
                " recv_ts=EXCLUDED.recv_ts, payload_id=EXCLUDED.payload_id, late=true WHERE metrics.rev < EXCLUDED.rev",
                [(tenant, cid, r["src"], r["metric"], ts(r["event_ts"]), r["win_s"], r.get("rev", 0), r["n"],
                  r["min"], r["avg"], r.get("p50"), r["p95"], r["max"], r["unit"], r["src_tz"], ts(r["collect_ts"]),
                  recv, pid, r.get("rev", 0) > 0) for r in metrics])
            changes = self.update_health(t, cid, metrics)
            await self.persist_health(conn, t, changes)
            for src, old, new, reason in changes:
                hs = t.health[src]
                live.append(("health", self.health_view(t, src, hs, old), {}))
            ev_ts = min(r["event_ts"] for r in metrics if r.get("rev", 0) == 0) if any(
                r.get("rev", 0) == 0 for r in metrics) else None
            live.append(("metric", {"id": pid, "records": metrics, "collector_id": cid},
                         {"event_ts": ev_ts, "collect_ts": metrics[0]["collect_ts"], "lane": lane,
                          "revision": not ev_ts}))
        if findings:
            await conn.executemany(
                "INSERT INTO findings(tenant_id, collector_id, finding_id, event_ts, breach_ts, collect_ts, recv_ts,"
                " src, src_tz, category, pattern, severity, risk, layer, metric, value, threshold, baseline, z,"
                " fail_count, distinct_users, window_s, payload_id) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,"
                "$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23) ON CONFLICT DO NOTHING",
                [(tenant, cid, r["finding_id"], ts(r["event_ts"]), ts(r["breach_ts"]), ts(r["collect_ts"]), recv,
                  r["src"], r["src_tz"], r["category"], r["pattern"], r["severity"], r["risk"], r["layer"],
                  r.get("metric"), r.get("value"), r.get("threshold"), r.get("baseline"), r.get("z"),
                  r.get("fail_count"), r.get("distinct_users"), r.get("window_s"), pid) for r in findings])
            for r in findings:
                live.append(("finding", {"id": r["finding_id"], **r}, {"breach_ts": r["breach_ts"], "lane": lane}))
        if events:
            await conn.executemany(
                "INSERT INTO events(tenant_id, collector_id, src, template_id, event_ts, win_s, category, level,"
                " template, count, redacted, src_tz, collect_ts, recv_ts, payload_id) VALUES"
                " ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)"
                " ON CONFLICT (tenant_id, src, template_id, event_ts) DO UPDATE SET count = events.count + EXCLUDED.count,"
                " redacted = events.redacted + EXCLUDED.redacted",
                [(tenant, cid, r["src"], r["template_id"], ts(r["event_ts"]), r["win_s"], r["category"], r["level"],
                  r["template"], r["count"], r["redacted"], r["src_tz"], ts(r["collect_ts"]), recv, pid)
                 for r in events])
            live.append(("event", {"id": pid, "records": events, "redactions": p.get("redactions", 0),
                                   "payload_id": pid, "seq": p["seq"]}, {}))
        return live

    async def write_heartbeat(self, conn, t: Tenant, cid: str, r: dict, recv) -> None:
        await conn.execute(
            "INSERT INTO heartbeats(tenant_id, collector_id, event_ts, recv_ts, version, config_hash, cpu, mem_mb,"
            " buffer_depth, buffer_bytes, last_acked_seq, last_assigned_seq, redactions_total, blocked_total,"
            " dup_source_events, late_beyond_horizon, uptime_s, saas_reachable) VALUES"
            " ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18) ON CONFLICT DO NOTHING",
            t.name, cid, ts(r["event_ts"]), recv, r["version"], r["config_hash"], r["cpu"], r["mem_mb"],
            r["buffer_depth"], r["buffer_bytes"], r["last_acked_seq"], r["last_assigned_seq"], r["redactions_total"],
            r["blocked_total"], r["dup_source_events"], r["late_beyond_horizon"], r["uptime_s"], r["saas_reachable"])
        prev = t.collectors.get(cid, {})
        last = prev.get("last_hb_event_ts")
        # a "future" last heartbeat (stamped before a clock offset was corrected) must not block newer ones
        newer = not last or ts(r["event_ts"]) >= last or last > now() + timedelta(seconds=5)
        if newer:
            t.collectors[cid] = {"last_hb_recv": time.time(), "last_hb_event_ts": ts(r["event_ts"]),
                                 "state": "online", "hb": r}
            await conn.execute(
                "INSERT INTO collectors(tenant_id, collector_id, region, last_hb_event_ts, version, config_hash, cpu,"
                " mem_mb, buffer_depth, buffer_bytes, last_acked_seq, last_assigned_seq, redactions_total,"
                " blocked_total, state, state_reason) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,"
                "'online','heartbeat ok') ON CONFLICT (tenant_id, collector_id) DO UPDATE SET"
                " last_hb_event_ts=EXCLUDED.last_hb_event_ts, version=EXCLUDED.version, config_hash=EXCLUDED.config_hash,"
                " cpu=EXCLUDED.cpu, mem_mb=EXCLUDED.mem_mb, buffer_depth=EXCLUDED.buffer_depth,"
                " buffer_bytes=EXCLUDED.buffer_bytes, last_acked_seq=EXCLUDED.last_acked_seq,"
                " last_assigned_seq=EXCLUDED.last_assigned_seq, redactions_total=EXCLUDED.redactions_total,"
                " blocked_total=EXCLUDED.blocked_total, state='online', state_reason='heartbeat ok'",
                t.name, cid, REGION, ts(r["event_ts"]), r["version"], r["config_hash"], r["cpu"], r["mem_mb"],
                r["buffer_depth"], r["buffer_bytes"], r["last_acked_seq"], r["last_assigned_seq"],
                r["redactions_total"], r["blocked_total"])

    def collector_view(self, t: Tenant, cid: str, hb: dict) -> dict:
        c = t.collectors.get(cid, {})
        return {"state": c.get("state", "online"), "region": REGION, **{k: hb.get(k) for k in (
            "version", "config_hash", "cpu", "mem_mb", "buffer_depth", "buffer_bytes", "last_acked_seq",
            "last_assigned_seq", "redactions_total", "blocked_total", "saas_reachable", "uptime_s")},
                "last_hb_event_ts": hb.get("event_ts")}

    # ------------------------------------------------------------------ health
    def update_health(self, t: Tenant, cid: str, metrics: list[dict]) -> list[tuple]:
        changes = []
        touched = set()
        for r in metrics:
            src, metric = r["src"], r["metric"]
            t.src_collector[src] = cid
            t.last_data[src] = now()
            hs = t.health.setdefault(src, SourceHealth(src))
            if metric == "disk_hours_to_full":
                hs.disk_forecast(float(r["avg"]), t.th.get("disk"))
            elif metric in t.th:
                hs.update(metric, r, t.th[metric])
            touched.add(src)
        for src in touched:
            changes.extend(self._reeval(t, src, clear_stale=True))
        return changes

    def _reeval(self, t: Tenant, src: str, clear_stale: bool = False) -> list[tuple]:
        hs = t.health[src]
        if clear_stale:
            hs.stale = False
        old = getattr(hs, "_last", None)
        hs.incident = t.incident_of_src.get(src)
        st, reason = hs.evaluate()
        hs.state, hs.reason = st, reason
        if old is None or old[0] != st or (old[1] != reason and RANK[st] >= RANK["fair"]):
            hs._last = (st, reason)
            hs.since = now() if old is None or old[0] != st else getattr(hs, "since", now())
            return [(src, old[0] if old else None, st, reason)]
        return []

    def health_view(self, t: Tenant, src: str, hs: SourceHealth, old: str | None) -> dict:
        kind, role = t.src_kind.get(src, (_kind(src), "other"))
        return {"id": src, "src": src, "kind": kind, "role": role, "collector_id": t.src_collector.get(src),
                "state": hs.state, "old_state": old, "reason": hs.reason, "stale": hs.stale,
                "signals": hs.signals, "since": iso(getattr(hs, "since", now()))}

    async def persist_health(self, conn, t: Tenant, changes: list[tuple]) -> None:
        # one lock order for every transaction (ingest and sweeper both upsert many rows): no deadlocks.
        # sorted() is stable, so several changes to one source keep their order.
        for src, old, new, reason in sorted(changes, key=lambda c: c[0]):
            hs = t.health[src]
            kind, role = t.src_kind.get(src, (_kind(src), "other"))
            await conn.execute(
                "INSERT INTO health_states(tenant_id, src, kind, role, collector_id, state, stale, reason, signals,"
                " since, last_data_ts, updated_ts) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,now())"
                " ON CONFLICT (tenant_id, src) DO UPDATE SET kind=EXCLUDED.kind, role=EXCLUDED.role,"
                " collector_id=EXCLUDED.collector_id, state=EXCLUDED.state, stale=EXCLUDED.stale,"
                " reason=EXCLUDED.reason, signals=EXCLUDED.signals,"
                " since=CASE WHEN health_states.state=EXCLUDED.state THEN health_states.since ELSE EXCLUDED.since END,"
                " last_data_ts=EXCLUDED.last_data_ts, updated_ts=now()",
                t.name, src, kind, role, t.src_collector.get(src), new, hs.stale, reason, hs.signals,
                getattr(hs, "since", now()), t.last_data.get(src))
            if old != new:
                await conn.execute(
                    "INSERT INTO health_transitions(tenant_id, src, old_state, new_state, reason) VALUES ($1,$2,$3,$4,$5)",
                    t.name, src, old, new, reason)

    # ------------------------------------------------------------------ AIOps
    async def analyse(self, t: Tenant, p: dict, lane: str) -> None:
        outs = []
        n = now()
        for r in p["records"]:
            if r["type"] == "metric":
                outs += t.engine.on_metric(r, n)
            elif r["type"] == "finding":
                outs += t.engine.on_finding(r, n)
        if outs:
            await self.persist_aiops(t, outs, lane)

    async def persist_aiops(self, t: Tenant, outs: list, lane: str = "live") -> None:
        events = []
        async with tenant_tx(self.pool, t.name) as conn:
            for o in outs:
                inc = o.incident
                if o.kind == "alert":
                    a = o.alert
                    await conn.execute(
                        "INSERT INTO alerts(tenant_id, alert_id, src, metric, layer, z, value, baseline, onset_ts,"
                        " last_ts, incident_id, suppressed_reason) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)"
                        " ON CONFLICT (alert_id) DO UPDATE SET last_ts=EXCLUDED.last_ts, z=EXCLUDED.z,"
                        " incident_id=EXCLUDED.incident_id",
                        t.name, _uuid(a.alert_id), a.src, a.metric or a.pattern, a.layer, a.z, a.value, a.baseline,
                        ts(a.onset_ts), ts(a.last_ts), inc.incident_id,
                        None if inc.members[0] is a else f"absorbed into {inc.incident_id}")
                elif o.kind == "incident":
                    if inc.status != "open":
                        # resolved by the sweeper while this update waited for a connection: writing it now
                        # would re-attach its sources to a dead incident and pin them at "fair" forever
                        continue
                    ev = o.evidence
                    for s in inc.srcs:
                        t.incident_of_src[s] = inc.incident_id
                    await conn.execute(
                        "INSERT INTO incidents(tenant_id, incident_id, status, priority, severity, title, root_cause_src,"
                        " affected, evidence, alert_count, breach_ts, opened_ts, alert_ts, updated_ts) VALUES"
                        " ($1,$2,'open',$3,$4,$5,$6,$7,$8,$9,$10,$11,now(),now()) ON CONFLICT (tenant_id, incident_id)"
                        " DO UPDATE SET priority=EXCLUDED.priority, severity=EXCLUDED.severity, title=EXCLUDED.title,"
                        " root_cause_src=EXCLUDED.root_cause_src, affected=EXCLUDED.affected,"
                        " evidence=EXCLUDED.evidence, alert_count=EXCLUDED.alert_count, updated_ts=now()",
                        t.name, inc.incident_id, inc.priority, inc.severity, ev["title"], inc.root,
                        ev["affected"], ev, len(inc.members), ts(inc.breach_ts), ts(inc.opened_ts))
                    events.append(("incident", {"id": inc.incident_id, "incident_id": inc.incident_id,
                                                "status": "open", "priority": inc.priority, "severity": inc.severity,
                                                "title": ev["title"], "root_cause_src": inc.root,
                                                "affected": ev["affected"], "alert_count": len(inc.members),
                                                "confidence": ev["confidence"], "created": o.created,
                                                "noise": t.engine.noise.as_dict()},
                                   # alert latency = breach -> the incident being RAISED: only the creating
                                   # event carries breach_ts; later updates (absorbed alerts) are not samples
                                   # (an incident raised from a post-outage backlog replay is tagged so the SLA
                                   # reports it separately, exactly like backlog dashboard samples)
                                   {"breach_ts": inc.breach_ts, "lane": lane} if o.created else {}))
                elif o.kind == "resolved":
                    await self.resolve(conn, t, inc.incident_id, "auto: all signals normal for 120 s")
                    events.append(("incident", {"id": inc.incident_id, "incident_id": inc.incident_id,
                                                "status": "resolved"}, {}))
        for kind, data, timing in events:
            await publish(self.nc, kind, t.name, REGION, data, id=data["id"], timing=timing)

    async def resolve(self, conn, t: Tenant, incident_id: str, why: str) -> None:
        await conn.execute("UPDATE incidents SET status='resolved', resolved_ts=now(), updated_ts=now()"
                           " WHERE incident_id=$1 AND status <> 'resolved'", incident_id)
        await audit(conn, t.name, "aiops", "incident.resolved", incident_id, {"why": why})
        for s, i in list(t.incident_of_src.items()):
            if i == incident_id:
                del t.incident_of_src[s]

    # ------------------------------------------------------------------ periodic
    async def sweeper(self) -> None:
        """Stale data, collector offline, remediation verification, incident expiry."""
        while True:
            await asyncio.sleep(2)
            for t in list(self.tenants.values()):
                try:
                    await self.sweep_tenant(t)
                except Exception:
                    log.exception("sweep failed for %s", t.name)

    async def sweep_tenant(self, t: Tenant) -> None:
        n = now()
        events, changes = [], []
        for cid, c in t.collectors.items():
            silent = time.time() - c["last_hb_recv"]
            if silent > OFFLINE_S and c["state"] != "offline":
                c["state"] = "offline"
                for src, owner in t.src_collector.items():
                    if owner == cid and src in t.health:
                        hs = t.health[src]
                        hs.signals["collector"] = {"state": "poor", "value": 0, "stat": "heartbeat",
                                                   "reason": f"collector offline: no heartbeat for {int(silent)} s"}
                        changes.extend(self._reeval(t, src))
                async with tenant_tx(self.pool, t.name) as conn:
                    await conn.execute("UPDATE collectors SET state='offline', state_reason=$2 WHERE collector_id=$1",
                                       cid, f"no heartbeat for {int(silent)} s")
                    await conn.execute(
                        "INSERT INTO latency_samples(tenant_id, kind, ref_id, event_ts, stored_ts, total_ms)"
                        " VALUES ($1,'offline',$2,$3,$4,$5)", t.name, cid, c["last_hb_event_ts"], n,
                        (n - c["last_hb_event_ts"]).total_seconds() * 1000)
                    await audit(conn, t.name, "sweeper", "collector.offline", cid, {"silent_s": int(silent)})
                events.append(("collector", {"id": cid, "collector_id": cid, "state": "offline",
                                             "reason": f"no heartbeat for {int(silent)} s",
                                             "last_hb_event_ts": iso(c["last_hb_event_ts"])},
                               {"event_ts": iso(c["last_hb_event_ts"])}))
            elif silent <= OFFLINE_S and c["state"] == "offline":
                c["state"] = "online"
            if c["state"] == "online":
                for src, owner in t.src_collector.items():
                    if owner == cid and src in t.health and "collector" in t.health[src].signals:
                        del t.health[src].signals["collector"]
                        changes.extend(self._reeval(t, src))
        for src, last in list(t.last_data.items()):
            hs = t.health.get(src)
            if hs and not hs.stale and (n - last).total_seconds() > STALE_S:
                hs.stale = True
                changes.extend(self._reeval(t, src))
        # incident membership changed health floor (open incident => at least Fair); a link to an incident the
        # correlator no longer holds open is stale and must not pin the source at Fair
        open_ids = {i.incident_id for i in t.engine.corr.open if i.status == "open"}
        for s, i in list(t.incident_of_src.items()):
            if i not in open_ids:
                del t.incident_of_src[s]
        for src in list(t.health):
            if t.health[src].incident != t.incident_of_src.get(src):
                changes.extend(self._reeval(t, src))
        outs = t.engine.tick(n)
        async with tenant_tx(self.pool, t.name) as conn:
            if changes:
                await self.persist_health(conn, t, changes)
            for o in outs:
                await self.resolve(conn, t, o.incident.incident_id, "auto: all signals normal for 120 s")
                events.append(("incident", {"id": o.incident.incident_id, "incident_id": o.incident.incident_id,
                                            "status": "resolved"}, {}))
            events += await self.verify_remediations(conn, t)
        for src, old, new, reason in changes:
            events.append(("health", self.health_view(t, src, t.health[src], old), {}))
        for kind, data, timing in events:
            await publish(self.nc, kind, t.name, REGION, data, id=data.get("id"), timing=timing)

    async def verify_remediations(self, conn, t: Tenant) -> list:
        """executed -> verified once the root cause is Good or better for 3 windows (15 s)."""
        out = []
        rows = await conn.fetch(
            "SELECT r.action_id, r.incident_id, r.executed_ts, r.action_key, r.rollback_of, i.root_cause_src"
            " FROM remediations r JOIN incidents i ON i.incident_id = r.incident_id AND i.tenant_id = r.tenant_id"
            " WHERE r.state = 'executed'")
        for r in rows:
            age = (now() - r["executed_ts"]).total_seconds() if r["executed_ts"] else 0
            hs = t.health.get(r["root_cause_src"])
            healthy = hs is not None and RANK[hs.state] <= RANK["good"] and \
                (now() - getattr(hs, "since", now())).total_seconds() >= 15
            if r["rollback_of"]:
                healthy = age >= 5  # a rollback is verified when the inverse action executed
            if healthy and age >= 5:
                await conn.execute("UPDATE remediations SET state='verified', verified_ts=now() WHERE action_id=$1",
                                   r["action_id"])
                await audit(conn, t.name, "aiops", "remediation.verified", r["incident_id"],
                            {"action_id": str(r["action_id"]), "action_key": r["action_key"]})
                if not r["rollback_of"]:
                    t.engine.mark_remediated(r["incident_id"])
                out.append(("remediation", {"id": str(r["action_id"]), "action_id": str(r["action_id"]),
                                            "incident_id": r["incident_id"], "state": "verified"}, {}))
            elif age > 180:
                await conn.execute("UPDATE remediations SET state='failed' WHERE action_id=$1", r["action_id"])
                await audit(conn, t.name, "aiops", "remediation.failed", r["incident_id"],
                            {"action_id": str(r["action_id"]), "why": "root cause not healthy after 180 s"})
                out.append(("remediation", {"id": str(r["action_id"]), "action_id": str(r["action_id"]),
                                            "incident_id": r["incident_id"], "state": "failed"}, {}))
        return out

    async def ledger(self) -> None:
        """Set-based gap check per collector (tolerates the two delivery lanes arriving out of order)."""
        holes_since: dict[tuple[str, str], float] = {}
        while True:
            await asyncio.sleep(5)
            for t in list(self.tenants.values()):
                try:
                    async with tenant_tx(self.pool, t.name) as conn:
                        rows = await conn.fetch(
                            "SELECT p.collector_id, min(p.seq) mn, max(p.seq) mx, count(DISTINCT p.seq) n,"
                            " COALESCE(d.js_duplicates,0) js, COALESCE(d.db_conflicts,0) db,"
                            " max(c.last_assigned_seq) assigned, max(c.buffer_depth) buffered"
                            " FROM received_payloads p LEFT JOIN delivery_counters d USING (tenant_id, collector_id)"
                            " LEFT JOIN collectors c USING (tenant_id, collector_id)"
                            " WHERE p.received_at > now() - interval '30 minutes'"
                            " GROUP BY p.collector_id, d.js_duplicates, d.db_conflicts")
                        for r in rows:
                            gaps = int(r["mx"] - r["mn"] + 1 - r["n"])
                            key = (t.name, r["collector_id"])
                            if gaps > 0:
                                holes_since.setdefault(key, time.time())
                            else:
                                holes_since.pop(key, None)
                            missing = gaps > 0 and time.time() - holes_since.get(key, time.time()) > 120 and \
                                (r["assigned"] or 0) > r["mx"]
                            await conn.execute(
                                "INSERT INTO delivery_counters(tenant_id, collector_id, gaps_open) VALUES ($1,$2,$3)"
                                " ON CONFLICT (tenant_id, collector_id) DO UPDATE SET gaps_open=EXCLUDED.gaps_open",
                                t.name, r["collector_id"], gaps)
                            await publish(self.nc, "delivery", t.name, REGION, {
                                "id": r["collector_id"], "collector_id": r["collector_id"], "min_seq": r["mn"],
                                "max_seq": r["mx"], "received": r["n"], "gaps": gaps, "missing_data": missing,
                                "js_duplicates": r["js"], "db_conflicts": r["db"], "buffered": r["buffered"],
                                "last_assigned_seq": r["assigned"]})
                except Exception:
                    log.exception("ledger failed for %s", t.name)

    async def persist_baselines(self) -> None:
        while True:
            await asyncio.sleep(30)
            for t in list(self.tenants.values()):
                try:
                    rows = [(t.name, f"{src}|{metric}", b.to_state()) for (src, metric), b in t.engine.bank.b.items()]
                    if rows:
                        async with tenant_tx(self.pool, t.name) as conn:
                            await conn.executemany(
                                "INSERT INTO baselines(tenant_id, key, state, updated_ts) VALUES ($1,$2,$3,now())"
                                " ON CONFLICT (tenant_id, key) DO UPDATE SET state=EXCLUDED.state, updated_ts=now()",
                                rows)
                except Exception:
                    log.exception("baseline persist failed")


def _kind(src: str) -> str:
    return {"h": "host", "svc": "svc", "link": "link", "ip": "ip", "u": "user"}.get(src.split(":", 1)[0], "other")


def _uuid(s: str):
    import uuid
    try:
        return uuid.UUID(s)
    except ValueError:
        return uuid.uuid5(uuid.NAMESPACE_URL, s)


async def audit(conn, tenant: str, actor: str, action: str, target: str | None, detail: dict) -> None:
    await conn.execute("INSERT INTO audit_log(tenant_id, actor, role, action, target, detail) VALUES"
                       " ($1,$2,'system',$3,$4,$5)", tenant, actor, action, target, detail)


async def retry(fn, what: str, tries: int = 90):
    for i in range(tries):
        try:
            return await fn()
        except Exception as e:
            log.warning("waiting for %s (%s)", what, e)
            await asyncio.sleep(min(5, 0.5 * (i + 1)))
    raise SystemExit(f"{what} unavailable")


def main() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(Workers().start())


if __name__ == "__main__":
    main()
