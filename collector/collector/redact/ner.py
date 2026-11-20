"""Name-spotter: second layer for person names / phone numbers in free text.

With WITH_NER=1 (image built with Presidio + spaCy en_core_web_sm) we use Presidio.
Otherwise a lightweight heuristic runs: honorific + capitalised word, a first-name list,
and phone numbers.
"""
from __future__ import annotations

import os
import re

FIRST_NAMES = {
    "james", "mary", "john", "patricia", "robert", "jennifer", "michael", "linda", "william", "elizabeth",
    "david", "barbara", "richard", "susan", "joseph", "jessica", "thomas", "sarah", "charles", "karen",
    "priya", "rahul", "amit", "anita", "arjun", "deepa", "vikram", "neha", "rohan", "sneha", "aarav",
    "ananya", "jeel", "aarya", "aryaman", "fatima", "mohammed", "wei", "yuki", "hans", "ingrid", "pierre",
    "alice", "bob", "carol", "dave", "erin", "frank", "grace", "heidi", "ivan", "judy", "mallory", "oscar",
}
HONORIFIC = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")
CAP_NAME = re.compile(r"\b([A-Z][a-z]{2,})(\s+[A-Z][a-z]{2,})?\b")
PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[ -]?)?(?:\(\d{2,4}\)[ -]?)?\d{3,5}[ -]\d{3,4}[ -]?\d{0,4}(?!\w)")

_presidio = None
if os.environ.get("WITH_NER") == "1":  # pragma: no cover - optional heavy dependency
    try:
        from presidio_analyzer import AnalyzerEngine

        _presidio = AnalyzerEngine()
    except Exception:
        _presidio = None


def scrub(text: str) -> tuple[str, int]:
    if _presidio is not None:  # pragma: no cover
        res = _presidio.analyze(text=text, language="en", entities=["PERSON", "PHONE_NUMBER"])
        n = 0
        for r in sorted(res, key=lambda r: r.start, reverse=True):
            if "<" in text[r.start:r.end]:
                continue
            text = text[:r.start] + "<person>" + text[r.end:]
            n += 1
        return text, n
    n = 0

    def repl(_m):
        nonlocal n
        n += 1
        return "<person>"

    text = HONORIFIC.sub(repl, text)

    def cap(m: re.Match) -> str:
        nonlocal n
        if m.group(1).lower() in FIRST_NAMES:
            n += 1
            return "<person>"
        return m.group(0)

    text = CAP_NAME.sub(cap, text)
    text = PHONE.sub(repl, text) if re.search(r"\d{3,5}[ -]\d{3,4}", text) else text
    return text, n
