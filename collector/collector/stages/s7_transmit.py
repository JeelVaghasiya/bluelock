"""Stage 7 - Transmit: seal -> outbox -> mutual-TLS send -> ack. At-least-once on the wire.

seal():  ONE SQLite transaction allocates seq, stores the gzip body in outbox.db and appends a
         'sealed' entry (exact JSON + sha256) to the hash-chained egress log.
sender:  live lane (fresh payloads, priority first) + backlog lane (retries, oldest seq first,
         rate-limited). While the SaaS is unreachable only one probe is in flight, with
         exponential backoff + jitter. Retries re-send the stored bytes byte-for-byte.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import logging
import os
import random
import ssl
import time
from datetime import datetime, timezone

import socket

# Static name -> address pins, applied before DNS:
#  * bore.pub: some ISPs (e.g. Airtel/Jio) sinkhole it, so pin the relay's real address.
#  * INGEST_HOSTS="nginx-ap=192.168.1.20": LAN mode. The collector dials https://nginx-ap:8443, so
#    TLS still verifies the server certificate against its real name (nginx-ap), not a bare IP.
def _pins() -> dict[str, str]:
    pins = {"bore.pub": os.environ.get("BORE_PUB_IP", "159.223.110.159")}
    for item in os.environ.get("INGEST_HOSTS", "").split(","):
        if "=" in item:
            name, addr = item.split("=", 1)
            pins[name.strip()] = addr.strip()
    return pins


_PINS = _pins()
_orig_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(host, port, *args, **kwargs):
    return _orig_getaddrinfo(_PINS.get(host, host), port, *args, **kwargs)


socket.getaddrinfo = _patched_getaddrinfo

import httpx
import orjson
from trustline_schema import Payload
from trustline_schema.ingest import CONTENT_TYPE, RETRYABLE_STATUS

from ..models import Counters
from ..stores import Stores

log = logging.getLogger("collector.transmit")


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ssl_context(ca_file: str, cert: tuple[str, str]) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=ca_file)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(*cert)
    return ctx


class Transmit:
    def __init__(self, stores: Stores, counters: Counters, *, ingest_base: str, ca_file: str,
                 cert: tuple[str, str], timeout_s: float = 5, backoff_max_s: float = 10,
                 backlog_rate: float = 50, backlog_inflight: int = 4):
        self.s = stores
        self.c = counters
        self.base = ingest_base
        self.ca_file, self.cert = ca_file, cert
        self.timeout = timeout_s
        self.backoff_max = backoff_max_s
        self.rate = backlog_rate
        self.inflight = backlog_inflight
        self.reachable: bool | None = None
        self.fail_streak = 0
        self.next_try = 0.0
        self.last_ack_ts: float | None = None
        self.last_error: str | None = None
        self.on_ack = None  # optional callback(payload_id, status)
        self.outage_until = 0.0  # debug: simulate the SaaS being unreachable (Simulator dashboard)

    # ------------------------------------------------------------------ seal
    def seal(self, payload: Payload, raw_refs: list[int], priority: int, kinds: str) -> int:
        with self.s.core.tx() as conn:
            seq = self.s.state.next_seq(conn)
            final = payload.model_copy(update={"seq": seq})
            js = final.model_dump_json(by_alias=True)
            body = gzip.compress(js.encode(), mtime=0)
            sha = hashlib.sha256(body).hexdigest()
            pid = str(final.payload_id)
            self.s.outbox.add(conn, payload_id=pid, seq=seq, body=body, sha256=sha,
                              records=len(final.records), kinds=kinds, priority=priority)
            self.s.egress.append(status="sealed", payload_id=pid, seq=seq, sha256=sha, json_text=js,
                                 raw_refs=raw_refs[:200] or None, bytes_=len(body), records=len(final.records),
                                 redactions=final.redactions, conn=conn)
        self.c.payloads_sealed += 1
        return seq

    # ------------------------------------------------------------------ send
    def _backoff(self) -> float:
        return min(self.backoff_max, 2 ** min(self.fail_streak, 10)) * random.uniform(0.5, 1.5)

    async def _send(self, client: httpx.Client, row: dict, lane: str) -> bool:
        attempt = row["attempts"] + 1
        headers = {"Content-Type": CONTENT_TYPE, "Content-Encoding": "gzip", "X-TL-Attempt": str(attempt),
                   "X-TL-Attempt-Ts": iso(time.time()), "X-TL-Lane": lane}
        if time.time() < self.outage_until:
            return await self._failed(row, "ConnectError: simulated SaaS outage (debug)")
        try:
            r = await asyncio.to_thread(client.post, "/v1/ingest", content=row["body"], headers=headers)
        except (httpx.TransportError, ssl.SSLError, OSError) as e:
            return await self._failed(row, f"{type(e).__name__}: {e}")
        if r.status_code == 200:
            ack = orjson.loads(r.content)
            status = ack.get("status", "stored")
            await asyncio.to_thread(self._done, row, "acked", status)
            self.c.acked += 1
            if status == "duplicate":
                self.c.duplicates_acked += 1
            self.reachable, self.fail_streak, self.last_ack_ts = True, 0, time.time()
            return True
        if r.status_code in RETRYABLE_STATUS:
            return await self._failed(row, f"HTTP {r.status_code}")
        # non-retryable: the SaaS refused this payload for a reason; record it, never resend
        try:
            detail = orjson.loads(r.content).get("error", f"HTTP {r.status_code}")
        except Exception:
            detail = f"HTTP {r.status_code}"
        await asyncio.to_thread(self._done, row, "rejected", f"{r.status_code}:{detail}")
        self.c.rejected += 1
        self.reachable, self.fail_streak = True, 0
        return True

    def _done(self, row: dict, status: str, detail: str) -> None:
        with self.s.core.tx() as conn:
            self.s.outbox.mark_done(conn, row["payload_id"], status, detail)
            self.s.egress.append(status=status, payload_id=row["payload_id"], seq=row["seq"], sha256=row["sha256"],
                                 detail=detail, conn=conn)

    async def _failed(self, row: dict, err: str) -> bool:
        self.c.send_failures += 1
        self.fail_streak += 1
        self.reachable = False
        self.last_error = err[:300]
        wait = self._backoff()
        self.next_try = time.time() + wait
        await asyncio.to_thread(self.s.outbox.mark_attempt_failed, row["payload_id"], self.next_try, err)
        return False

    async def run(self) -> None:
        ctx = ssl_context(self.ca_file, self.cert)
        bucket, last_refill = self.rate, time.monotonic()
        with httpx.Client(base_url=self.base, verify=ctx, timeout=self.timeout) as client:
            while True:
                now = time.time()
                if self.reachable is False and now < self.next_try:
                    await asyncio.sleep(min(0.2, self.next_try - now))
                    continue
                if self.reachable is False:
                    # SaaS was down: probe with the oldest pending payload only
                    row = await asyncio.to_thread(self.s.outbox.oldest_pending)
                    if row:
                        await self._send(client, row, "backlog")
                    else:
                        self.reachable = None
                    continue
                live = await asyncio.to_thread(self.s.outbox.live, now)
                for row in live:
                    if not await self._send(client, row, "live"):
                        break
                # backlog lane: oldest first, bounded concurrency, token-bucket rate
                t = time.monotonic()
                bucket = min(self.rate, bucket + (t - last_refill) * self.rate)
                last_refill = t
                n = min(self.inflight, int(bucket))
                backlog = await asyncio.to_thread(self.s.outbox.backlog, time.time(), n) if n > 0 else []
                if backlog and self.reachable is not False:
                    bucket -= len(backlog)
                    await asyncio.gather(*(self._send(client, row, "backlog") for row in backlog))
                if not live and not backlog:
                    await asyncio.sleep(0.05)
