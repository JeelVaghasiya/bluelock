"""Stage 3 - Redact: delete secrets, replace identities with HMAC codes, spot names.

After this stage NOTHING carries a raw identity or secret: entities are codes, free text is
a masked template. Secret spans are written back to raw.db so the on-site raw viewer can
highlight exactly what was removed.
"""
from __future__ import annotations

import asyncio
import json

from ..models import CleanEvent, Counters, ParsedEvent
from ..redact import redact
from ..redact.pseudonym import Pseudonymizer
from ..stores.raw import RawStore


class Redact:
    def __init__(self, in_q: asyncio.Queue, out_q: asyncio.Queue, counters: Counters, pseudo: Pseudonymizer,
                 raw: RawStore, entropy_threshold: float = 4.0, use_ner: bool = True):
        self.in_q, self.out_q, self.c = in_q, out_q, counters
        self.pseudo = pseudo
        self.raw = raw
        self.entropy = entropy_threshold
        self.use_ner = use_ner
        self._span_updates: list[tuple[str, int]] = []

    def clean(self, p: ParsedEvent) -> CleanEvent:
        src = self.pseudo.code(p.entity_prefix, p.entity or "unknown")
        if p.kind == "sample":
            return CleanEvent(rid=p.rid, kind="sample", event_ts=p.event_ts, collect_ts=p.collect_ts, src=src,
                              metric=p.metric, value=p.value, unit=p.unit)
        r = redact(p.msg, self.pseudo, entropy_threshold=self.entropy, use_ner=self.use_ner)
        self.c.redactions_total += r.secrets
        self.c.identities_coded += r.identities
        for k, v in r.by_rule.items():
            self.c.redactions_by_rule[k] = self.c.redactions_by_rule.get(k, 0) + v
        if r.spans:
            self._span_updates.append((json.dumps(r.spans), p.rid))
        ce = CleanEvent(rid=p.rid, kind=p.kind, event_ts=p.event_ts, collect_ts=p.collect_ts, src=src,
                        level=p.level, category=p.category, template=r.template, template_id=r.template_id,
                        redacted=r.secrets, by_rule=r.by_rule)
        if p.kind == "auth":
            ce.user_code = self.pseudo.code("u", p.auth_user or "unknown")
            ce.ip_code = self.pseudo.code("ip", p.auth_ip or "0.0.0.0")
            ce.auth_outcome = p.auth_outcome
        return ce

    async def span_flusher(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if self._span_updates:
                ups, self._span_updates = self._span_updates, []
                await asyncio.to_thread(self.raw.flush)  # make sure rows exist first
                await asyncio.to_thread(self.raw.set_spans, ups)

    async def run(self) -> None:
        while True:
            p: ParsedEvent = await self.in_q.get()
            try:
                ce = self.clean(p)
            except Exception:
                self.c.dropped_unknown += 1
                continue
            await self.out_q.put(ce)
