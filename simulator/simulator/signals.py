"""Realistic signal models (seeded numpy).

CPU   : daily sine + AR(1) noise          mem  : slow drift (+ leak on demand)
disk  : linear fill + rotation drops      net  : diurnal + backup spikes
rtt   : log-normal                        loss : Gilbert-Elliott two-state bursts
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


def diurnal(t: float, tz_hours: float, peak_hour: float = 14.0) -> float:
    """0..1 daily cycle in the site's local time, peaking at peak_hour."""
    local_h = ((t / 3600.0) + tz_hours) % 24
    return 0.5 + 0.5 * math.cos((local_h - peak_hour) / 24 * 2 * math.pi)


@dataclass
class HostState:
    role: str
    rng: np.random.Generator
    cpu_ar: float = 0.0
    mem: float = 55.0
    disk: float = 52.0
    base_cpu: float = 35.0
    extra: dict = field(default_factory=dict)

    def step(self, t: float, tz_h: float, faults: dict) -> dict[str, float]:
        d = diurnal(t, tz_h)
        self.cpu_ar = 0.8 * self.cpu_ar + self.rng.normal(0, 3.0)
        cpu = self.base_cpu + 18 * d + self.cpu_ar
        cpu += faults.get("cpu_spike", 0.0)
        cpu = float(np.clip(cpu, 0.5, 100))

        # memory: mean-reverting drift around a role-specific level, plus leak
        target = 62.0 if self.role == "db" else 48.0
        self.mem += 0.02 * (target - self.mem) + self.rng.normal(0, 0.15)
        self.mem += faults.get("mem_leak", 0.0)
        self.mem = float(np.clip(self.mem, 5, 99.5))

        # disk: slow linear fill; rotation drop every ~6 h of sim time
        self.disk += 0.00015 + faults.get("disk_fill", 0.0)
        if self.disk > 75 and not faults.get("disk_fill"):
            self.disk = 52.0
        self.disk = float(np.clip(self.disk, 1, 100))

        net_base = 2.0e6 if self.role in ("web", "gateway") else 6.0e5
        backup = 4.0e6 if (int(t) % 3600) < 120 and self.role == "db" else 0.0
        net_in = max(0.0, net_base * (0.4 + d) * (1 + self.rng.normal(0, 0.08)) + backup)
        net_out = max(0.0, net_in * (1.6 if self.role == "web" else 0.7) * (1 + self.rng.normal(0, 0.05)))
        capacity = 1.25e8  # 1 Gbit/s
        return {
            "cpu": cpu, "mem": self.mem, "disk": self.disk,
            "net_in": net_in, "net_out": net_out,
            "net_util": min(100.0, 100 * (net_in + net_out) / capacity),
        }


@dataclass
class LinkState:
    rng: np.random.Generator
    bad: bool = False  # Gilbert-Elliott state

    def step(self, faults: dict) -> dict:
        # rtt ~ log-normal(mu=ln 20, sigma=0.25), multiplied by any latency ramp
        rtt = float(self.rng.lognormal(math.log(20), 0.25)) * faults.get("rtt_mult", 1.0)
        # Gilbert-Elliott: good->bad 0.2%, bad->good 20%
        if self.bad:
            self.bad = self.rng.random() > 0.20
        else:
            self.bad = self.rng.random() < 0.002
        loss = float(self.rng.uniform(5, 15)) if self.bad else float(max(0.0, self.rng.normal(0.05, 0.05)))
        loss += faults.get("loss", 0.0)
        ok = True
        if faults.get("down"):
            ok, loss, rtt = False, 100.0, 0.0
        return {"rtt_ms": round(rtt, 2), "loss": round(min(100.0, loss), 2), "ok": ok}


@dataclass
class ServiceState:
    rng: np.random.Generator

    def step(self, faults: dict, link_rtt: float | None) -> dict:
        base_err = 0.004
        err = max(0.0, self.rng.normal(base_err, 0.0015)) + faults.get("error_rate", 0.0)
        latency = float(self.rng.lognormal(math.log(12), 0.3))
        if link_rtt:
            latency += link_rtt * 0.6
        ok = self.rng.random() > min(0.95, err * 3) if not faults.get("down") else False
        return {"error_rate": round(min(1.0, err), 5), "latency_ms": round(latency, 1),
                "ok": bool(ok), "status": 200 if ok else 503}
