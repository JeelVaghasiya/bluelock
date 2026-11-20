import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers.aiops.baseline import BaselineBank  # noqa: E402
from workers.aiops.correlate import Alert, Correlator, priority  # noqa: E402
from workers.aiops.engine import TenantEngine  # noqa: E402
from workers.aiops.explain import explain  # noqa: E402
from workers.health import SourceHealth, classify, merged_thresholds  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
PLAYBOOK = yaml.safe_load((ROOT / "config" / "playbook.yaml").read_text())
THRESH = yaml.safe_load((ROOT / "config" / "thresholds.yaml").read_text())
T0 = datetime(2026, 11, 21, 4, 0, tzinfo=timezone.utc)
LINK, API, WEB, DB = "link:3fa1c09e7d22", "svc:8b20aa41c7e5", "svc:51aa00ff1234", "svc:db00ff001122"
TOPO = {"nodes": [{"src": LINK, "kind": "link", "role": "uplink"}, {"src": API, "kind": "svc", "role": "api"},
                  {"src": WEB, "kind": "svc", "role": "web"}, {"src": DB, "kind": "svc", "role": "db"}],
        "edges": [{"dependent": API, "dependency": LINK, "kind": "routes_via"},
                  {"dependent": WEB, "dependency": API, "kind": "depends_on"},
                  {"dependent": API, "dependency": DB, "kind": "depends_on"}]}


def iso(t):
    return t.isoformat().replace("+00:00", "Z")


def rec(i, src, metric, v):
    return {"type": "metric", "event_ts": iso(T0 + timedelta(seconds=5 * i)), "src": src, "metric": metric,
            "min": v, "avg": v, "p50": v, "p95": v, "max": v, "rev": 0, "unit": "ms"}


# ------------------------------------------------------------------ L1
def test_step_change_flags_within_three_windows_after_warmup():
    bank = BaselineBank(warmup_windows=20)
    rng = np.random.default_rng(0)
    for i in range(40):
        assert not bank.observe(LINK, "rtt", rec(i, LINK, "rtt", 20 + rng.normal(0, 1))).raised
    raised = [bank.observe(LINK, "rtt", rec(40 + i, LINK, "rtt", 90)).raised for i in range(3)]
    assert any(raised[:2])


def test_single_blip_is_suppressed_by_persistence():
    bank = BaselineBank(warmup_windows=20)
    rng = np.random.default_rng(1)
    for i in range(40):
        bank.observe(LINK, "rtt", rec(i, LINK, "rtt", 20 + rng.normal(0, 1)))
    v = bank.observe(LINK, "rtt", rec(40, LINK, "rtt", 90))
    assert not v.raised and v.suppressed.startswith("persistence")
    assert not bank.observe(LINK, "rtt", rec(41, LINK, "rtt", 20)).raised


def test_warmup_suppresses_and_hysteresis_prevents_flapping():
    bank = BaselineBank(warmup_windows=20)
    v = None
    for i in range(12):
        v = bank.observe(LINK, "rtt", rec(i, LINK, "rtt", 20 + (i % 2)))
    v = bank.observe(LINK, "rtt", rec(12, LINK, "rtt", 200))
    assert v.suppressed and v.suppressed.startswith("warm-up")
    bank = BaselineBank(warmup_windows=10)
    rng = np.random.default_rng(2)
    for i in range(30):
        bank.observe(LINK, "rtt", rec(i, LINK, "rtt", 20 + rng.normal(0, 1)))
    states = []
    for i, val in enumerate([90, 90, 20, 90, 20, 20, 20]):
        v = bank.observe(LINK, "rtt", rec(30 + i, LINK, "rtt", val))
        states.append((v.raised, v.cleared))
    assert sum(r for r, _ in states) == 1  # raised once, no flapping
    assert states[-1][1]  # cleared only after 3 normal windows


# ------------------------------------------------------------------ L4 / L5
def alert(src, metric, offset_s, z, layer="L1"):
    t = iso(T0 + timedelta(seconds=offset_s))
    return Alert(alert_id=f"a-{src}-{metric}", src=src, metric=metric, layer=layer, onset_ts=t, breach_ts=t,
                 last_ts=t, z=z, value=1, baseline=0.1, severity="high" if z > 6 else "medium", layers={layer, "L2"})


def test_link_plus_dependent_services_become_one_incident_rooted_at_link():
    c = Correlator()
    c.set_topology(TOPO["nodes"], TOPO["edges"])
    inc, created = c.add(alert(API, "error_rate", 94, 4.8), T0)
    assert created
    c.add(alert(LINK, "rtt", 0, 7.4), T0)
    c.add(alert(WEB, "rtt", 100, 3.5), T0)
    assert len(c.open) == 1 and inc.root == LINK
    assert {r["src"] for r in inc.rejected} == {API, WEB}
    ev = explain(inc, c, PLAYBOOK)
    assert ev["rule"] == "upstream_link_degradation" and ev["action"]["key"] == "failover_uplink"
    assert abs(sum(s["weight"] for s in ev["signals"]) - 1) < 0.02
    assert ev["signals"][0]["src"] == LINK and ev["noise"]["raw_alerts"] == 3
    assert [t["step"] for t in ev["trace"]][-4:] == ["correlate", "root_cause", "playbook", "policy"]
    json.dumps(ev)


