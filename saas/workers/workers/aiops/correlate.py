"""L4 - dependency-graph correlation: many alerts -> one incident, with a root cause.

Graph comes from the collector's pseudonymised TopologyRecord (codes + roles only).
Edge dependent -> dependency ("api depends_on db", "api routes_via uplink-1").
An alert joins an open incident when it is within +/-120 s of a member's onset AND
(same source, or graph distance <= 2). Root cause = the member that depends on no other
member (most upstream); ties -> earliest onset. Rejected candidates keep their reason.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime

import networkx as nx

WINDOW_S = 120


def ts(s: str | datetime) -> datetime:
    return s if isinstance(s, datetime) else datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class Alert:
    alert_id: str
    src: str
    metric: str | None
    layer: str  # L1 | T | L0
    onset_ts: str
    breach_ts: str
    last_ts: str
    z: float | None = None
    value: float | None = None
    baseline: float | None = None
    severity: str = "medium"
    category: str = "anomaly"  # anomaly | threshold | auth | connectivity
    pattern: str | None = None
    persistence: float = 1.0
    layers: set[str] = field(default_factory=set)
    risk: float | None = None
    fail_count: int | None = None
    distinct_users: int | None = None
    window_s: int | None = None
    contrib: dict | None = None
    hst: dict | None = None
    active: bool = True


@dataclass
class Incident:
    incident_id: str
    members: list[Alert] = field(default_factory=list)
    status: str = "open"
    opened_ts: str = ""
    root: str | None = None
    rejected: list[dict] = field(default_factory=list)
    suppressed: list[dict] = field(default_factory=list)
    priority: str = "P4"
    severity: str = "medium"
    blast: int = 1
    last_activity: datetime | None = None

    @property
    def srcs(self) -> list[str]:
        seen: list[str] = []
        for m in self.members:
            if m.src not in seen:
                seen.append(m.src)
        return seen

    @property
    def breach_ts(self) -> str:
        return min((m.breach_ts for m in self.members), key=ts)


SEV_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


class Correlator:
    def __init__(self, seq_start: int = 0):
        self.g = nx.DiGraph()
        self.roles: dict[str, str] = {}
        self.open: list[Incident] = []
        self.seq = itertools.count(seq_start + 1)

    def set_topology(self, nodes: list[dict], edges: list[dict]) -> None:
        g = nx.DiGraph()
        for n in nodes:
            g.add_node(n["src"], kind=n.get("kind"), role=n.get("role"))
            self.roles[n["src"]] = n.get("role", "other")
        for e in edges:
            g.add_edge(e["dependent"], e["dependency"], kind=e.get("kind"))
        self.g = g

    def distance(self, a: str, b: str) -> int | None:
        if a == b:
            return 0
        if a not in self.g or b not in self.g:
            return None
        try:
            return nx.shortest_path_length(self.g.to_undirected(as_view=True), a, b)
        except nx.NetworkXNoPath:
            return None

    def _fits(self, inc: Incident, a: Alert) -> bool:
        t = ts(a.onset_ts)
        for m in inc.members:
            if abs((ts(m.onset_ts) - t).total_seconds()) > WINDOW_S:
                continue
            if m.src == a.src:
                return True
            d = self.distance(m.src, a.src)
            if d is not None and d <= 2:
                return True
        return False

    def add(self, a: Alert, now: datetime) -> tuple[Incident, bool]:
        for inc in self.open:
            if inc.status == "open" and self._fits(inc, a):
                # same src+metric already a member: refresh instead of duplicating
                for m in inc.members:
                    if m.src == a.src and m.metric == a.metric and m.layer == a.layer and a.category != "auth":
                        m.last_ts, m.z, m.value, m.active = a.last_ts, a.z, a.value, True
                        m.layers |= a.layers
                        inc.last_activity = now
                        self.analyse(inc)
                        return inc, False
                inc.members.append(a)
                inc.suppressed.append({"alert_id": a.alert_id, "src": a.src, "metric": a.metric,
                                       "reason": f"absorbed into {inc.incident_id}"})
                inc.last_activity = now
                self.analyse(inc)
                return inc, False
        inc = Incident(incident_id=f"inc-{next(self.seq):04d}", members=[a], opened_ts=a.onset_ts, last_activity=now)
        self.open.append(inc)
        self.analyse(inc)
        return inc, True

    def analyse(self, inc: Incident) -> None:
        srcs = inc.srcs
        onset = {s: min((ts(m.onset_ts) for m in inc.members if m.src == s)) for s in srcs}
        candidates, rejected = [], []
        for s in srcs:
            deps = nx.descendants(self.g, s) if s in self.g else set()
            upstream = [o for o in srcs if o != s and o in deps]
            if upstream:
                lead = (onset[s] - onset[upstream[0]]).total_seconds()
                rejected.append({"src": s, "why": f"depends on {upstream[0]}; onset {lead:+.0f} s"})
            else:
                candidates.append(s)
        candidates.sort(key=lambda s: onset[s])
        inc.root = candidates[0] if candidates else srcs[0]
        for s in candidates[1:]:
            rejected.append({"src": s, "why": f"no dependency path to other members; onset later than {inc.root}"})
        inc.rejected = rejected
        impacted = set(srcs)
        if inc.root in self.g:
            impacted |= nx.ancestors(self.g, inc.root)
        inc.blast = len(impacted)
        inc.severity = max((m.severity for m in inc.members), key=lambda s: SEV_RANK.get(s, 0))
        inc.priority = priority(inc)

    def impacted(self, inc: Incident) -> list[str]:
        out = [s for s in inc.srcs if s != inc.root]
        if inc.root in self.g:
            for s in nx.ancestors(self.g, inc.root):
                if s not in out:
                    out.append(s)
        return out

    def expire(self, now: datetime, quiet_s: int = 120) -> list[Incident]:
        """Resolve incidents whose members are all back to normal and quiet for quiet_s."""
        done = []
        for inc in self.open:
            if inc.status == "open" and not any(m.active for m in inc.members) and inc.last_activity and \
                    (now - inc.last_activity).total_seconds() > quiet_s:
                inc.status = "resolved"
                done.append(inc)
        self.open = [i for i in self.open if i.status == "open"]
        return done


def priority(inc: Incident) -> str:
    sev = inc.severity
    if sev == "critical" or inc.blast >= 4 or any(m.pattern == "success_after_failures" for m in inc.members):
        return "P1"
    if sev == "high" or inc.blast >= 2:
        return "P2"
    if sev == "medium":
        return "P3"
    return "P4"
