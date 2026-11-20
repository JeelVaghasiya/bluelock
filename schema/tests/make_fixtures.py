"""Generate schema/fixtures/* - deterministic, committed at G0.

Run:  python schema/tests/make_fixtures.py
"""
from __future__ import annotations

import copy
import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trustline_schema import Payload, code

FIX = Path(__file__).resolve().parent.parent / "fixtures"
KEY = b"fixture-key-acme-not-a-real-secret"
T0 = datetime(2026, 11, 21, 4, 12, 0, tzinfo=timezone.utc)
RNG = random.Random(7)


def iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


def uuid7_at(t: datetime) -> str:
    ms = int(t.timestamp() * 1000)
    rnd = RNG.getrandbits(74)
    val = (ms << 80) | (0x7 << 76) | ((rnd >> 62) << 64) | (0b10 << 62) | (rnd & ((1 << 62) - 1))
    return str(uuid.UUID(int=val))


SRC = {
    "web": code("h", KEY, "web-01"),
    "db": code("h", KEY, "db-01"),
    "uplink": code("link", KEY, "uplink-1"),
    "api": code("svc", KEY, "api"),
    "attacker": code("ip", KEY, "203.0.113.7"),
    "alice": code("u", KEY, "alice@acme.test"),
}


def metric(t: datetime, src: str, name: str, base: float, unit: str) -> dict:
    vals = sorted(base + RNG.gauss(0, base * 0.05 + 0.1) for _ in range(5))
    return {
        "type": "metric", "event_ts": iso(t), "collect_ts": iso(t + timedelta(seconds=5.5)),
        "win_s": 5, "rev": 0, "n": 5, "src_tz": "+05:30", "src": src, "metric": name,
        "min": round(vals[0], 3), "avg": round(sum(vals) / 5, 3), "p50": round(vals[2], 3),
        "p95": round(vals[4], 3), "max": round(vals[4], 3), "unit": unit,
    }


def valid_payload() -> dict:
    return {
        "schema": "trustline.v1",
        "payload_id": uuid7_at(T0 + timedelta(seconds=10)),
        "collector_id": "col-ac01",
        "seq": 18234,
        "region": "ap-south",
        "sent_ts": iso(T0 + timedelta(seconds=10, milliseconds=4)),
        "redactions": 1,
        "redactions_by_rule": {"github_token": 1},
        "records": [
            metric(T0, SRC["web"], "cpu", 48.0, "%"),
            {
                "type": "finding", "finding_id": str(uuid.UUID(int=RNG.getrandbits(128), version=4)),
                "event_ts": iso(T0 + timedelta(seconds=5)), "breach_ts": iso(T0 + timedelta(seconds=2)),
                "collect_ts": iso(T0 + timedelta(seconds=5, milliseconds=300)), "src_tz": "+05:30",
                "src": SRC["attacker"], "category": "auth", "pattern": "spray", "severity": "high",
                "risk": 0.92, "layer": "L0", "fail_count": 37, "distinct_users": 14, "window_s": 60,
            },
            {
                "type": "event", "event_ts": iso(T0), "collect_ts": iso(T0 + timedelta(seconds=5.5)),
                "win_s": 5, "src_tz": "+05:30", "src": SRC["api"], "category": "app", "level": "error",
                "template_id": "5b1f0e2a9c44",
                "template": "POST /login token=<secret:github_token> user <u:%s>" % SRC["alice"][2:],
                "count": 1, "redacted": 1,
            },
            {
                "type": "heartbeat", "event_ts": iso(T0 + timedelta(seconds=10)), "src_tz": "+05:30",
                "version": "0.3.1", "config_hash": "a41c00ff119e", "cpu": 3.1, "mem_mb": 182.0,
                "buffer_depth": 0, "buffer_bytes": 0, "last_acked_seq": 18233, "last_assigned_seq": 18234,
                "redactions_total": 1284, "blocked_total": 2, "dup_source_events": 0,
                "late_beyond_horizon": 0, "uptime_s": 3600, "saas_reachable": True,
            },
        ],
    }


