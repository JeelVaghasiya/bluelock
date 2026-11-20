"""Fault catalogue and per-site fault state."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

# kind -> (target type, default magnitude, default duration_s, description)
CATALOGUE: dict[str, tuple[str | None, float, int, str]] = {
    "cpu_spike": ("host", 45.0, 180, "CPU +magnitude points on a host"),
    "mem_leak": ("host", 0.12, 900, "memory grows magnitude %/s on a host"),
    "disk_fill": ("host", 0.15, 600, "disk fills magnitude %/s on a host"),
    "latency_ramp": ("link", 9.0, 900, "link RTT ramps to magnitude x over 60 s; dependents error after ~75 s"),
    "packet_loss": ("link", 6.0, 300, "link loss +magnitude %"),
    "link_down": ("link", 1.0, 120, "link probes fail; dependent services error"),
    "service_errors": ("svc", 0.10, 300, "service error rate +magnitude"),
    "login_spray": (None, 1.0, 40, "one attacker IP tries many usernames (1/s)"),
    "brute_force": (None, 2.0, 30, "one attacker IP hammers one account (magnitude/s)"),
    "success_after_failures": (None, 1.0, 10, "8 failures then a successful login"),
    "secret_in_log": (None, 1.0, 0, "one app log line containing a secret (target = rule name)"),
    "dup_burst": (None, 2.0, 0, "re-send the last tick magnitude times with identical event ids"),
    "out_of_order": (None, 1.0, 30, "shuffle and delay records by 2-8 s"),
    "clock_skew": (None, 8.0, 120, "source timestamps run magnitude seconds fast"),
}

DEFAULT_TARGETS = {"host": "api-01", "link": "uplink-1", "svc": "api"}


@dataclass
class Fault:
    kind: str
    target: str | None
    magnitude: float
    start: float
    duration: float
    id: str = ""

    def active(self, now: float) -> bool:
        return self.duration <= 0 or now < self.start + self.duration

    def elapsed(self, now: float) -> float:
        return now - self.start


@dataclass
class FaultBook:
    faults: list[Fault] = field(default_factory=list)
    failed_over: set[str] = field(default_factory=set)
    blocked_ips: set[str] = field(default_factory=set)

    def add(self, f: Fault) -> None:
        self.faults.append(f)

    def prune(self, now: float) -> None:
        self.faults = [f for f in self.faults if f.duration > 0 and f.active(now)]

    def of(self, kind: str, now: float) -> list[Fault]:
        return [f for f in self.faults if f.kind == kind and f.active(now)]

    def clear(self, kind: str | None = None, target: str | None = None) -> int:
        before = len(self.faults)
        self.faults = [
            f for f in self.faults
            if not ((kind is None or f.kind == kind) and (target is None or f.target == target))
        ]
        return before - len(self.faults)

    def summary(self, now: float) -> list[dict]:
        return [
            {"kind": f.kind, "target": f.target, "magnitude": f.magnitude,
             "elapsed_s": round(f.elapsed(now)), "remaining_s": round(f.start + f.duration - now)}
            for f in self.faults if f.active(now)
        ]


def now() -> float:
    return time.time()
