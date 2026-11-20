"""Identifier helpers shared by both tiers."""
from __future__ import annotations

import hashlib
import hmac
from uuid import UUID

import uuid6

PREFIXES = ("h", "ip", "u", "svc", "link")


def new_payload_id() -> UUID:
    """Time-ordered UUIDv7: assigned once per payload, before the first send."""
    return uuid6.uuid7()


def code(prefix: str, key: bytes, value: str) -> str:
    """Pseudonymous ID: HMAC-SHA256 with the per-tenant key, first 12 hex chars.

    Deterministic per tenant (same user -> same code) and irreversible without the key,
    which never leaves Tier 1.
    """
    if prefix not in PREFIXES:
        raise ValueError(f"unknown pseudonym prefix {prefix!r}")
    digest = hmac.new(key, value.strip().lower().encode(), hashlib.sha256).hexdigest()
    return f"{prefix}:{digest[:12]}"


def short(src: str) -> str:
    """Display form used by the UI, e.g. 'link:3fa1c09e7d22' -> 'link·3fa1'."""
    prefix, _, h = src.partition(":")
    return f"{prefix}·{h[:4]}" if h else src
