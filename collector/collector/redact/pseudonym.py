"""Pseudonymous identities: HMAC-SHA256 with the per-tenant key, first 12 hex chars.

The key and the reverse map (code -> real name) live only in Tier 1 (state.db), so the
SaaS can correlate 'same user, same code' but can never recover who it is.
"""
from __future__ import annotations

import re
import threading

from trustline_schema import code as hmac_code

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4 = re.compile(r"\b(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}\b")
IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){3,7}[0-9a-fA-F]{1,4}\b")
USER_KV = re.compile(r"(?i)\b(user(?:name)?|uid|login|account)=([^\s,;&]+)")
SSH_USER = re.compile(r"\bfor (invalid user )?(\S+) from\b")
FQDN = re.compile(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:internal|local|lan|corp|test)\b", re.I)


class Pseudonymizer:
    def __init__(self, key: bytes, known_hosts: dict[str, str] | None = None, on_new=None):
        self.key = key
        self.known: dict[str, str] = {}  # real lowercase name -> prefix
        for name, prefix in (known_hosts or {}).items():
            self.known[name.lower()] = prefix
        self._host_re = self._build_host_re()
        self.reverse: dict[str, str] = {}
        self._lock = threading.Lock()
        self._on_new = on_new  # callback(code, real, prefix) to persist reverse map

    def _build_host_re(self) -> re.Pattern | None:
        names = sorted(self.known, key=len, reverse=True)
        if not names:
            return None
        return re.compile(r"(?<![\w.-])(" + "|".join(re.escape(n) for n in names) + r")(?![\w-])", re.I)

    def code(self, prefix: str, value: str) -> str:
        c = hmac_code(prefix, self.key, value)
        if c not in self.reverse:
            with self._lock:
                self.reverse[c] = value
            if self._on_new:
                self._on_new(c, value, prefix)
        return c

    def marker(self, prefix: str, value: str) -> str:
        return "<" + self.code(prefix, value) + ">"

    def apply(self, text: str) -> tuple[str, int]:
        """Replace identities in free text with <prefix:code> markers."""
        n = 0

        def sub(regex: re.Pattern, prefix_fn, group: int = 0) -> None:
            nonlocal text

            def repl(m: re.Match) -> str:
                nonlocal n
                val = m.group(group)
                if not val or val.startswith("<"):  # already a marker
                    return m.group(0)
                n += 1
                mk = self.marker(prefix_fn(val), val)
                whole = m.group(0)
                if group == 0:
                    return mk
                s, e = m.start(group) - m.start(0), m.end(group) - m.start(0)
                return whole[:s] + mk + whole[e:]

            text = regex.sub(repl, text)

        sub(EMAIL, lambda _v: "u")
        sub(USER_KV, lambda _v: "u", group=2)
        sub(SSH_USER, lambda _v: "u", group=2)
        sub(IPV4, lambda _v: "ip")
        sub(IPV6, lambda _v: "ip")
        sub(FQDN, lambda _v: "h")
        if self._host_re:
            sub(self._host_re, lambda v: self.known.get(v.lower(), "h"))
        return text, n
