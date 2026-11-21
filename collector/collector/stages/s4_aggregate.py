"""Stage 4 - Aggregate: 5-second tumbling windows by EVENT time.

* metrics: min / avg / p50 / p95 / max per (src, metric); link rtt also yields jitter;
  host disk yields disk_hours_to_full from the recent slope.
* log templates: counts per (src, template_id) per window.
* windows close on wall clock (end + grace) so a stalled stream still flushes;
* samples arriving late (<= late_horizon) re-open the window and re-emit it with rev+1;
  later ones are counted in late_beyond_horizon (the raw copy stays in raw.db).
* unchanged-series suppression: a window whose min/avg/max all sit within the unit's dead-band of the
  LAST WINDOW SENT for that series (a flat 'up' = 1, static device memory, a near-full-forever disk
  forecast...) is not sent again; a keep-alive goes out at least every KEEPALIVE_S (below the SaaS 30 s
  staleness limit) and any real change goes out at once. Noisy series are never suppressed, so no
  information is lost - only repetition.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np

from ..models import CleanEvent, Counters

LEVEL_RANK = {"info": 0, "warn": 1, "error": 2, "critical": 3}
KEEPALIVE_S = 25
DEADBAND = {"%": 0.05, "ratio": 0.0005, "ms": 0.01, "h": 0.5, "count": 0.0, "ops/s": 0.0, "B/s": 0.0}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class WindowAggregator:
    def __init__(self, src_tz: str, win_s: int = 5, grace: float = 0.5, late_horizon: float = 120,
                 counters: Counters | None = None):
        self.tz = src_tz
        self.win = win_s
        self.grace = grace
        self.horizon = late_horizon
        self.c = counters or Counters()
        # start -> key -> list[(event_ts, value)]
        self.open: dict[float, dict[tuple[str, str], list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
        self.units: dict[tuple[str, str], str] = {}
        self.events: dict[float, dict[tuple[str, str], dict]] = defaultdict(dict)
        self.closed: dict[float, dict[tuple[str, str], list[tuple[float, float]]]] = {}
        self.revs: dict[tuple[float, tuple[str, str]], int] = {}
        self.dirty: set[tuple[float, tuple[str, str]]] = set()
        self.disk_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=24))
        self.last_sent: dict[tuple[str, str], tuple[float, float, float, float]] = {}  # start, min, avg, max

    def unchanged(self, rec: dict, start: float) -> bool:
        """True if this window repeats the last one sent for its series (within the unit's dead-band)."""
        k = (rec["src"], rec["metric"])
        prev = self.last_sent.get(k)
        tol = DEADBAND.get(rec["unit"], 0.0)
        if prev and start - prev[0] < KEEPALIVE_S and abs(rec["min"] - prev[1]) <= tol \
                and abs(rec["avg"] - prev[2]) <= tol and abs(rec["max"] - prev[3]) <= tol:
            return True
        self.last_sent[k] = (start, rec["min"], rec["avg"], rec["max"])
        return False

    def start_of(self, ts: float) -> float:
        return math.floor(ts / self.win) * self.win

    # ------------------------------------------------------------------ input
    def add(self, ce: CleanEvent, now: float) -> None:
        start = self.start_of(ce.event_ts)
        if ce.kind == "sample":
            key = (ce.src, ce.metric)
            self.units[key] = ce.unit or ""
            if start in self.closed:
                if now - (start + self.win) <= self.horizon:
                    self.closed[start].setdefault(key, []).append((ce.event_ts, ce.value))
                    self.dirty.add((start, key))
                else:
                    self.c.late_beyond_horizon += 1
                return
            if now - (start + self.win) > self.horizon:
                self.c.late_beyond_horizon += 1
                return
            self.open[start][key].append((ce.event_ts, ce.value))
            return
        # log / auth -> template counts (late ones join the window they arrive in)
        if start in self.closed or start + self.win + self.grace <= now:
            start = self.start_of(now)
        b = self.events[start]
        k = (ce.src, ce.template_id)
        e = b.get(k)
        if e is None:
            b[k] = e = {"count": 0, "level": ce.level, "category": ce.category, "redacted": 0,
                        "template": ce.template, "rids": []}
        e["count"] += 1
        e["redacted"] += ce.redacted
        if LEVEL_RANK.get(ce.level, 0) > LEVEL_RANK.get(e["level"], 0):
            e["level"] = ce.level
        if len(e["rids"]) < 20:
            e["rids"].append(ce.rid)

    # ------------------------------------------------------------------ output
    def _metric(self, start: float, key: tuple[str, str], vals: list[tuple[float, float]], rev: int,
                now: float) -> list[dict]:
        src, metric = key
        arr = np.array([v for _, v in vals], dtype=float)
        rec = {
            "type": "metric", "event_ts": iso(start), "collect_ts": iso(now), "win_s": self.win, "rev": rev,
            "n": int(arr.size), "src_tz": self.tz, "src": src, "metric": metric,
            "min": round(float(arr.min()), 4), "avg": round(float(arr.mean()), 4),
            "p50": round(float(np.percentile(arr, 50)), 4), "p95": round(float(np.percentile(arr, 95)), 4),
            "max": round(float(arr.max()), 4), "unit": self.units.get(key) or "%",
        }
        out = [rec]
        if metric == "rtt" and src.startswith("link:") and arr.size >= 2:
            seq = [v for _, v in sorted(vals)]
            diffs = np.abs(np.diff(seq))
            j = round(float(diffs.mean()), 4)
            out.append({**rec, "metric": "jitter", "min": round(float(diffs.min()), 4), "avg": j, "p50": j,
                        "p95": round(float(np.percentile(diffs, 95)), 4), "max": round(float(diffs.max()), 4),
                        "unit": "ms", "n": int(diffs.size)})
        if metric == "disk" and rev == 0:
            h = self.disk_hist[src]
            h.append((start, rec["avg"]))
            if len(h) >= 6:
                xs = np.array([x for x, _ in h]) - h[0][0]
                ys = np.array([y for _, y in h])
                slope = float(np.polyfit(xs, ys, 1)[0])  # % per second
                hours = (100 - rec["avg"]) / slope / 3600 if slope > 1e-6 else 9999.0
                hours = round(min(9999.0, max(0.0, hours)), 2)
                out.append({**rec, "metric": "disk_hours_to_full", "min": hours, "avg": hours, "p50": hours,
                            "p95": hours, "max": hours, "unit": "h", "n": len(h)})
        return out

    def close_due(self, now: float) -> list[dict]:
        out: list[dict] = []
        for start in sorted(list(self.open)):
            if now >= start + self.win + self.grace:
                keys = self.open.pop(start)
                for key, vals in keys.items():
                    for rec in self._metric(start, key, vals, 0, now):
                        self.c.windows_out += 1
                        if self.unchanged(rec, start):
                            self.c.suppressed_unchanged += 1
                        else:
                            out.append(rec)
                self.closed[start] = {k: list(v) for k, v in keys.items()}
        for start in sorted(list(self.events)):
            if now >= start + self.win + self.grace:
                for (src, tid), e in self.events.pop(start).items():
                    out.append({"type": "event", "event_ts": iso(start), "collect_ts": iso(now), "win_s": self.win,
                                "src_tz": self.tz, "src": src, "category": e["category"], "level": e["level"],
                                "template_id": tid, "template": e["template"], "count": e["count"],
                                "redacted": e["redacted"], "_raw": e["rids"]})
                self.closed.setdefault(start, {})
        # revisions for windows that received late samples
        for start, key in sorted(self.dirty):
            if start not in self.closed:
                continue
            rev = min(50, self.revs.get((start, key), 0) + 1)
            self.revs[(start, key)] = rev
            self.c.late_revisions += 1
            out.extend(self._metric(start, key, self.closed[start][key], rev, now))
        self.dirty.clear()
        # forget closed windows past the late horizon
        for start in [s for s in self.closed if now - (s + self.win) > self.horizon]:
            del self.closed[start]
            for k in [k for k in self.revs if k[0] == start]:
                del self.revs[k]
        return out


class Aggregate:
    """Feeds every clean event to the aggregator and forwards it to stage 5; emits closed windows."""

    def __init__(self, in_q: asyncio.Queue, out_q: asyncio.Queue, agg: WindowAggregator):
        self.in_q, self.out_q, self.agg = in_q, out_q, agg

    async def run(self) -> None:
        while True:
            try:
                ce: CleanEvent = await asyncio.wait_for(self.in_q.get(), timeout=0.1)
                now = time.time()
                self.agg.add(ce, now)
                await self.out_q.put(("ev", ce))
            except asyncio.TimeoutError:
                pass
            for rec in self.agg.close_due(time.time()):
                await self.out_q.put(("rec", rec))
