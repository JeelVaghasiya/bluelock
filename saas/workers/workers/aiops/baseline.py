"""L1 - robust per-signal baselines.

Per (tenant, src, metric): a ring of recent window values gives median and MAD, plus an EWMA
level. Once an hour-of-day bucket holds >= 30 values it is used instead of the global ring,
so a daily cycle is "normal". robust z = (x - median) / (1.4826 * MAD + eps).

Noise control: learn-only warm-up, raise when |z| > 3 in 2 of the last 3 windows
(persistence), clear only after 3 normal windows (hysteresis).

Level shifts: anomalous windows are not learned, so an incident does not become the new normal. But a
signal that sits at a new level for ADAPT_WINDOWS (10 min) is a level shift (resized host, new config),
not an incident: from then on it is learned, the median moves within ~5 min and the alert clears. The
hour-of-day buckets describe the old level, so they are dropped when the shift is accepted.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

ADAPT_WINDOWS = 120  # 10 min of 5 s windows

UNIT_FLOOR = {"cpu": 1.0, "mem": 0.5, "disk": 0.5, "rtt": 1.0, "jitter": 0.5, "loss": 0.2, "up": 0.01,
              "error_rate": 0.002, "net_in": 1e4, "net_out": 1e4, "net_util": 0.2}
STAT = {"cpu": "p95", "rtt": "p95", "jitter": "avg", "loss": "avg", "up": "min", "error_rate": "avg",
        "mem": "avg", "disk": "max", "net_in": "avg", "net_out": "avg", "net_util": "avg"}


@dataclass
class Baseline:
    metric: str
    ring: deque = field(default_factory=lambda: deque(maxlen=120))
    hours: dict[int, deque] = field(default_factory=dict)
    ewma: float | None = None
    seen: int = 0
    recent: deque = field(default_factory=lambda: deque(maxlen=3))  # bool anomalous per window
    normal_run: int = 0
    active: bool = False
    onset_ts: str | None = None
    last_z: float = 0.0
    anom_run: int = 0  # consecutive anomalous windows
    shifted: bool = False  # adopting a level shift: score against the ring, not stale hour buckets

    def stats(self, hour: int | None) -> tuple[float, float, int]:
        src = self.hours.get(hour) if hour is not None else None
        arr = np.fromiter(src if src is not None and len(src) >= 30 else self.ring, float)
        if arr.size == 0:
            return 0.0, 0.0, 0
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med)))
        return med, mad, int(arr.size)

    def score(self, x: float, hour: int | None = None) -> tuple[float, float, float]:
        med, mad, n = self.stats(hour)
        if n < 10:
            return 0.0, med, mad
        eps = max(UNIT_FLOOR.get(self.metric, 0.01), abs(med) * 0.01)
        return (x - med) / (1.4826 * mad + eps), med, mad

    def learn(self, x: float, hour: int | None = None) -> None:
        self.ring.append(x)
        if hour is not None:
            self.hours.setdefault(hour, deque(maxlen=120)).append(x)
        self.ewma = x if self.ewma is None else 0.1 * x + 0.9 * self.ewma
        self.seen += 1

    def band(self, hour: int | None = None) -> tuple[float, float, float]:
        med, mad, _ = self.stats(hour)
        eps = max(UNIT_FLOOR.get(self.metric, 0.01), abs(med) * 0.01)
        w = 3 * (1.4826 * mad + eps)
        return med, med - w, med + w

    def to_state(self) -> dict:
        return {"metric": self.metric, "ring": list(self.ring), "ewma": self.ewma, "seen": self.seen,
                "hours": {str(h): list(v) for h, v in self.hours.items()}}

    @classmethod
    def from_state(cls, d: dict) -> "Baseline":
        b = cls(metric=d["metric"])
        b.ring.extend(d.get("ring", []))
        b.ewma, b.seen = d.get("ewma"), d.get("seen", 0)
        for h, v in (d.get("hours") or {}).items():
            b.hours[int(h)] = deque(v, maxlen=120)
        return b


@dataclass
class Verdict:
    raised: bool = False  # transition into anomalous
    cleared: bool = False  # transition back to normal
    anomalous_now: bool = False
    z: float = 0.0
    baseline: float = 0.0
    value: float = 0.0
    persistence: float = 0.0
    suppressed: str | None = None  # why an anomalous window did NOT alert


class BaselineBank:
    def __init__(self, warmup_windows: int = 60, z_threshold: float = 3.0, need: int = 2, clear_after: int = 3):
        self.b: dict[tuple[str, str], Baseline] = {}
        self.warmup = warmup_windows
        self.zt = z_threshold
        self.need = need
        self.clear_after = clear_after

    def get(self, src: str, metric: str) -> Baseline:
        k = (src, metric)
        if k not in self.b:
            self.b[k] = Baseline(metric)
        return self.b[k]

    def observe(self, src: str, metric: str, rec: dict) -> Verdict:
        bl = self.get(src, metric)
        x = float(rec.get(STAT.get(metric, "avg"), rec.get("avg")))
        ts = rec.get("event_ts")
        hour = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).hour if ts else None
        z, med, _ = bl.score(x, None if bl.shifted else hour)
        v = Verdict(z=round(z, 2), baseline=round(med, 4), value=x)
        anomalous = abs(z) > self.zt
        warm = bl.seen >= self.warmup
        bl.recent.append(anomalous and warm)
        hits = sum(bl.recent)
        v.persistence = round(hits / len(bl.recent), 2) if bl.recent else 0.0
        v.anomalous_now = anomalous
        if anomalous and not warm:
            v.suppressed = f"warm-up ({bl.seen}/{self.warmup} windows)"
        elif anomalous and not bl.active and hits < self.need:
            v.suppressed = f"persistence {hits}/{self.need} of last 3 windows"
        if not bl.active and hits >= self.need:
            bl.active, v.raised, bl.onset_ts = True, True, ts
            bl.normal_run = 0
        elif bl.active:
            if anomalous:
                bl.normal_run = 0
            else:
                bl.normal_run += 1
                if bl.normal_run >= self.clear_after:
                    bl.active, v.cleared = False, True
        bl.last_z = v.z
        bl.anom_run = bl.anom_run + 1 if anomalous and warm else 0
        if v.cleared and bl.shifted:
            bl.hours.clear()  # buckets learned the old level: start them again at the new one
            bl.shifted = False
        # learn from normal windows only, so an incident does not become the new normal -
        # unless the "anomaly" has lasted ADAPT_WINDOWS: then it is a level shift
        if not anomalous or not warm:
            bl.learn(x, hour)
        elif bl.anom_run >= ADAPT_WINDOWS:
            bl.shifted = True
            bl.learn(x, None)  # into the global ring only, then used instead of stale hour buckets
        return v
