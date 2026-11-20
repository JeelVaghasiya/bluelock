"""High-entropy token detector: catches secrets no pattern knows about.

Tokens of 20+ chars from [A-Za-z0-9+/=_-] with Shannon entropy > 4.0 bits/char.
Hex digests top out at 4.0 bits/char, so commit hashes and our own HMAC codes never trigger.
"""
from __future__ import annotations

import math
import re
from collections import Counter

# '=' only as base64 padding, so 'key=value' splits into two tokens
TOKEN = re.compile(r"[A-Za-z0-9+/_-]{20,}={0,2}")
MARKER = re.compile(r"<[a-z]+:[a-z0-9_]+>")


def shannon(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def scrub(text: str, threshold: float = 4.0) -> tuple[str, int]:
    hits = 0

    def repl(m: re.Match) -> str:
        nonlocal hits
        tok = m.group(0)
        # must mix character classes to look like a credential, not a word or path
        classes = sum(bool(re.search(p, tok)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
        if classes >= 2 and shannon(tok) > threshold:
            hits += 1
            return "<secret:high_entropy>"
        return tok

    # protect existing markers
    parts = re.split(r"(<[a-z]+:[a-z0-9_]+>)", text)
    out = [p if MARKER.fullmatch(p) else TOKEN.sub(repl, p) for p in parts]
    return "".join(out), hits
