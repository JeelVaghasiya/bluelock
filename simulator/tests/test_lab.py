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
