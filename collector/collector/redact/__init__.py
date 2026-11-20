"""Stage-3 redaction pipeline: secrets -> entropy -> identities -> names -> template."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import entropy, ner, rules, template
from .pseudonym import Pseudonymizer


@dataclass
class Redacted:
    text: str
    template: str
    template_id: str
    secrets: int
    identities: int
    names: int
    by_rule: dict[str, int] = field(default_factory=dict)
    spans: list[tuple[int, int, str]] = field(default_factory=list)  # secret spans in the raw text

    @property
    def redacted(self) -> int:
        return self.secrets


def redact(text: str, pseudo: Pseudonymizer, *, entropy_threshold: float = 4.0, use_ner: bool = True) -> Redacted:
    t, by_rule, spans = rules.scrub(text)
    t, hi = entropy.scrub(t, entropy_threshold)
    if hi:
        by_rule["high_entropy"] = by_rule.get("high_entropy", 0) + hi
    t, ids = pseudo.apply(t)
    names = 0
    if use_ner:
        t, names = ner.scrub(t)
    tpl, tid = template.make(t)
    return Redacted(text=t, template=tpl, template_id=tid, secrets=sum(by_rule.values()),
                    identities=ids, names=names, by_rule=by_rule, spans=spans)
