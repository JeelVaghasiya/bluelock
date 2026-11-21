"""Data verification and redundancy removal: what comes in vs what has to leave the site."""
import asyncio
import json
import math

from collector.models import CleanEvent, Counters, RawEvent
from collector.stages.s2_filter import Filter
from collector.stages.s4_aggregate import KEEPALIVE_S, WindowAggregator

T0 = 1_800_000_000.0


def raw(d: dict, eid: str | None = None, collect=T0) -> RawEvent:
    body = json.dumps(d)
    return RawEvent(source="sim", body=body, collect_ts=collect, data=d, eid=eid)


def run_filter(events: list[RawEvent]) -> tuple[list, Counters]:
    async def go():
        c = Counters()
        q_in, q_out = asyncio.Queue(), asyncio.Queue()
        f = Filter(q_in, q_out, c, "+05:30")
        for ev in events:
            q_in.put_nowait(ev)
        task = asyncio.create_task(f.run())
        while not q_in.empty():
            await asyncio.sleep(0)
        await asyncio.sleep(0.01)
        task.cancel()
        out = []
        while not q_out.empty():
            out.append(q_out.get_nowait())
        return out, c
    return asyncio.run(go())


def metric(host=None, device=None, service=None, name="cpu", value=50.0, ts="2027-01-15T08:00:00.000Z"):
    d = {"kind": "metric", "name": name, "value": value, "ts": ts}
    d.update({k: v for k, v in (("host", host), ("device", device), ("service", service)) if v})
    return d


def test_verification_drops_impossible_values_by_reason():
    evs = [raw(metric(host="a", value=42.0), "1"),
           raw(metric(host="a", name="cpu", value=180.0), "2"),          # % above 100
           raw(metric(host="a", name="apdex", value=-0.2), "3"),          # ratio below 0
           raw(metric(device="san-01", name="iops", value=-5), "4"),      # negative rate
           raw(metric(host="a", name="mem", value=math.nan), "5"),        # NaN
           raw(metric(host="a", name="load", value=3.7), "6"),            # load average may exceed 1
           raw(metric(host="a", value=40.0, ts="2099-01-01T00:00:00.000Z"), "7")]  # broken clock
    out, c = run_filter(evs)
    assert [p.value for p in out] == [42.0, 3.7]
    assert c.dropped_invalid == 5
    assert c.invalid_by_reason == {"above_range": 1, "negative": 2, "non_finite": 1, "future_ts": 1}


def test_same_reading_from_two_agents_is_counted_once():
    d = metric(host="web-01", value=37.5)
    out, c = run_filter([raw(d, "agent-a-1"), raw({**d, "agent": "node-exporter"}, "agent-b-1"),  # double scrape
                         raw(d, "agent-a-1"),                                                    # re-sent delivery
                         raw(metric(host="web-01", value=37.6), "agent-a-2")])                  # a real new reading
    assert len(out) == 2 and c.dup_samples == 1 and c.dup_source_events == 1 and c.samples_in == 2


def test_devices_get_their_own_prefix_and_authoritative_unit():
    out, _ = run_filter([raw({**metric(device="core-sw-01", name="if_errors", value=3.0), "unit": "%"}, "x")])
    assert out[0].entity_prefix == "dev" and out[0].entity == "core-sw-01" and out[0].unit == "ops/s"


def sample(ts, v, src="dev:9c1e4a7b20f1", metric="mem", unit="%"):
    return CleanEvent(rid=0, kind="sample", event_ts=ts, collect_ts=ts, src=src, metric=metric, value=v, unit=unit)


def test_flat_series_suppressed_with_keepalive_but_changes_sent_at_once():
    c = Counters()
    agg = WindowAggregator("+05:30", counters=c)
    sent = []
    for w in range(12):  # 60 s of a flat 41 % series, then a real change
        start = T0 + 5 * w
        v = 41.0 if w < 10 else 55.0
        for i in range(5):
            agg.add(sample(start + i, v), start + i)
        sent += [(w, r["avg"]) for r in agg.close_due(start + 5.6) if r["metric"] == "mem"]
    windows = [w for w, _ in sent]
    assert windows[0] == 0 and 10 in windows  # first window and the change both go out immediately
    assert any(0 < w < 10 for w in windows)  # a keep-alive inside the flat stretch
    gaps = [b - a for a, b in zip(windows, windows[1:]) if b < 10]
    assert all(g * 5 <= KEEPALIVE_S + 5 for g in gaps)  # never silent longer than the keep-alive
    assert c.suppressed_unchanged == 12 - len(sent) and c.windows_out == 12


def test_noisy_series_never_suppressed():
    import numpy as np
    rng = np.random.default_rng(0)
    c = Counters()
    agg = WindowAggregator("+05:30", counters=c)
    n = 0
    for w in range(10):
        start = T0 + 5 * w
        for i in range(5):
            agg.add(sample(start + i, 40 + rng.normal(0, 3), metric="cpu"), start + i)
        n += len(agg.close_due(start + 5.6))
    assert n == 10 and c.suppressed_unchanged == 0
