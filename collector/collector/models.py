"""In-process event types flowing between the seven stages.

RawEvent (stage 1) -> ParsedEvent (2) -> CleanEvent (3) -> records dict (4/5) -> Payload (6) -> bytes (7)
Only CleanEvent and later carry no secrets and no identities.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class RawEvent:
    source: str  # sim | syslog | organiser
    body: str
    collect_ts: float
    data: dict | None = None  # parsed JSON when the source is structured
    eid: str | None = None
    rid: int = 0  # raw.db id


@dataclass(slots=True)
class ParsedEvent:
    rid: int
    kind: Literal["sample", "log", "auth"]
    event_ts: float
    collect_ts: float
    entity_prefix: str = "h"  # h | svc | link
    entity: str = ""
    metric: str | None = None
    value: float | None = None
    unit: str | None = None
    level: str = "info"
    category: str = "app"
    msg: str = ""
    auth_user: str | None = None
    auth_ip: str | None = None
    auth_outcome: Literal["fail", "success"] | None = None


@dataclass(slots=True)
class CleanEvent:
    rid: int
    kind: Literal["sample", "log", "auth"]
    event_ts: float
    collect_ts: float
    src: str  # HMAC code, e.g. h:9c1e4a7b20f1
    metric: str | None = None
    value: float | None = None
    unit: str | None = None
    level: str = "info"
    category: str = "app"
    template: str | None = None
    template_id: str | None = None
    redacted: int = 0
    by_rule: dict[str, int] = field(default_factory=dict)
    user_code: str | None = None
    ip_code: str | None = None
    auth_outcome: str | None = None


@dataclass
class Counters:
    raw_in: int = 0
    parsed: int = 0
    dropped_noise: int = 0
    dropped_unknown: int = 0
    dup_source_events: int = 0
    # data volume + redundancy accounting (what came in vs what had to leave)
    samples_in: int = 0  # valid, unique metric samples accepted
    dup_samples: int = 0  # same reading delivered again under a different event id (e.g. two agents)
    dropped_invalid: int = 0  # failed verification (non-finite, out of physical range, future timestamp)
    invalid_by_reason: dict[str, int] = field(default_factory=dict)
    windows_out: int = 0  # metric window records produced by aggregation
    suppressed_unchanged: int = 0  # windows identical to the last one sent (kept on site, keep-alive every 25 s)
    findings_aggregated: int = 0  # per-IP auth findings folded into a login_flood finding
    flood_ips: int = 0  # distinct attacking IPs in the last login_flood (stays on site)
    redactions_total: int = 0
    redactions_by_rule: dict[str, int] = field(default_factory=dict)
    identities_coded: int = 0
    late_revisions: int = 0
    late_beyond_horizon: int = 0
    findings: int = 0
    records_out: int = 0
    payloads_sealed: int = 0
    blocked_total: int = 0
    acked: int = 0
    duplicates_acked: int = 0
    rejected: int = 0
    send_failures: int = 0

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)
