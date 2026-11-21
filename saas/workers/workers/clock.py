"""Collector clock-skew correction (global-ready: site clocks are not trusted to be in sync).

Ingest measures, for every live delivery attempt, skew = SaaS receive time - collector attempt time
= (SaaS clock - collector clock) + one-way network delay. Like NTP, the minimum over recent attempts is the
best estimate of the clock offset (delay >= 0, so the smallest sample carries the least delay).

When a collector's offset is beyond TOLERANCE_MS, every timestamp in its records is shifted into SaaS time
before it is stored or analysed, so latency SLAs, staleness, offline detection and charts all share one clock.
The payload itself (and the collector's egress proof) is never altered - only how the SaaS files it.

The applied offset is whole seconds and sticky (it only moves when the estimate drifts more than
HYSTERESIS_MS away), so a late revision of a 5 s window lands on the same event_ts as the original.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

TOLERANCE_MS = 1000  # NTP-synced clocks are well inside this: leave them alone
HYSTERESIS_MS = 750
SAMPLES = 30


class ClockSync:
    def __init__(self) -> None:
        self.samples: dict[str, deque] = {}
        self.applied: dict[str, int] = {}

    def observe(self, collector: str, skew_ms: float | None) -> int:
        """Feed one live attempt's measured skew; returns the offset (ms) to ADD to that collector's timestamps."""
        if skew_ms is not None:
            q = self.samples.setdefault(collector, deque(maxlen=SAMPLES))
            q.append(float(skew_ms))
            est = min(q)
            cur = self.applied.get(collector, 0)
            if abs(est) < TOLERANCE_MS:
                self.applied[collector] = 0
            elif cur == 0 or abs(est - cur) > HYSTERESIS_MS:
                self.applied[collector] = int(round(est / 1000.0)) * 1000
        return self.applied.get(collector, 0)

    def offset(self, collector: str) -> int:
        return self.applied.get(collector, 0)


def _shift(v: str, off: timedelta) -> str:
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return v
    return (t + off).astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def shift_payload(p: dict, offset_ms: int) -> dict:
    """Return a copy of the payload with sent_ts and every record *_ts moved by offset_ms."""
    if not offset_ms:
        return p
    off = timedelta(milliseconds=offset_ms)
    out = dict(p)
    if isinstance(p.get("sent_ts"), str):
        out["sent_ts"] = _shift(p["sent_ts"], off)
    out["records"] = [{k: (_shift(v, off) if k.endswith("_ts") and isinstance(v, str) else v) for k, v in r.items()}
                      for r in p.get("records", [])]
    out["clock_offset_ms"] = offset_ms
    return out

