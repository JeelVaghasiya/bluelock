from workers.clock import ClockSync, shift_payload


def test_offset_is_min_skew_rounded_and_sticky():
    c = ClockSync()
    # collector 100.8 s fast: receive - attempt = -100.8 s + network delay
    for d in (35, 12, 60, 9, 22):
        off = c.observe("col", -100_800 + d)
    assert off == -101_000  # min(-100791) rounded to whole seconds
    assert c.observe("col", -100_600) == -101_000  # within hysteresis: unchanged (revisions keep their event_ts)
    assert c.observe("col", None) == -101_000  # backlog attempts carry no skew: keep the estimate


def test_synced_clock_left_alone():
    c = ClockSync()
    for d in (40, 15, 300):
        assert c.observe("col", d) == 0


def test_shift_payload_moves_every_timestamp_and_nothing_else():
    p = {"sent_ts": "2026-11-21T06:00:10.000Z", "seq": 5, "records": [
        {"type": "metric", "event_ts": "2026-11-21T06:00:00Z", "collect_ts": "2026-11-21T06:00:05.500Z", "avg": 1.0},
        {"type": "finding", "breach_ts": "2026-11-21T06:00:02.250Z", "src": "h:abc"}]}
    q = shift_payload(p, -101_000)
    assert q["sent_ts"] == "2026-11-21T05:58:29.000Z"
    assert q["records"][0]["event_ts"] == "2026-11-21T05:58:19.000Z"
    assert q["records"][0]["collect_ts"] == "2026-11-21T05:58:24.500Z" and q["records"][0]["avg"] == 1.0
    assert q["records"][1]["breach_ts"] == "2026-11-21T05:58:21.250Z" and q["records"][1]["src"] == "h:abc"
    assert p["sent_ts"] == "2026-11-21T06:00:10.000Z"  # the original payload is untouched
    assert shift_payload(p, 0) is p
