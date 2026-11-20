"""Turn a redacted log line into a stable, safe template (log-pattern grouping)."""
from __future__ import annotations

import hashlib
import re

MARKER_SPLIT = re.compile(r"(<[a-z]+(?::[a-z0-9_]+)?>)")
UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}\b")
NUM = re.compile(r"(?<![A-Za-z\d])\d+(?:\.\d+)?")  # '5000ms' -> '<num>ms', 'ssh2' untouched
UNSAFE = re.compile(r"""[^A-Za-z0-9 _.,:;=/()\[\]<>'"#@+*%!?|-]""")
MAX_LEN = 160


def make(text: str) -> tuple[str, str]:
    """Return (template, template_id)."""
    parts = MARKER_SPLIT.split(text)
    out = []
    for p in parts:
        if MARKER_SPLIT.fullmatch(p):
            out.append(p)
            continue
        p = UUID.sub("<uuid>", p)
        p = HEX.sub("<hex>", p)
        p = NUM.sub("<num>", p)
        p = UNSAFE.sub(" ", p)
        out.append(p)
    t = re.sub(r"\s+", " ", "".join(out)).strip()
    if len(t) > MAX_LEN:
        t = t[: MAX_LEN - 3]
        # never cut a marker in half
        cut = t.rfind("<")
        if cut > t.rfind(">"):
            t = t[:cut]
        t = t.rstrip() + "..."
    tid = hashlib.sha256(t.encode()).hexdigest()[:12]
    return t, tid
