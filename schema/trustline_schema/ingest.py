"""POST /v1/ingest contract (frozen at G0).

Wire: Content-Type application/vnd.trustline.v1+json, Content-Encoding gzip (mtime=0).
The gzip bytes are stored once in the collector outbox and resent byte-identical;
sha256 is computed over those gzip bytes on both sides.
Per-attempt headers: X-TL-Attempt, X-TL-Attempt-Ts, X-TL-Lane (live|backlog).
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .v1 import UtcTs

CONTENT_TYPE = "application/vnd.trustline.v1+json"
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_INFLATED_BYTES = 20 * 1024 * 1024

ErrorCode = Literal[
    "bad_encoding", "schema_rejected", "residency_denied", "identity_mismatch",
    "revoked", "too_large", "queue_unavailable", "unauthenticated",
]

# Collector retry policy: retry these, mark any other 4xx as rejected.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class IngestAck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload_id: UUID
    status: Literal["stored", "duplicate"]
    seq: int
    recv_ts: UtcTs


class IngestError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: ErrorCode
    detail: str = ""
