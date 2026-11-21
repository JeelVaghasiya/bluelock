"""Per-tenant AIOps engine: L1 baselines + L2 HST + L3 autoencoder + L4 correlation + L5 explain.

Pure in-memory logic (no I/O) so it is unit-testable; workers/main.py persists its output.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from .baseline import BaselineBank
from .correlate import Alert, Correlator, Incident
from .explain import explain
from .models import HST, AutoEncoder, features_for

MODEL_METRICS = {"cpu", "mem", "disk", "net_util", "rtt", "loss", "jitter", "up", "error_rate"}
L1_METRICS = {"cpu", "mem", "disk", "rtt", "loss", "jitter", "up", "error_rate", "net_util",
              # wider estate: the signals that mean trouble (pure volume metrics such as qps or iops are context)
              "io_latency", "query_ms", "repl_lag", "cache_hit", "apdex", "queue_depth", "consumer_lag",
              "if_errors", "drops"}


@dataclass
class Noise:
    raw_alerts: int = 0
    incidents: int = 0
    suppressed: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def as_dict(self) -> dict:
        return {"raw_alerts": self.raw_alerts, "incidents": self.incidents, "suppressed": dict(self.suppressed),
                "reduction": round(1 - self.incidents / self.raw_alerts, 3) if self.raw_alerts else 0.0}


@dataclass
class Out:
    kind: str  # incident | alert | resolved
    incident: Incident | None = None
    alert: Alert | None = None
    created: bool = False
    evidence: dict | None = None


class TenantEngine:
    def __init__(self, tenant: str, playbook: dict, warmup_windows: int = 60, seq_start: int = 0):
        self.tenant = tenant
        self.playbook = playbook
        self.bank = BaselineBank(warmup_windows=warmup_windows)
        self.hst = HST(warmup=warmup_windows)
        self.ae = AutoEncoder(min_train=max(30, warmup_windows))
        self.corr = Correlator(seq_start)
        self.vectors: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
        self.model_flags: dict[str, dict] = {}  # src -> latest {"L2": {...}, "L3": {...}}
        self.active: dict[tuple[str, str], Alert] = {}
        self.noise = Noise()

    # ------------------------------------------------------------------ inputs
    def on_topology(self, rec: dict) -> None:
        self.corr.set_topology(rec.get("nodes", []), rec.get("edges", []))

    def _models(self, src: str, event_ts: str, metric: str, value: float) -> None:
        if metric not in MODEL_METRICS:
            return
        key = (src, event_ts)
        self.vectors[key][metric] = value
        keys = features_for(src)
        if keys and all(k in self.vectors[key] for k in keys):
            vec = self.vectors.pop(key)
            flags = {}
            s, thr = self.hst.score(src, vec)
            if s is not None and thr is not None:
                flags["L2"] = {"score": s, "threshold": thr, "anomalous": s > thr}
            ae = self.ae.observe(src, vec)
            if ae:
                flags["L3"] = ae
            self.model_flags[src] = flags
        if len(self.vectors) > 5000:  # incomplete vectors never complete: drop the oldest
            for k in list(self.vectors)[:1000]:
                del self.vectors[k]

    def on_metric(self, rec: dict, now: datetime) -> list[Out]:
        src, metric = rec["src"], rec["metric"]
        stat_val = rec.get("avg")
        self._models(src, rec["event_ts"], metric, float(stat_val))
        if metric not in L1_METRICS or rec.get("rev", 0) > 0 or (metric == "net_util" and src.startswith("dev:")):
            return []
        v = self.bank.observe(src, metric, rec)
        if v.suppressed:
            self.noise.suppressed[v.suppressed.split(" (")[0].split(" ")[0]] += 1
        out: list[Out] = []
        key = (src, metric)
        if v.raised:
            flags = self.model_flags.get(src, {})
            layers = {"L1"}
            hst = flags.get("L2")
            ae = flags.get("L3")
            if hst and hst.get("anomalous"):
                layers.add("L2")
            if ae and ae.get("anomalous"):
                layers.add("L3")
            sev = "high" if abs(v.z) >= 6 else "medium"
            a = Alert(alert_id=str(uuid.uuid4()), src=src, metric=metric, layer="L1",
                      onset_ts=self.bank.get(src, metric).onset_ts or rec["event_ts"],
                      breach_ts=rec["event_ts"], last_ts=rec["event_ts"], z=v.z, value=round(v.value, 4),
                      baseline=v.baseline, severity=sev, category="anomaly", persistence=v.persistence,
                      layers=layers, contrib=(ae or {}).get("contrib"), hst=hst)
            self.active[key] = a
            out.extend(self._correlate(a, now))
        elif key in self.active:
            a = self.active[key]
            a.last_ts, a.z, a.value = rec["event_ts"], v.z, round(v.value, 4)
            if v.cleared:
                a.active = False
                del self.active[key]
        return out

    def on_finding(self, rec: dict, now: datetime) -> list[Out]:
        cat = rec.get("category", "threshold")
        a = Alert(alert_id=rec.get("finding_id") or str(uuid.uuid4()), src=rec["src"], metric=rec.get("metric"),
                  layer=rec.get("layer", "T"), onset_ts=rec["breach_ts"], breach_ts=rec["breach_ts"],
                  last_ts=rec["event_ts"], z=rec.get("z"), value=rec.get("value"), baseline=rec.get("baseline"),
                  severity=rec.get("severity", "high"), category=cat, pattern=rec.get("pattern"),
                  layers={rec.get("layer", "T")}, risk=rec.get("risk"), fail_count=rec.get("fail_count"),
                  distinct_users=rec.get("distinct_users"), window_s=rec.get("window_s"),
                  active=cat != "auth")
        if cat != "auth" and a.metric:
            self.active.setdefault((a.src, a.metric + ":T"), a)
        return self._correlate(a, now)

    def _correlate(self, a: Alert, now: datetime) -> list[Out]:
        self.noise.raw_alerts += 1
        inc, created = self.corr.add(a, now)
        if created:
            self.noise.incidents += 1
        ev = explain(inc, self.corr, self.playbook)
        return [Out("alert", inc, a), Out("incident", inc, a, created, ev)]

    def tick(self, now: datetime) -> list[Out]:
        # threshold (T) alerts clear once the baseline says the signal is normal again
        for key in [k for k in self.active if k[1].endswith(":T")]:
            src, metric = key[0], key[1][:-2]
            bl = self.bank.b.get((src, metric))
            if bl is None or (not bl.active and abs(bl.last_z) < 3):
                self.active[key].active = False
                del self.active[key]
        return [Out("resolved", inc) for inc in self.corr.expire(now)]

    def mark_remediated(self, incident_id: str) -> None:
        for inc in self.corr.open:
            if inc.incident_id == incident_id:
                for m in inc.members:
                    m.active = False
