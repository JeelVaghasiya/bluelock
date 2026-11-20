"""Live-update publishing over core NATS: subject live.<tenant>.<kind>."""
from __future__ import annotations

from typing import Any

import orjson

from .config import iso, now


def envelope(kind: str, tenant: str, region: str, data: dict, *, id: str | None = None,
             timing: dict | None = None) -> bytes:
    t = dict(timing or {})
    t["push_ts"] = iso(now())
    return orjson.dumps({"v": 1, "type": kind, "tenant": tenant, "region": region, "id": id,
                         "timing": t, "data": data}, default=_default)


def _default(o: Any):
    if hasattr(o, "isoformat"):
        return iso(o)
    if hasattr(o, "hex"):
        return str(o)
    raise TypeError(type(o))


async def publish(nc, kind: str, tenant: str, region: str, data: dict, **kw) -> None:
    if nc is None or not nc.is_connected:
        return
    await nc.publish(f"live.{tenant}.{kind}", envelope(kind, tenant, region, data, **kw))