def test_unrelated_sources_make_separate_incidents():
    c = Correlator()
    c.set_topology(TOPO["nodes"], TOPO["edges"])
    c.add(alert(LINK, "rtt", 0, 7), T0)
    c.add(alert("h:ffffffffffff", "mem", 10, 5), T0)
    c.add(alert(DB, "error_rate", 600, 5), T0)  # related but 10 min later
    assert len(c.open) == 3


def test_confidence_formula():
    c = Correlator()
    c.set_topology(TOPO["nodes"], TOPO["edges"])
    a = alert(LINK, "rtt", 0, 8.0)
    a.layers = {"L1", "L2", "L3"}
    inc, _ = c.add(a, T0)
    ev = explain(inc, c, PLAYBOOK)
    assert ev["confidence"] == pytest.approx(0.5 * 1 + 0.3 * 1 + 0.2 * 1)


def test_priority_table_and_auth_rule():
    c = Correlator()
    a = Alert(alert_id="x", src="ip:3fa1c09e7d22", metric=None, layer="L0", onset_ts=iso(T0), breach_ts=iso(T0),
              last_ts=iso(T0), severity="critical", category="auth", pattern="success_after_failures",
              risk=0.95, fail_count=8, distinct_users=1, window_s=60, layers={"L0"})
    inc, _ = c.add(a, T0)
    assert priority(inc) == "P1"
    ev = explain(inc, c, PLAYBOOK)
    assert ev["rule"] == "auth_attack" and "Block source" in ev["action"]["text"]


def test_login_flood_storm_is_one_incident_with_rate_limit_action():
    """A flood finding plus per-IP findings from 30 different IPs: ONE incident, login_flood rule."""
    c = Correlator()
    flood = Alert(alert_id="f", src="h:000000000001", metric=None, layer="L0", onset_ts=iso(T0), breach_ts=iso(T0),
                  last_ts=iso(T0), severity="critical", category="auth", pattern="login_flood", value=150.0,
                  risk=0.9, fail_count=750, distinct_users=40, window_s=5, layers={"L0"})
    inc, new = c.add(flood, T0)
    assert new
    for i in range(30):
        a = Alert(alert_id=f"a{i}", src=f"ip:{i:012x}", metric=None, layer="L0", onset_ts=iso(T0 + timedelta(seconds=i)),
                  breach_ts=iso(T0), last_ts=iso(T0), severity="high", category="auth", pattern="spray",
                  risk=0.8, fail_count=12, distinct_users=6, window_s=60, layers={"L0"})
        other, created = c.add(a, T0 + timedelta(seconds=i))
        assert not created and other is inc
    assert len(c.open) == 1 and len(inc.members) == 31
    ev = explain(inc, c, PLAYBOOK)
    assert ev["rule"] == "login_flood" and ev["action"]["key"] == "enable_auth_rate_limit"
    assert ev["action"]["rollback_key"] == "disable_auth_rate_limit" and ev["noise"]["raw_alerts"] == 31


# ------------------------------------------------------------------ engine end to end
def test_engine_latency_ramp_story():
    eng = TenantEngine("acme", PLAYBOOK, warmup_windows=20)
    eng.on_topology(TOPO)
    rng = np.random.default_rng(3)
    outs = []
    for i in range(80):
        rtt = 22 + rng.normal(0, 1.5) if i < 50 else 22 + (i - 50) * 6
        err = 0.004 + abs(rng.normal(0, 0.0008)) if i < 62 else 0.09
        now = T0 + timedelta(seconds=5 * i)
        outs += eng.on_metric(rec(i, LINK, "rtt", rtt), now)
        outs += eng.on_metric(rec(i, API, "error_rate", err), now)
    incidents = {o.incident.incident_id for o in outs if o.kind == "incident"}
    assert len(incidents) == 1
    last = [o for o in outs if o.kind == "incident"][-1]
    assert last.incident.root == LINK and last.evidence["rule"] == "upstream_link_degradation"
    assert eng.noise.raw_alerts >= 2 and eng.noise.incidents == 1


# ------------------------------------------------------------------ health
def test_health_states_and_reasons():
    th = merged_thresholds(THRESH, "acme")
    assert classify(45, th["cpu"]) == "excellent" and classify(95, th["cpu"]) == "poor"
    assert classify(0.97, th["up"]) == "fair" and classify(1.0, th["up"]) == "excellent"
    assert merged_thresholds(THRESH, "globex")["cpu"]["fair"] == 85
    h = SourceHealth("h:1")
    h.update("cpu", {"p95": 91.2, "avg": 50, "event_ts": "x", "unit": "%"}, th["cpu"])
    h.update("mem", {"avg": 40, "event_ts": "x"}, th["mem"])
    st, why = h.evaluate()
    assert st == "poor" and "cpu p95 91.2%" in why
    h.signals.clear()
    h.update("mem", {"avg": 40, "event_ts": "x"}, th["mem"])
    h.incident = "inc-0001"
    assert h.evaluate()[0] == "fair"
    h.incident, h.stale = None, True
    assert h.evaluate() == ("good", "stale data: no metrics for 30 s")
