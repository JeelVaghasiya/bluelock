"""Stage 2 - Filter: parse each source format into fields, verify, drop noise, de-duplicate.

Parsers: simulator/organiser JSON (metric, probe, check, log, auth) and sshd/PAM syslog.
Redundancy removed here (the rest happens in stage 4 aggregation):
  * source dedupe  - a 10-minute TTL set keyed on eid, else sha1(source|body): re-sent deliveries;
  * sample dedupe  - the same reading (entity, metric, timestamp, value) arriving under another event id,
                     e.g. a host scraped by two agents: counted once;
  * noise          - debug logs and health-check access lines.
Verification: every sample must be finite, inside its unit's physical range (% 0-100, ratio 0-1, rates
and counts >= 0) and not stamped more than 5 min in the future; failures are dropped and counted by reason.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

from ..models import Counters, ParsedEvent, RawEvent

# the collector's own unit table is authoritative (a source cannot relabel a metric's unit)
UNITS = {
    # compute
    "cpu": "%", "mem": "%", "disk": "%", "net_in": "B/s", "net_out": "B/s", "net_util": "%", "load": "ratio",
    # network
    "rtt": "ms", "jitter": "ms", "loss": "%", "if_errors": "ops/s", "drops": "ops/s", "sessions": "count",
    # web apps / services
    "up": "ratio", "error_rate": "ratio", "req_rate": "ops/s", "apdex": "ratio",
    # databases
    "db_conns": "count", "qps": "ops/s", "query_ms": "ms", "repl_lag": "ms", "cache_hit": "ratio",
    # other applications
    "evictions": "ops/s", "queue_depth": "count", "consumer_lag": "ms",
    # storage
    "iops": "ops/s", "io_latency": "ms", "io_bytes": "B/s", "capacity": "%",
}
ALLOWED_METRICS = set(UNITS)
# physical range per unit; "load" (per-core load average) may exceed 1
RANGE = {"%": (0.0, 100.0), "ratio": (0.0, 1.0), "ms": (0.0, 3.6e6), "B/s": (0.0, 1e12), "ops/s": (0.0, 1e9),
         "count": (0.0, 1e9)}
RANGE_OVERRIDE = {"load": (0.0, 1000.0)}
FUTURE_S = 300  # a sample stamped further ahead than this is a broken clock, not data

SYSLOG = re.compile(r"^<(\d+)>(\w{3})\s+(\d{1,2}) (\d\d:\d\d:\d\d) (\S+) ([\w./-]+)(?:\[(\d+)\])?: (.*)$")
SSH_FAIL = re.compile(r"Failed (?:password|publickey) for (invalid user )?(\S+) from (\S+) port \d+")
SSH_OK = re.compile(r"Accepted (?:password|publickey) for (\S+) from (\S+) port \d+")
PAM_FAIL = re.compile(r"authentication failure;.*rhost=(\S+)\s+user=(\S+)")
NOISE = re.compile(r"(?:GET|HEAD) /(?:healthz|health|ready|livez|metrics)\b")
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class TTLSet:
    def __init__(self, ttl: float = 600, max_items: int = 200_000):
        self.ttl = ttl
        self.max = max_items
        self.d: OrderedDict[str, float] = OrderedDict()

    def seen(self, key: str, now: float) -> bool:
        while self.d:
            k, t = next(iter(self.d.items()))
            if now - t > self.ttl or len(self.d) > self.max:
                self.d.popitem(last=False)
            else:
                break
        if key in self.d:
            return True
        self.d[key] = now
        return False


def parse_ts(s: str | None, fallback: float) -> float:
    if not s:
        return fallback
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return fallback


def tz_offset(tz: str) -> timedelta:
    sign = -1 if tz.startswith("-") else 1
    h, m = tz[1:].split(":")
    return sign * timedelta(hours=int(h), minutes=int(m))


class Filter:
    def __init__(self, in_q: asyncio.Queue, out_q: asyncio.Queue, counters: Counters, src_tz: str):
        self.in_q, self.out_q, self.c = in_q, out_q, counters
        self.dedupe = TTLSet()
        self.sample_seen = TTLSet(ttl=180, max_items=500_000)  # covers the 120 s late horizon
        self.tz = tz_offset(src_tz)

    # ------------------------------------------------------------- verification + sample dedupe
    def invalid(self, p: ParsedEvent) -> str | None:
        v = p.value
        if v is None or not math.isfinite(v):
            return "non_finite"
        lo, hi = RANGE_OVERRIDE.get(p.metric or "", RANGE.get(p.unit or "", (-math.inf, math.inf)))
        if v < lo - 1e-9:
            return "negative" if lo == 0 else "below_range"
        if v > hi + 1e-9:
            return "above_range"
        if p.event_ts - p.collect_ts > FUTURE_S:
            return "future_ts"
        return None

    def keep_sample(self, p: ParsedEvent, now: float) -> bool:
        why = self.invalid(p)
        if why:
            self.c.dropped_invalid += 1
            self.c.invalid_by_reason[why] = self.c.invalid_by_reason.get(why, 0) + 1
            return False
        if self.sample_seen.seen(f"{p.entity_prefix}|{p.entity}|{p.metric}|{p.event_ts:.3f}|{p.value!r}", now):
            self.c.dup_samples += 1
            return False
        self.c.samples_in += 1
        return True

    # ------------------------------------------------------------- parsers
    def parse_json(self, ev: RawEvent) -> list[ParsedEvent]:
        d = ev.data or {}
        ts = parse_ts(d.get("ts"), ev.collect_ts)
        k = d.get("kind")
        base = dict(rid=ev.rid, event_ts=ts, collect_ts=ev.collect_ts)
        if k == "metric":
            name = d.get("name")
            if name not in ALLOWED_METRICS or d.get("value") is None:
                self.c.dropped_unknown += 1
                return []
            if d.get("service"):
                return [ParsedEvent(kind="sample", entity_prefix="svc", entity=d["service"], metric=name,
                                    value=float(d["value"]), unit=UNITS[name], **base)]
            if d.get("device"):  # storage arrays, switches, firewalls, load balancers
                return [ParsedEvent(kind="sample", entity_prefix="dev", entity=d["device"], metric=name,
                                    value=float(d["value"]), unit=UNITS[name], **base)]
            return [ParsedEvent(kind="sample", entity_prefix="h", entity=d.get("host", "unknown"), metric=name,
                                value=float(d["value"]), unit=UNITS[name], **base)]
        if k == "probe":
            link = d.get("link", "link")
            ok = bool(d.get("ok", True))
            out = [ParsedEvent(kind="sample", entity_prefix="link", entity=link, metric="up",
                               value=1.0 if ok else 0.0, unit="ratio", **base),
                   ParsedEvent(kind="sample", entity_prefix="link", entity=link, metric="loss",
                               value=float(d.get("loss", 0)), unit="%", **base)]
            if ok:
                out.append(ParsedEvent(kind="sample", entity_prefix="link", entity=link, metric="rtt",
                                       value=float(d.get("rtt_ms", 0)), unit="ms", **base))
            return out
        if k == "check":
            svc = d.get("service", "svc")
            out = [ParsedEvent(kind="sample", entity_prefix="svc", entity=svc, metric="up",
                               value=1.0 if d.get("ok") else 0.0, unit="ratio", **base)]
            if d.get("latency_ms") is not None:
                out.append(ParsedEvent(kind="sample", entity_prefix="svc", entity=svc, metric="rtt",
                                       value=float(d["latency_ms"]), unit="ms", **base))
            return out
        if k == "log":
            msg = str(d.get("msg", ""))
            level = str(d.get("level", "info")).lower()
            if level == "debug" or NOISE.search(msg):
                self.c.dropped_noise += 1
                return []
            level = {"warning": "warn", "fatal": "critical", "err": "error"}.get(level, level)
            if level not in ("info", "warn", "error", "critical"):
                level = "info"
            return [ParsedEvent(kind="log", entity_prefix="h", entity=d.get("host", "unknown"), level=level,
                                category="app", msg=msg, **base)]
        if k == "auth":
            outcome = "success" if d.get("outcome") == "success" else "fail"
            msg = (f"{'Accepted' if outcome == 'success' else 'Failed'} login for {d.get('user')} "
                   f"from {d.get('src_ip')}")
            return [ParsedEvent(kind="auth", entity_prefix="h", entity=d.get("host", "gw"), level="warn",
                                category="auth", msg=msg, auth_user=d.get("user"), auth_ip=d.get("src_ip"),
                                auth_outcome=outcome, **base)]
        self.c.dropped_unknown += 1
        return []

    def parse_syslog(self, ev: RawEvent) -> list[ParsedEvent]:
        m = SYSLOG.match(ev.body)
        if not m:
            self.c.dropped_unknown += 1
            return []
        _pri, mon, day, hms, host, prog, _pid, msg = m.groups()
        # RFC 3164 has no year or zone: interpret in the site's local time
        now = datetime.now(timezone.utc) + self.tz
        try:
            local = datetime(now.year, MONTHS[mon], int(day), *map(int, hms.split(":")))
            ts = (local - self.tz).replace(tzinfo=timezone.utc).timestamp()
            if ts - ev.collect_ts > 86400:  # December log read in January
                ts = (local.replace(year=now.year - 1) - self.tz).replace(tzinfo=timezone.utc).timestamp()
        except (KeyError, ValueError):
            ts = ev.collect_ts
        base = dict(rid=ev.rid, event_ts=ts, collect_ts=ev.collect_ts, entity_prefix="h", entity=host)
        if prog.startswith("sshd") or "pam" in prog:
            f = SSH_FAIL.search(msg)
            if f:
                return [ParsedEvent(kind="auth", level="warn", category="auth", msg=msg, auth_user=f.group(2),
                                    auth_ip=f.group(3), auth_outcome="fail", **base)]
            ok = SSH_OK.search(msg)
            if ok:
                return [ParsedEvent(kind="auth", level="info", category="auth", msg=msg, auth_user=ok.group(1),
                                    auth_ip=ok.group(2), auth_outcome="success", **base)]
            p = PAM_FAIL.search(msg)
            if p:
                return [ParsedEvent(kind="auth", level="warn", category="auth", msg=msg, auth_user=p.group(2),
                                    auth_ip=p.group(1), auth_outcome="fail", **base)]
        return [ParsedEvent(kind="log", level="info", category="system", msg=msg, **base)]

    # ------------------------------------------------------------- loop
    async def run(self) -> None:
        while True:
            ev: RawEvent = await self.in_q.get()
            key = ev.eid or hashlib.sha1(f"{ev.source}|{ev.body}".encode()).hexdigest()
            if self.dedupe.seen(key, time.time()):
                self.c.dup_source_events += 1
                continue
            try:
                parsed = self.parse_syslog(ev) if ev.source == "syslog" else self.parse_json(ev)
            except Exception:
                self.c.dropped_unknown += 1
                continue
            now = time.time()
            for p in parsed:
                if p.kind == "sample" and not self.keep_sample(p, now):
                    continue
                self.c.parsed += 1
                await self.out_q.put(p)
