"""Dashboard WebSocket contract (frozen at G0).

Server -> client: WsEnvelope. Client -> server: Subscribe, RenderAck, Ping.
`timing` carries every hop timestamp so the dashboard can show measured latency.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WsType = Literal[
    "metric", "event", "finding", "incident", "health", "collector", "delivery",
    "remediation", "latency", "hello", "pong",
]


class Timing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_ts: str | None = None
    collect_ts: str | None = None
    sent_ts: str | None = None
    recv_ts: str | None = None
    stored_ts: str | None = None
    push_ts: str | None = None
    breach_ts: str | None = None


class WsEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1] = 1
    type: WsType
    tenant: str | None = None
    region: str | None = None
    id: str | None = None
    timing: Timing | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class Subscribe(BaseModel):
    type: Literal["subscribe"]
    topics: list[WsType]


class RenderAckItem(BaseModel):
    id: str
    kind: Literal["dash", "alert", "offline"]
    render_ts: str


class RenderAck(BaseModel):
    type: Literal["render_ack"]
    items: list[RenderAckItem]


class Ping(BaseModel):
    type: Literal["ping"]
    t0: str
