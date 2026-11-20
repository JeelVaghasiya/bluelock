"""Stage 6 - Policy: the allow-list gate. Default-deny, fails closed.

Every record is validated against trustline.v1 (extra="forbid", HMAC-only identities, safe
text). A record that does not fit is BLOCKED: logged in egress.db with the reason, never sent,
and it consumes no sequence number. The assembled payload then passes the egress guard,
which re-scans every string for secrets and raw identities before it can be sealed.
Severity high/critical findings flush immediately; everything else batches every 1 s.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from pydantic import TypeAdapter, ValidationError
from trustline_schema import MAX_RECORDS, Payload, Record, new_payload_id

from ..models import Counters
from ..redact.egress_guard import scan
from ..stores import Stores
from .s7_transmit import Transmit

RECORD = TypeAdapter(Record)
URGENT = {"high", "critical"}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def reason(e: ValidationError) -> str:
    err = e.errors()[0]
    loc = ".".join(str(p) for p in err.get("loc", ()) if not isinstance(p, int))
    return f"{err.get('type')}: {loc}"[:200]


class Policy:
    def __init__(self, in_q: asyncio.Queue, transmit: Transmit, stores: Stores, counters: Counters, *,
                 collector_id: str, region: str, batch_ms: int):
        self.in_q = in_q
        self.tx = transmit
        self.stores = stores
        self.c = counters
        self.collector_id = collector_id
        self.region = region
        self.batch_s = batch_ms / 1000
        self.leak_next = False  # demo toggle: next metric record carries a raw field
        self._by_rule_sent: dict[str, int] = {}

    # ------------------------------------------------------------------ validation
    def _check(self, rec: dict) -> tuple[dict | None, str | None]:
        try:
            RECORD.validate_python(rec)
            return rec, None
        except ValidationError as e:
            return None, reason(e)

    def _leak(self, rec: dict) -> dict:
        """Demo only: simulate a developer bug that adds a raw field to a record."""
        raw = self.stores.raw.query(limit=1, only_secrets=True) or self.stores.raw.query(limit=1)
        self.leak_next = False
        return {**rec, "raw_msg": raw[0]["body"] if raw else "Failed password for alice from 203.0.113.7"}

    def _rule_delta(self) -> dict[str, int]:
        cur = self.c.redactions_by_rule
        delta = {k: v - self._by_rule_sent.get(k, 0) for k, v in cur.items() if v - self._by_rule_sent.get(k, 0) > 0}
        self._by_rule_sent = dict(cur)
        return delta

    async def flush(self, records: list[dict]) -> None:
        if not records:
            return
        ok: list[dict] = []
        refs: list[int] = []
        for rec in records:
            rids = rec.pop("_raw", None) or []
            if self.leak_next and rec.get("type") == "metric":
                rec = self._leak(rec)
            good, why = self._check(rec)
            if good is not None:
                # egress guard, per record: a hit drops only the offending record, not the batch
                hit = scan(good)
                if hit:
                    good, why = None, f"egress_guard:{hit}"
            if good is None:
                self.c.blocked_total += 1
                await asyncio.to_thread(self.stores.egress.append, status="blocked", detail=why,
                                        json_text=json.dumps(rec, default=str), raw_refs=rids or None, records=1)
                continue
            ok.append(good)
            refs.extend(rids)
        for i in range(0, len(ok), MAX_RECORDS):
            chunk = ok[i:i + MAX_RECORDS]
            await self._seal(chunk, refs if i == 0 else [])

    async def _seal(self, records: list[dict], refs: list[int]) -> None:
        redactions = sum(r.get("redacted", 0) for r in records if r["type"] == "event")
        body = {
            "schema": "trustline.v1", "payload_id": str(new_payload_id()), "collector_id": self.collector_id,
            "seq": 1, "region": self.region, "sent_ts": iso(time.time()), "redactions": redactions,
            "redactions_by_rule": self._rule_delta() if redactions else {}, "records": records,
        }
        try:
            payload = Payload.model_validate(body)
        except ValidationError as e:  # should not happen: records were validated one by one
            self.c.blocked_total += 1
            await asyncio.to_thread(self.stores.egress.append, status="blocked", detail=reason(e),
                                    records=len(records))
            return
        hit = scan(json.loads(payload.model_dump_json(by_alias=True)))
        if hit:
            self.c.blocked_total += 1
            await asyncio.to_thread(self.stores.egress.append, status="blocked", payload_id=str(payload.payload_id),
                                    detail=f"egress_guard:{hit}", records=len(records), raw_refs=refs or None)
            return
        urgent = any(r["type"] == "finding" and r["severity"] in URGENT for r in records)
        kinds = ",".join(sorted({r["type"] for r in records}))
        await asyncio.to_thread(self.tx.seal, payload, refs, 1 if urgent else 0, kinds)
        self.c.records_out += len(records)

    # ------------------------------------------------------------------ loop
    async def run(self) -> None:
        buf: list[dict] = []
        deadline = time.monotonic() + self.batch_s
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            try:
                rec = await asyncio.wait_for(self.in_q.get(), timeout=timeout)
                buf.append(rec)
                urgent = rec.get("type") == "finding" and rec.get("severity") in URGENT
                if not urgent and len(buf) < MAX_RECORDS:
                    continue
            except asyncio.TimeoutError:
                pass
            batch, buf = buf, []
            deadline = time.monotonic() + self.batch_s
            try:
                await self.flush(batch)
            except Exception as e:  # never let one bad batch stop egress
                self.c.blocked_total += 1
                await asyncio.to_thread(self.stores.egress.append, status="blocked",
                                        detail=f"policy_error: {type(e).__name__}: {e}"[:200])
