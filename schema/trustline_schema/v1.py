"""trustline.v1 - the ONLY shape of data allowed to cross the trust boundary (hop H2).

Default-deny: every model forbids unknown fields, every identity field accepts only a
12-hex HMAC code, and free text is restricted to a length-capped safe character class.
The same models are imported by the collector (stage 6 policy) and by ingest-api, so
both ends enforce the identical allow-list.

Serialise ALWAYS with ``model_dump_json(by_alias=True)``. ``tenant_id`` is deliberately
NOT a field: the SaaS takes the tenant from the verified client certificate, and a body
that claims a tenant is rejected.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

SCHEMA = "trustline.v1"
MAX_RECORDS = 500


def _utc(v: datetime) -> datetime:
    if v.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return v


def _v7(u: UUID) -> UUID:
    if u.version != 7:
        raise ValueError("payload_id must be UUIDv7")
    return u


UtcTs = Annotated[AwareDatetime, AfterValidator(_utc)]
UUID7 = Annotated[UUID, AfterValidator(_v7)]
Hex12 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{12}$")]
SrcId = Annotated[str, StringConstraints(pattern=r"^(h|ip|u|svc|link):[0-9a-f]{12}$")]
TzOffset = Annotated[str, StringConstraints(pattern=r"^[+-]\d{2}:\d{2}$")]
CollectorId = Annotated[str, StringConstraints(pattern=r"^col-[0-9a-f]{4,12}$")]
RuleName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{2,32}$")]
SafeText = Annotated[
    str,
    StringConstraints(max_length=160, pattern=r"^[A-Za-z0-9 _.,:;=/()\[\]<>'\"#@+*%!?|-]*$"),
]
NonNeg = Annotated[int, Field(ge=0)]
Region = Literal["ap-south", "eu-west"]
MetricName = Literal[
    "cpu", "mem", "disk", "disk_hours_to_full", "net_in", "net_out", "net_util",
    "rtt", "jitter", "loss", "up", "error_rate",
]
Unit = Literal["%", "ms", "B/s", "ratio", "h"]
Severity = Literal["info", "low", "medium", "high", "critical"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class MetricRecord(_Strict):
    type: Literal["metric"]
    event_ts: UtcTs  # window start (= oldest possible sample)
    collect_ts: UtcTs  # window emitted at collector
    win_s: Literal[1, 5, 60]
    rev: Annotated[int, Field(ge=0, le=50)] = 0
    n: Annotated[int, Field(ge=1)]
    src_tz: TzOffset
    src: SrcId
    metric: MetricName
    min: float
    avg: float
    p95: float
    max: float
    p50: float | None = None
    unit: Unit


class FindingRecord(_Strict):
    type: Literal["finding"]
    finding_id: UUID
    event_ts: UtcTs
    breach_ts: UtcTs  # first offending raw sample -> alert-latency origin
    collect_ts: UtcTs
    src_tz: TzOffset
    src: SrcId
    category: Literal["auth", "threshold", "anomaly", "connectivity"]
    pattern: Literal[
        "brute_force", "spray", "success_after_failures",
        "threshold_breach", "edge_anomaly", "link_down",
    ]
    severity: Severity
    risk: Annotated[float, Field(ge=0, le=1)]
    layer: Literal["L0", "T"]
    metric: MetricName | None = None
    value: float | None = None
    threshold: float | None = None
    baseline: float | None = None
    z: float | None = None
    fail_count: NonNeg | None = None
    distinct_users: NonNeg | None = None
    window_s: Annotated[int, Field(ge=1, le=3600)] | None = None


class EventRecord(_Strict):
    type: Literal["event"]
    event_ts: UtcTs
    collect_ts: UtcTs
    win_s: Literal[5, 60]
    src_tz: TzOffset
    src: SrcId
    category: Literal["auth", "system", "network", "app", "security"]
    level: Literal["debug", "info", "warn", "error", "critical"]
    template_id: Hex12
    # e.g. "Failed password for <u:4be1c02a9f10> from <ip:3fa1c09e7d22> port <num>"
    template: SafeText
    count: Annotated[int, Field(ge=1)]
    redacted: NonNeg


class HeartbeatRecord(_Strict):
    type: Literal["heartbeat"]
    event_ts: UtcTs
    src_tz: TzOffset
    version: Annotated[str, StringConstraints(pattern=r"^\d+\.\d+\.\d+$")]
    config_hash: Hex12
    cpu: Annotated[float, Field(ge=0)]
    mem_mb: Annotated[float, Field(ge=0)]
    buffer_depth: NonNeg
    buffer_bytes: NonNeg
    last_acked_seq: NonNeg
    last_assigned_seq: NonNeg
    redactions_total: NonNeg
    blocked_total: NonNeg
    dup_source_events: NonNeg
    late_beyond_horizon: NonNeg
    uptime_s: NonNeg
    saas_reachable: bool


class TopoNode(_Strict):
    src: SrcId
    kind: Literal["host", "svc", "link"]
    role: Literal["web", "api", "db", "gateway", "uplink", "worker", "other"]


class TopoEdge(_Strict):
    dependent: SrcId
    dependency: SrcId
    kind: Literal["depends_on", "runs_on", "routes_via"]


class TopologyRecord(_Strict):
    type: Literal["topology"]
    event_ts: UtcTs
    nodes: Annotated[list[TopoNode], Field(max_length=500)]
    edges: Annotated[list[TopoEdge], Field(max_length=2000)]


class ActionResultRecord(_Strict):
    type: Literal["action_result"]
    event_ts: UtcTs
    action_id: UUID
    status: Literal["executed", "failed", "rolled_back", "refused_local_policy"]
    code: RuleName


Record = Annotated[
    Union[
        MetricRecord, FindingRecord, EventRecord, HeartbeatRecord,
        TopologyRecord, ActionResultRecord,
    ],
    Field(discriminator="type"),
]


class Payload(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, populate_by_name=True
    )
    # 'schema' shadows a BaseModel attribute -> use an alias on the wire
    schema_: Literal["trustline.v1"] = Field(default=SCHEMA, alias="schema")
    payload_id: UUID7
    collector_id: CollectorId
    seq: Annotated[int, Field(ge=1)]
    region: Region
    sent_ts: UtcTs  # sealed time; immutable across retries
    redactions: NonNeg
    redactions_by_rule: dict[RuleName, NonNeg] = Field(default_factory=dict)
    records: Annotated[list[Record], Field(min_length=1, max_length=MAX_RECORDS)]

    def to_json_bytes(self) -> bytes:
        return self.model_dump_json(by_alias=True).encode()
