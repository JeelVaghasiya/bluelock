import asyncio
import gzip
import hashlib
import json
import random

import pytest
from trustline_schema import Payload

from collector.models import CleanEvent, Counters
from collector.stages.s4_aggregate import WindowAggregator
from collector.stages.s5_analyse import FastPath, SecurityL0
from collector.stages.s6_policy import Policy
from collector.stages.s7_transmit import Transmit
from collector.stores import open_stores

SRC = "h:9c1e4a7b20f1"
T0 = 1_800_000_000.0  # divisible by 5


def sample(ts, v, src=SRC, metric="cpu"):
    return CleanEvent(rid=0, kind="sample", event_ts=ts, collect_ts=ts, src=src, metric=metric, value=v, unit="%")


# ------------------------------------------------------------------ aggregate
def test_window_stats_order_independent():
    vals = [10, 20, 30, 40, 50]
    a, b = WindowAggregator("+05:30"), WindowAggregator("+05:30")
    for i, v in enumerate(vals):
        a.add(sample(T0 + i, v), T0 + i)
    shuffled = list(enumerate(vals))
    random.Random(1).shuffle(shuffled)
    for i, v in shuffled:
        b.add(sample(T0 + i, v), T0 + 4)
    ra, rb = a.close_due(T0 + 6)[0], b.close_due(T0 + 6)[0]
    for k in ("min", "avg", "p95", "max", "n"):
        assert ra[k] == rb[k]
    assert ra["avg"] == 30 and ra["event_ts"].endswith("Z") and ra["rev"] == 0


def test_stalled_stream_closes_on_wall_clock():
    a = WindowAggregator("+05:30")
    a.add(sample(T0, 5), T0)
    assert a.close_due(T0 + 5.2) == []
    assert len(a.close_due(T0 + 5.6)) == 1


def test_late_sample_reemits_revision_and_beyond_horizon_counted():
    c = Counters()
    a = WindowAggregator("+05:30", counters=c)
    a.add(sample(T0, 10), T0)
    a.close_due(T0 + 6)
    a.add(sample(T0 + 1, 90), T0 + 30)  # 25 s late -> revision
    out = a.close_due(T0 + 30.1)
    assert out[0]["rev"] == 1 and out[0]["max"] == 90 and out[0]["n"] == 2
    a.add(sample(T0 + 2, 50), T0 + 200)  # beyond 120 s horizon
    assert c.late_beyond_horizon == 1


def test_link_rtt_yields_jitter():
    a = WindowAggregator("+05:30")
    for i, v in enumerate([20, 24, 19, 25, 21]):
        a.add(sample(T0 + i, v, src="link:3fa1c09e7d22", metric="rtt"), T0 + i)
    out = {r["metric"]: r for r in a.close_due(T0 + 6)}
    assert out["jitter"]["avg"] == pytest.approx((4 + 5 + 6 + 4) / 4)


# ------------------------------------------------------------------ analyse
def test_fast_path_three_consecutive_poor_samples():
    th = {"stat": "p95", "excellent": 60, "good": 80, "fair": 90}
    fp = FastPath(lambda m: th)
    res = [fp.check(sample(T0 + i, v), "+05:30") for i, v in enumerate([95, 96, 97, 98])]
    assert res[0] is None and res[1] is None and res[2]["pattern"] == "threshold_breach"
    assert res[2]["breach_ts"].startswith("2027-01-15T08:00:00") and res[3] is None  # not re-armed


def auth(ts, user, ip="ip:3fa1c09e7d22", outcome="fail"):
    return CleanEvent(rid=1, kind="auth", event_ts=ts, collect_ts=ts, src="h:000000000001",
                      user_code=user, ip_code=ip, auth_outcome=outcome)


def test_l0_spray_brute_and_success_after_failures():
    sec = SecurityL0()
    spray = [sec.observe(auth(T0 + i, f"u:{i:012x}"), "+05:30") for i in range(6)]
    f = next(x for x in spray if x)
    assert f["pattern"] == "spray" and f["severity"] == "high" and 0 <= f["risk"] <= 1

    sec = SecurityL0()
    brute = [sec.observe(auth(T0 + i, "u:000000000aaa", ip="ip:0000000000bb"), "+05:30") for i in range(10)]
    assert brute[-1]["pattern"] == "brute_force"

    sec = SecurityL0(brute=100)
    for i in range(6):
        sec.observe(auth(T0 + i, "u:000000000ccc", ip="ip:0000000000cc"), "+05:30")
    f = sec.observe(auth(T0 + 7, "u:000000000ccc", ip="ip:0000000000cc", outcome="success"), "+05:30")
    assert f["pattern"] == "success_after_failures" and f["severity"] == "critical"


