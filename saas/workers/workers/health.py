"""Four-state health with reasons. Thresholds from config/thresholds.yaml (+ tenant overrides).

A source's state is the worst of its signals; an open incident makes it at least Fair;
missing data never shows as healthy (the sweeper degrades stale sources).
"""
from __future__ import annotations

from dataclasses import dataclass, field

STATES = ["excellent", "good", "fair", "poor"]
RANK = {s: i for i, s in enumerate(STATES)}


def worst(*states: str) -> str:
    return max(states, key=lambda s: RANK.get(s, 0))


def degrade(state: str, steps: int = 1) -> str:
    return STATES[min(3, RANK[state] + steps)]


def merged_thresholds(doc: dict, tenant: str, db_override: dict | None = None) -> dict[str, dict]:
    sig = {k: dict(v) for k, v in (doc.get("signals") or {}).items()}
    for src in ((doc.get("overrides") or {}).get(tenant) or {}, db_override or {}):
        for metric, vals in src.items():
            if metric in sig:
                sig[metric].update(vals)
    return sig


def classify(value: float, th: dict) -> str:
    if th.get("lower_is_worse"):
        if value >= th["excellent"]:
            return "excellent"
        if value >= th["good"]:
            return "good"
        if value >= th["fair"]:
            return "fair"
        return "poor"
    if value < th["excellent"]:
        return "excellent"
    if value < th["good"]:
        return "good"
    if value < th["fair"]:
        return "fair"
    return "poor"


def fmt(v: float, unit: str) -> str:
    if unit == "ratio":
        return f"{v:.3f}"
    if abs(v) >= 100:
        return f"{v:.0f}{unit}"
    return f"{v:.1f}{unit}"


@dataclass
class SourceHealth:
    src: str
    signals: dict[str, dict] = field(default_factory=dict)  # metric -> {state, value, reason, ts}
    state: str = "excellent"
    reason: str = "all signals within thresholds"
    stale: bool = False
    incident: str | None = None

    def update(self, metric: str, record: dict, th: dict) -> None:
        stat = th.get("stat", "avg")
        value = float(record.get(stat, record.get("avg")))
        if metric == "disk_hours_to_full":
            return
        st = classify(value, th)
        unit = th.get("unit", record.get("unit", ""))
        op = "<" if th.get("lower_is_worse") else ">="
        bound = {"good": th["excellent"], "fair": th["good"], "poor": th["fair"]}.get(st)
        reason = (f"{metric} {stat} {fmt(value, unit)} {op} {fmt(float(bound), unit)} ({st})"
                  if bound is not None else f"{metric} {stat} {fmt(value, unit)} ({st})")
        self.signals[metric] = {"state": st, "value": round(value, 4), "stat": stat, "reason": reason,
                                "ts": record.get("event_ts")}

    def disk_forecast(self, hours: float, th: dict | None) -> None:
        limit = (th or {}).get("poor_if_hours_to_full_lt")
        if limit and hours < float(limit):
            self.signals["disk_hours_to_full"] = {"state": "poor", "value": hours, "stat": "forecast",
                                                  "reason": f"disk full in {hours:.1f}h < {limit}h (poor)"}
        else:
            self.signals.pop("disk_hours_to_full", None)

    def evaluate(self) -> tuple[str, str]:
        if not self.signals:
            return self.state, self.reason
        metric, sig = max(self.signals.items(), key=lambda kv: RANK[kv[1]["state"]])
        st, reason = sig["state"], sig["reason"]
        if st in ("excellent", "good") and all(RANK[s["state"]] <= RANK["good"] for s in self.signals.values()):
            reason = "all signals within thresholds" if st == "excellent" else reason
        if self.incident and RANK[st] < RANK["fair"]:
            st, reason = "fair", f"open incident {self.incident} ({reason})"
        if self.stale:
            st, reason = degrade(st), "stale data: no metrics for 30 s"
        return st, reason
