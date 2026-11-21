"""Simulation Lab: every story step is runnable, SLA scoring reads the SaaS's own measurements."""
from simulator import boundary, sla
from simulator.control import STORIES
from simulator.engine import SiteRuntime  # noqa: F401  (import check)
from simulator.faults import CATALOGUE


def test_story_steps_reference_real_scenarios():
    ids = [s["id"] for s in STORIES]
    assert len(ids) == len(set(ids))
    for s in STORIES:
        for st in s["steps"]:
            assert st["op"] in ("inject", "leak", "outage", "boundary", "note"), st
            if st["op"] == "inject":
                assert st["kind"] in CATALOGUE, st
            if st["op"] == "boundary":
                assert st["test"] in boundary.TESTS
    assert {"sla_offline", "sla_flood", "sla_storm", "sla_multi"} <= set(ids)


def _data(offline_n=1, gaps=0):
    return {
        "slo": {"kinds": {"dash": {"n": 40, "p50": 2100, "p95": 3900, "max": 5200},
                          "alert": {"n": 3, "p50": 6000, "p95": 8200, "max": 9000},
                          **({"offline": {"n": offline_n, "p50": 47000, "p95": 47000, "max": 47500}} if offline_n else {})},
                "hops_ms": {}, "targets": {}},
        "fleet": {"collectors": [{"collector_id": "col-ac01", "state": "online"}]},
        "delivery": {"ledger": [{"collector_id": "col-ac01", "received": 500, "gaps": gaps, "gaps_open": 0,
                                 "js_duplicates": 12, "db_conflicts": 0, "last_assigned_seq": 500, "last_acked_seq": 500}]},
        "incidents": {"incidents": []}, "sources": {"sources": [{"src": "h:1", "stale": True}, {"src": "h:2", "stale": False}]},
    }


def test_sla_scoring():
    out = sla.evaluate(_data(), {"outbox": {"depth": 0}}, 15)
    v = {c["id"]: c["pass"] for c in out["checks"]}
    assert v == {"e2e": True, "alert": True, "offline": True, "resync": True}
    assert out["stale_sources"] == 1
    out = sla.evaluate(_data(offline_n=0, gaps=2), {"outbox": {"depth": 0}}, 15)
    v = {c["id"]: c["pass"] for c in out["checks"]}
    assert v["offline"] is None and v["resync"] is False
    # still draining after an outage: verdict pending, not failed
    out = sla.evaluate(_data(), {"outbox": {"depth": 300}}, 15)
    assert next(c for c in out["checks"] if c["id"] == "resync")["pass"] is None


def test_large_profile_matches_its_tier1_topology():
    import re
    from pathlib import Path
    from simulator.sites import PROFILES, SITES
    s = PROFILES["large"]("acme")
    text = (Path(__file__).resolve().parents[2] / "config" / "sites" / "acme-large" / "topology.yaml").read_text()
    names = set(re.findall(r"\{name: ([\w-]+),", text))
    assert names == set(s.hosts) | set(s.services) | set(s.links)
    # every role must be in the frozen trustline.v1 set, or the topology record (and its payload) is rejected
    assert set(re.findall(r"role: (\w+)", text)) <= {"web", "api", "db", "gateway", "uplink", "worker", "other"}
    std = SITES["acme"]  # stories and scenarios target these names: the large site must keep them
    assert set(std.hosts) <= set(s.hosts) and set(std.services) <= set(s.services) and set(std.links) <= set(s.links)
    assert len(s.hosts) == 24 and len(s.services) == 7 and len(s.links) == 4


def test_latency_ramp_only_hits_services_behind_that_link():
    from simulator.engine import SiteRuntime
    from simulator.sites import PROFILES
    rt = SiteRuntime(PROFILES["large"]("acme"), 1)
    from simulator.faults import Fault
    rt.book.add(Fault("latency_ramp", "wan-cdn", 9.0, 0.0, 600))
    err = {}
    for i in range(160):
        recs, _ = rt.tick(float(i))
        for r in recs:
            if r.get("kind") == "metric" and r.get("name") == "error_rate":
                err[r["service"]] = r["value"]
    assert err["search"] > 0.05  # search routes via wan-cdn
    assert err["web"] < 0.03 and err["api"] < 0.03  # web/api route via uplink-1: unaffected


def test_restart_does_not_look_like_a_memory_leak():
    """A restarted site must start each host where its memory settles, or learned baselines see a fake leak."""
    from simulator.engine import SiteRuntime
    from simulator.sites import PROFILES
    for profile in ("standard", "large"):
        long_run = SiteRuntime(PROFILES[profile]("acme"), 1)
        for i in range(600):  # ~50 min of 5 s windows
            long_run.tick(float(i))
        fresh = SiteRuntime(PROFILES[profile]("acme"), 1)
        for h, st in long_run.hosts.items():
            assert abs(st.mem - fresh.hosts[h].mem) < 3.0, (profile, h, st.mem, fresh.hosts[h].mem)
    levels = {round(st.mem) for st in SiteRuntime(PROFILES["large"]("acme"), 1).hosts.values()}
    assert len(levels) > 8  # large profile keeps a varied fleet
