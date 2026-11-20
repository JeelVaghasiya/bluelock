"""Egress guard: defence in depth on the FINAL payload, just before it is sealed.

Walks every string value of the validated payload and re-runs the secret rules plus raw
IPv4 / email detectors. Our own redaction markers are ignored. Any hit blocks the payload,
so a bug in an earlier stage still cannot leak a secret or a raw identity.
"""
from __future__ import annotations

import re

from .pseudonym import EMAIL, IPV4
from .rules import MARKER, RULES

SKIP_KEYS = {"payload_id", "finding_id", "action_id", "sent_ts", "event_ts", "collect_ts", "breach_ts",
             "config_hash", "template_id"}


def _strings(obj, key: str | None = None):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(v, k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v, key)
    elif isinstance(obj, str) and key not in SKIP_KEYS:
        yield key, obj


PLACEHOLDER = "<M>"


def scan(payload: dict) -> str | None:
    """Return the name of the first rule that fires, or None if clean."""
    for key, s in _strings(payload):
        # markers become an inert placeholder: 'token=<secret:x>' is a redacted value, not a leak
        clean = MARKER.sub(PLACEHOLDER, s)
        for rule in RULES:
            for m in rule.regex.finditer(clean):
                hit = m.group(rule.group)
                if hit.strip("\"'") == PLACEHOLDER:
                    continue
                if rule.validator is None or rule.validator(hit):
                    return f"{rule.name}@{key}"
        clean = clean.replace(PLACEHOLDER, " ")
        if IPV4.search(clean):
            return f"raw_ipv4@{key}"
        if EMAIL.search(clean):
            return f"raw_email@{key}"
    return None
