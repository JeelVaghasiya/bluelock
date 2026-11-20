"""Stage 5 - Analyse at the edge.

* Fast path (T): per (src, metric) on raw 1-second samples: N consecutive samples beyond the
  Poor threshold -> a high-severity threshold_breach finding, flushed immediately.
  breach_ts = first offending sample, so alert latency is measured from the real breach.
* L0 security (needs raw login records, so it runs here): sliding 60 s per coded source IP
  -> brute_force / spray / success_after_failures, risk from rules + Isolation Forest.
* Edge pre-score: a robust z (median / MAD of recent samples) attached to every finding.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

import numpy as np

from ..models import CleanEvent, Counters


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def is_poor(value: float, th: dict) -> bool:
    if th.get("lower_is_worse"):
        return value < th["fair"]
    return value >= th["fair"]


class FastPath:
    def __init__(self, threshold_for, consecutive: int = 3, rearm_after: int = 15):
        self.threshold_for = threshold_for
        self.n = consecutive
        self.rearm = rearm_after
        self.run_len: dict[tuple[str, str], int] = defaultdict(int)
        self.first_ts: dict[tuple[str, str], float] = {}
        self.normal_run: dict[tuple[str, str], int] = defaultdict(int)
        self.armed: dict[tuple[str, str], bool] = defaultdict(lambda: True)
        self.hist: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=300))

    def robust_z(self, key, value) -> tuple[float | None, float | None]:
        h = self.hist[key]
        if len(h) < 30:
            return None, None
        arr = np.fromiter(h, float)
        med = float(np.median(arr))
        mad = float(np.median(np.abs(arr - med)))
        z = (value - med) / (1.4826 * mad + max(1e-6, abs(med) * 0.01))
        return round(med, 4), round(z, 2)

    def check(self, ce: CleanEvent, src_tz: str) -> dict | None:
        th = self.threshold_for(ce.metric)
        key = (ce.src, ce.metric)
        baseline, z = self.robust_z(key, ce.value)
        self.hist[key].append(ce.value)
        if not th or (ce.metric == "rtt" and not ce.src.startswith("link:")):
            return None
        if is_poor(ce.value, th):
            self.normal_run[key] = 0
            if self.run_len[key] == 0:
                self.first_ts[key] = ce.event_ts
            self.run_len[key] += 1
            if self.run_len[key] >= self.n and self.armed[key]:
                self.armed[key] = False
                now = time.time()
                down = ce.metric == "up"
                return {
                    "type": "finding", "finding_id": str(uuid.uuid4()), "event_ts": iso(ce.event_ts),
                    "breach_ts": iso(self.first_ts[key]), "collect_ts": iso(now), "src_tz": src_tz,
                    "src": ce.src, "category": "connectivity" if down else "threshold",
                    "pattern": "link_down" if down and ce.src.startswith("link:") else "threshold_breach",
                    "severity": "high", "risk": 0.8, "layer": "T", "metric": ce.metric,
                    "value": round(ce.value, 4), "threshold": float(th["fair"]),
                    "baseline": baseline, "z": z, "window_s": self.n,
                }
        else:
            self.run_len[key] = 0
            self.normal_run[key] += 1
            if self.normal_run[key] >= self.rearm:
                self.armed[key] = True
        return None


class SecurityL0:
    """Login-attack detection per coded source IP over a sliding window."""

    def __init__(self, window_s: int = 60, brute: int = 10, spray_users: int = 5, saf: int = 5):
        self.win = window_s
        self.brute, self.spray_users, self.saf = brute, spray_users, saf
        self.fails: dict[str, deque] = defaultdict(deque)  # ip -> (ts, user)
        self.successes: dict[str, deque] = defaultdict(deque)
        self.cooldown: dict[tuple[str, str], float] = {}
        self.iforest = None
        self.training: list[list[float]] = []
        self._seed_model()

    def _seed_model(self) -> None:
        """Isolation Forest seeded with benign behaviour (few fails, 1 user, office hours);
        refit later with features observed on site."""
        try:
            from sklearn.ensemble import IsolationForest
        except Exception:  # pragma: no cover
            return
        rng = np.random.default_rng(0)
        X = np.column_stack([
            rng.poisson(0.3, 500) / 60.0,  # fail rate per s
            rng.integers(1, 3, 500),  # distinct users
            np.sin(rng.uniform(0, 2 * math.pi, 500)),  # hour of day (cyclic)
            rng.uniform(0.6, 1.0, 500),  # success ratio
        ])
        self.iforest = IsolationForest(n_estimators=100, contamination=0.01, random_state=0).fit(X)

    def _features(self, ip: str, now: float) -> list[float]:
        fails = self.fails[ip]
        succ = self.successes[ip]
        users = {u for _, u in fails}
        total = len(fails) + len(succ)
        hour = datetime.fromtimestamp(now, tz=timezone.utc).hour
        return [len(fails) / self.win, len(users), math.sin(hour / 24 * 2 * math.pi),
                (len(succ) / total) if total else 1.0]

    def _risk(self, feats: list[float], rule_strength: float) -> float:
        ml = 0.5
        if self.iforest is not None:
            s = float(self.iforest.score_samples([feats])[0])  # ~ -0.3 normal, -0.7 anomalous
            ml = max(0.0, min(1.0, (-s - 0.35) / 0.35))
        return round(max(0.0, min(1.0, 0.6 * rule_strength + 0.4 * ml)), 3)

    def observe(self, ce: CleanEvent, src_tz: str) -> dict | None:
        ip, now = ce.ip_code, ce.event_ts
        for dq in (self.fails[ip], self.successes[ip]):
            while dq and now - dq[0][0] > self.win:
                dq.popleft()
        if ce.auth_outcome == "fail":
            self.fails[ip].append((now, ce.user_code))
        else:
            self.successes[ip].append((now, ce.user_code))
        fails = self.fails[ip]
        users = {u for _, u in fails}
        pattern = severity = None
        strength = 0.0
        if ce.auth_outcome == "success" and len(fails) >= self.saf and ce.user_code in users:
            pattern, severity, strength = "success_after_failures", "critical", 1.0
        elif len(users) >= self.spray_users:
            pattern, severity = "spray", "high"
            strength = min(1.0, len(users) / (self.spray_users * 2.5))
        elif len(fails) >= self.brute:
            pattern, severity = "brute_force", "high"
            strength = min(1.0, len(fails) / (self.brute * 2.5))
        if not pattern:
            return None
        last = self.cooldown.get((ip, pattern), 0)
        if now - last < 30 and pattern != "success_after_failures":
            return None
        self.cooldown[(ip, pattern)] = now
        feats = self._features(ip, now)
        return {
            "type": "finding", "finding_id": str(uuid.uuid4()), "event_ts": iso(now),
            "breach_ts": iso(fails[0][0] if fails else now), "collect_ts": iso(time.time()), "src_tz": src_tz,
            "src": ip, "category": "auth", "pattern": pattern, "severity": severity,
            "risk": self._risk(feats, strength), "layer": "L0", "fail_count": len(fails),
            "distinct_users": len(users), "window_s": self.win,
        }


class Analyse:
    def __init__(self, in_q: asyncio.Queue, out_q: asyncio.Queue, counters: Counters, fast: FastPath,
                 sec: SecurityL0, src_tz: str):
        self.in_q, self.out_q, self.c = in_q, out_q, counters
        self.fast, self.sec, self.tz = fast, sec, src_tz

    async def run(self) -> None:
        while True:
            kind, item = await self.in_q.get()
            if kind == "rec":
                await self.out_q.put(item)
                continue
            ce: CleanEvent = item
            finding = None
            if ce.kind == "sample" and ce.value is not None:
                finding = self.fast.check(ce, self.tz)
            elif ce.kind == "auth":
                finding = self.sec.observe(ce, self.tz)
                if finding:
                    finding["_raw"] = [ce.rid]
            if finding:
                self.c.findings += 1
                await self.out_q.put(finding)