def write(name: str, obj) -> None:
    (FIX / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    FIX.mkdir(exist_ok=True)
    good = valid_payload()
    Payload.model_validate(good)  # sanity
    write("payload_valid.json", good)

    bad = copy.deepcopy(good)
    bad["records"][0]["raw_msg"] = "Failed password for alice from 203.0.113.7"
    write("payload_extra_field.json", bad)

    bad = copy.deepcopy(good)
    bad["records"][0]["src"] = "web-01.acme.internal"
    write("payload_raw_hostname.json", bad)

    bad = copy.deepcopy(good)
    bad["tenant_id"] = "globex"
    write("payload_tenant_spoof.json", bad)

    bad = copy.deepcopy(good)
    bad["records"][0]["event_ts"] = "2026-11-21T09:42:00+05:30"
    write("payload_non_utc.json", bad)

    # 600-record stream (120 windows x 5 metrics) for AIOps unit tests; latency ramp from window 80
    lines = []
    for w in range(120):
        t = T0 + timedelta(seconds=5 * w)
        rtt = 22.0 if w < 80 else 22.0 + (w - 80) * 4.5
        err = 0.004 if w < 98 else 0.09
        for rec in (
            metric(t, SRC["web"], "cpu", 45.0, "%"),
            metric(t, SRC["db"], "mem", 61.0, "%"),
            metric(t, SRC["uplink"], "rtt", rtt, "ms"),
            metric(t, SRC["uplink"], "loss", 0.2, "%"),
            metric(t, SRC["api"], "error_rate", err, "ratio"),
        ):
            lines.append(json.dumps(rec))
    (FIX / "payload_stream.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    ws = [
        {"v": 1, "type": "metric", "tenant": "acme", "region": "ap-south", "id": "m1",
         "timing": {"event_ts": iso(T0), "collect_ts": iso(T0 + timedelta(seconds=5.5)),
                    "recv_ts": iso(T0 + timedelta(seconds=6.4)), "stored_ts": iso(T0 + timedelta(seconds=6.5)),
                    "push_ts": iso(T0 + timedelta(seconds=6.52))},
         "data": metric(T0, SRC["web"], "cpu", 48.0, "%")},
        {"v": 1, "type": "health", "tenant": "acme", "region": "ap-south", "id": SRC["uplink"],
         "data": {"src": SRC["uplink"], "kind": "link", "role": "uplink", "state": "poor", "stale": False,
                  "old_state": "fair", "reason": "rtt p95 212ms >= 200 (poor)", "since": iso(T0)}},
        {"v": 1, "type": "collector", "tenant": "acme", "region": "ap-south", "id": "col-ac01",
         "data": {"collector_id": "col-ac01", "state": "online", "buffer_depth": 0, "last_acked_seq": 18234,
                  "version": "0.3.1", "skew_ms": 12}},
    ]
    (FIX / "ws_samples.jsonl").write_text("\n".join(json.dumps(x) for x in ws) + "\n", encoding="utf-8")

    evidence = {
        "incident": "inc-0412", "priority": "P2", "root_cause": SRC["uplink"],
        "affected": [SRC["api"], SRC["web"]],
        "signals": [
            {"src": SRC["uplink"], "metric": "rtt", "stat": "p95", "value": 184, "baseline": 22, "z": 7.4,
             "weight": 0.61, "layer": "L1", "onset_ts": iso(T0)},
            {"src": SRC["api"], "metric": "error_rate", "stat": "avg", "value": 0.09, "baseline": 0.004,
             "z": 4.8, "weight": 0.39, "layer": "L1", "onset_ts": iso(T0 + timedelta(seconds=94))},
        ],
        "layers_agreeing": ["L1", "L2", "L3"], "persistence": 1.0, "rule": "upstream_link_degradation",
        "confidence": 0.87, "confidence_parts": {"magnitude": 0.46, "agreement": 0.30, "persistence": 0.20},
        "action": {"key": "failover_uplink", "text": "Check link link·3fa1 or fail over to the secondary uplink",
                   "reversible": True, "rollback_key": "failback_uplink", "requires_role": "tenant-admin"},
        "why": "Link link·3fa1 rtt p95 rose 94 s before 2 dependent components degraded",
        "trace": [
            {"step": "detect", "layer": "L1", "detail": "z=7.4 in 3/3 windows"},
            {"step": "corroborate", "layer": "L2", "detail": "HST score 0.93 > p99 0.81"},
            {"step": "correlate", "detail": "graph distance <=2 within +/-120 s", "members": 3},
            {"step": "root_cause", "chosen": SRC["uplink"],
             "rejected": [{"src": SRC["api"], "why": "depends on link; onset +94 s"}]},
            {"step": "playbook", "rule": "upstream_link_degradation",
             "matched": ["root_kind=link", "root_metric=rtt", "impacted=error_rate"]},
            {"step": "policy", "detail": "reversible, blast radius 3 <= 6, requires tenant-admin"},
        ],
        "suppressed": [{"alert_id": "a-7", "src": SRC["web"], "metric": "rtt", "reason": "absorbed into inc-0412"}],
        "noise": {"raw_alerts": 7, "incidents": 1},
    }
    write("incident_evidence.json", evidence)
    print(f"fixtures written to {FIX}")


if __name__ == "__main__":
    main()
