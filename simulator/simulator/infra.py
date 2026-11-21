"""Signal models for the wider estate: databases, caches, queues, web apps, storage arrays and network
devices. Every model is driven by the same daily load curve as hosts and links, so a busy hour shows up
everywhere at once, and faults propagate the way they do in real estates:

  storage I/O latency  -> database query time -> app latency / errors
  cache miss storm     -> database QPS and query time rise
  network device errors -> services routed through it error and slow down
  queue consumers slow -> queue depth and consumer lag grow

Each step returns [(metric, value, unit)] in the trustline.v1 vocabulary.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

Sample = tuple[str, float, str]


def _jit(rng: np.random.Generator, sd: float) -> float:
    return 1.0 + float(rng.normal(0, sd))


@dataclass
class AppState:
    """Role-specific application metrics for one service (on top of up / rtt / error_rate)."""
    role: str  # web | api | db | cache | queue | other | compute
    rng: np.random.Generator
    scale: float = 1.0  # traffic size of this service relative to the others
    depth: float = 40.0  # queue backlog (queue role)
    lag_ms: float = 40.0  # replication lag (db role)
    extra: dict = field(default_factory=dict)

    def step(self, load: float, faults: dict, err: float, latency_ms: float, io_latency: float = 1.0) -> list[Sample]:
        r, s = self.rng, self.scale
        out: list[Sample] = []
        if self.role in ("web", "api", "other"):
            rate = s * (120 + 900 * load) * _jit(r, 0.05)
            target = 40.0  # apdex T: satisfied under 40 ms, tolerating under 160 ms
            slow = min(1.0, max(0.0, (latency_ms - target) / (3 * target)))
            apdex = 1.0 - 2.5 * err - 0.5 * slow + float(r.normal(0, 0.004))
            out += [("req_rate", rate, "ops/s"), ("apdex", float(np.clip(apdex, 0, 1)), "ratio")]
        elif self.role == "db":
            miss = faults.get("cache_miss", 0.0)  # upstream cache misses fall through to the database
            qps = s * (250 + 1400 * load) * (1 + 1.4 * miss) * _jit(r, 0.05)
            conns = 30 + 150 * load + 120 * miss + float(r.normal(0, 3))
            q_ms = float(r.lognormal(math.log(3.5), 0.25)) * (1 + 0.6 * load + 0.8 * miss) + 3.0 * io_latency
            # replication lag: small, noisy; a lag fault ramps it towards magnitude ms
            goal = 35.0 + faults.get("repl_lag", 0.0)
            self.lag_ms += 0.08 * (goal - self.lag_ms)
            lag = self.lag_ms * float(r.lognormal(0, 0.2))
            hit = 0.988 - 0.01 * load - 0.05 * miss + float(r.normal(0, 0.0015))
            out += [("qps", qps, "ops/s"), ("db_conns", max(1.0, round(conns)), "count"), ("query_ms", q_ms, "ms"),
                    ("repl_lag", lag, "ms"), ("cache_hit", float(np.clip(hit, 0, 1)), "ratio")]
        elif self.role == "cache":
            miss = faults.get("cache_miss", 0.0)
            rate = s * (900 + 3800 * load) * _jit(r, 0.04)
            hit = 0.955 - 0.02 * load - miss + float(r.normal(0, 0.003))
            ev = (4 + 35 * load) * (1 + 25 * miss) * _jit(r, 0.15)
            out += [("req_rate", rate, "ops/s"), ("cache_hit", float(np.clip(hit, 0, 1)), "ratio"),
                    ("evictions", max(0.0, ev), "ops/s")]
        elif self.role == "queue":
            pub = s * (60 + 420 * load) * _jit(r, 0.08)
            # healthy consumers have 1.5x headroom over the publish rate; a backlog fault takes a share of it away
            capacity = (1.5 * pub + 30) * (1 - faults.get("consumer_slow", 0.0))
            self.depth = max(0.0, self.depth + pub - min(capacity, self.depth + pub))
            self.depth += float(r.normal(0, 4)) if self.depth > 10 else 0.0
            depth = max(0.0, self.depth) + 20 + 30 * load  # in-flight messages even when healthy
            lag = depth / max(1.0, min(capacity, pub + depth)) * 1000
            out += [("req_rate", pub, "ops/s"), ("queue_depth", round(depth), "count"), ("consumer_lag", lag, "ms")]
        elif self.role == "compute":
            out += [("req_rate", s * (8 + 40 * load) * _jit(r, 0.1), "ops/s")]  # batch jobs/s
        return out


@dataclass
class StorageState:
    """A storage array / NAS: IOPS follow load; latency rises with utilisation (queueing) and faults."""
    rng: np.random.Generator
    cap_iops: float = 20000.0
    base_ms: float = 0.9
    io_size: float = 16384.0
    capacity: float = 62.0  # % used, fills very slowly (a near-flat series: the collector suppresses repeats)
    fill_per_s: float = 0.00002
    last_latency: float = 1.0

    def step(self, load: float, faults: dict) -> list[Sample]:
        r = self.rng
        iops = self.cap_iops * (0.12 + 0.55 * load) * _jit(r, 0.06)
        u = min(0.98, iops / self.cap_iops)
        lat = self.base_ms * (1 + 2.5 * u ** 3) * float(r.lognormal(0, 0.12)) * faults.get("io_mult", 1.0)
        self.last_latency = lat
        self.capacity = min(99.0, self.capacity + self.fill_per_s)
        qd = iops * lat / 1000.0  # Little's law: outstanding I/Os
        return [("iops", iops, "ops/s"), ("io_bytes", iops * self.io_size, "B/s"), ("io_latency", lat, "ms"),
                ("queue_depth", round(qd, 1), "count"), ("capacity", round(self.capacity, 2), "%")]


@dataclass
class NetDeviceState:
    """A switch, firewall or load balancer: port utilisation, errors, drops, control-plane CPU/mem."""
    rng: np.random.Generator
    kind: str = "switch"  # switch | firewall | lb
    port_bps: float = 1.25e9  # 10 Gbit/s
    base_util: float = 18.0
    mem: float = 41.0

    def step(self, load: float, faults: dict) -> list[Sample]:
        r = self.rng
        util = float(np.clip(self.base_util + 42 * load + r.normal(0, 1.5), 0.5, 100))
        bps = util / 100 * self.port_bps
        errs = float(r.poisson(0.03 + faults.get("if_errors", 0.0)))
        drops = float(r.poisson(0.15 + 4 * max(0.0, util - 80) / 20 + 0.6 * faults.get("if_errors", 0.0)))
        cpu = float(np.clip(9 + 22 * load + r.normal(0, 1.2) + 0.3 * faults.get("if_errors", 0.0), 0, 100))
        out = [("net_util", util, "%"), ("net_in", bps * 0.52, "B/s"), ("net_out", bps * 0.48, "B/s"),
               ("if_errors", errs, "ops/s"), ("drops", drops, "ops/s"), ("cpu", cpu, "%"),
               ("mem", self.mem, "%"),  # control-plane memory is static: suppressed as unchanged after first window
               ("up", 1.0, "ratio")]
        if self.kind in ("firewall", "lb"):
            out.append(("sessions", round(1500 + 16000 * load * _jit(r, 0.03)), "count"))
        return out
