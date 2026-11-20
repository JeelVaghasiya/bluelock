import time

from simulator.engine import SiteRuntime
from simulator.faults import Fault
from simulator.sites import SITES


def run(rt, seconds, t0):
    out = []
    for i in range(seconds):
        recs, lines = rt.tick(t0 + i)
        out.append((recs, lines))
    return out


def test_tick_emits_all_signal_families():
    rt = SiteRuntime(SITES["acme"], 1)
    recs, _ = rt.tick(time.time())
    kinds = {r["kind"] for r in recs}
    assert {"metric", "probe", "check", "log"} <= kinds
    names = {r.get("name") for r in recs if r["kind"] == "metric"}
    assert {"cpu", "mem", "disk", "net_in", "net_out", "net_util", "error_rate"} <= names
    assert all(r["ts"].endswith("Z") for r in recs)


def test_latency_ramp_raises_rtt_then_dependent_errors():
    rt = SiteRuntime(SITES["acme"], 2)
    t0 = 1_800_000_000.0
    rt.book.add(Fault("latency_ramp", "uplink-1", 9.0, t0, 900))
    ticks = run(rt, 120, t0)

    def rtt(i):
        return next(r["rtt_ms"] for r in ticks[i][0] if r.get("link") == "uplink-1")

    def err(i):
        return next(r["value"] for r in ticks[i][0] if r.get("service") == "api" and r["kind"] == "metric")

    assert sum(rtt(i) for i in range(70, 80)) / 10 > 4 * sum(rtt(i) for i in range(0, 5)) / 5
    assert err(115) > 0.05 > err(10)


def test_spray_emits_many_distinct_users_from_one_ip():
    rt = SiteRuntime(SITES["acme"], 3)
    t0 = 1_800_000_000.0
    rt.book.add(Fault("login_spray", None, 1.0, t0, 40))
    lines = [ln for _, ls in run(rt, 40, t0) for ln in ls if "203.0.113.7" in ln]
    users = {ln.split(" for ")[1].split(" from ")[0].replace("invalid user ", "") for ln in lines}
    assert len(lines) >= 35 and len(users) >= 14


def test_failover_clears_link_faults():
    rt = SiteRuntime(SITES["acme"], 4)
    rt.book.add(Fault("latency_ramp", "uplink-1", 9.0, time.time(), 900))
    res = rt.action("failover_uplink", "uplink-1")
    assert res["ok"] and not rt.book.faults
