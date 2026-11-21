"""Secret-scrubbing rules (gitleaks-derived patterns + validators).

Each match is replaced with ``<secret:RULE>``. Order matters: specific rules run before
generic key=value rules so the label is as precise as possible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


def luhn_ok(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class Rule:
    name: str
    regex: re.Pattern
    validator: Callable[[str], bool] | None = None
    group: int = 0  # which group is the secret (0 = whole match)


RULES: list[Rule] = [
    Rule("pem_private_key", re.compile(
        r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z ]+ )?PRIVATE KEY-----", re.S)),
    Rule("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b")),
    Rule("aws_secret", re.compile(r"(?i)aws_?secret_?access_?key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})"), group=1),
    Rule("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    Rule("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    Rule("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    Rule("jwt", re.compile(r"\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}")),
    Rule("bearer", re.compile(r"(?i)\bauthorization:\s*bearer\s+(\S+)"), group=1),
    Rule("url_credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^/\s@]+)@"), group=1),
    Rule("card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), validator=luhn_ok),
    Rule("password_kv", re.compile(
        r"(?i)\b(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)\s*[=:]\s*(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"),
        group=1),
]

MARKER = re.compile(r"<(?:secret|u|ip|h|svc|link|dev|person):[a-z0-9_]+>|<(?:num|hex|uuid|path|person)>")


def scrub(text: str) -> tuple[str, dict[str, int], list[tuple[int, int, str]]]:
    """Return (clean_text, counts_by_rule, spans_in_ORIGINAL_text)."""
    counts: dict[str, int] = {}
    spans: list[tuple[int, int, str]] = []
    # Find spans on the original text first so the UI can highlight them.
    taken: list[tuple[int, int]] = []
    for rule in RULES:
        for m in rule.regex.finditer(text):
            s, e = m.span(rule.group)
            if s < 0:
                continue
            if rule.validator and not rule.validator(m.group(rule.group)):
                continue
            if any(not (e <= a or s >= b) for a, b in taken):
                continue
            # don't re-redact our own markers
            if MARKER.fullmatch(text[s:e]):
                continue
            taken.append((s, e))
            spans.append((s, e, rule.name))
            counts[rule.name] = counts.get(rule.name, 0) + 1
    if not spans:
        return text, counts, spans
    out, last = [], 0
    for s, e, name in sorted(spans):
        out.append(text[last:s])
        out.append(f"<secret:{name}>")
        last = e
    out.append(text[last:])
    return "".join(out), counts, sorted(spans)