# ------------------------------------------------------------------ policy + transmit + egress
@pytest.fixture
def stack(tmp_path):
    stores = open_stores(tmp_path)
    c = Counters()
    tx = Transmit(stores, c, ingest_base="https://127.0.0.1:1", ca_file="", cert=("", ""))
    pol = Policy(asyncio.Queue(), tx, stores, c, collector_id="col-ac01", region="ap-south", batch_ms=1000)
    return stores, c, tx, pol


def metric_rec():
    a = WindowAggregator("+05:30")
    a.add(sample(T0, 42), T0)
    return a.close_due(T0 + 6)[0]


def test_policy_seals_valid_and_blocks_extra_field_without_consuming_seq(stack):
    stores, c, tx, pol = stack
    asyncio.run(pol.flush([metric_rec()]))
    assert stores.state.peek_seq() == 1
    pol.leak_next = True
    asyncio.run(pol.flush([metric_rec()]))
    assert stores.state.peek_seq() == 1 and c.blocked_total == 1
    blocked = stores.egress.list(status="blocked")[0]
    assert blocked["detail"].startswith("extra_forbidden") and "raw_msg" in blocked["detail"]


def test_policy_blocks_raw_hostname_in_src(stack):
    stores, c, _, pol = stack
    bad = {**metric_rec(), "src": "web-01.acme.internal"}
    asyncio.run(pol.flush([bad]))
    assert c.blocked_total == 1 and stores.state.peek_seq() == 0


def test_egress_guard_blocks_secret_that_slipped_into_template(stack):
    stores, c, _, pol = stack
    ev = {"type": "event", "event_ts": "2027-01-15T08:00:00Z", "collect_ts": "2027-01-15T08:00:05Z", "win_s": 5,
          "src_tz": "+05:30", "src": SRC, "category": "app", "level": "error", "template_id": "0123456789ab",
          "template": "leaked AKIAIOSFODNN7EXAMPLE here", "count": 1, "redacted": 0}
    asyncio.run(pol.flush([ev]))
    assert c.blocked_total == 1
    assert stores.egress.list(status="blocked")[0]["detail"].startswith("egress_guard:aws_access_key")


def test_sealed_body_is_gzip_of_exact_json_and_retry_identical(stack):
    stores, _, _, pol = stack
    asyncio.run(pol.flush([metric_rec()]))
    row = stores.outbox.oldest_pending()
    eg = stores.egress.list(status="sealed")[0]
    assert hashlib.sha256(row["body"]).hexdigest() == row["sha256"] == eg["sha256"]
    sealed = stores.egress.by_payload(row["payload_id"])[0]
    assert gzip.decompress(row["body"]).decode() == sealed["json_text"]
    Payload.model_validate_json(sealed["json_text"])
    # the stored bytes ARE the retry: reading again yields identical bytes
    assert stores.outbox.oldest_pending()["body"] == row["body"]
    assert gzip.compress(sealed["json_text"].encode(), mtime=0) == row["body"]


def test_egress_chain_verifies_and_detects_tampering(stack):
    stores, _, _, pol = stack
    for _ in range(3):
        asyncio.run(pol.flush([metric_rec()]))
    assert stores.egress.verify()["ok"]
    victim = stores.egress.list()[1]["id"]
    stores.core.execute("UPDATE egress SET detail='tampered' WHERE id=?", (victim,))
    v = stores.egress.verify()
    assert not v["ok"] and v["broken_at"] == victim


def test_outbox_failure_backoff_then_backlog_order(stack):
    stores, c, tx, pol = stack
    for _ in range(3):
        asyncio.run(pol.flush([metric_rec()]))
    rows = stores.outbox.backlog(now=10**10, limit=10, live_age=-10**10)
    assert [r["seq"] for r in rows] == [1, 2, 3]
    tx.fail_streak = 3
    waits = [tx._backoff() for _ in range(50)]
    assert all(4 <= w <= 12 for w in waits)
    json.dumps(c.as_dict())
