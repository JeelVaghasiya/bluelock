"""Stage 1 - Collect: stamp collect_ts, keep the ORIGINAL in raw.db (on-site only)."""
from __future__ import annotations

import asyncio

from ..models import Counters, RawEvent
from ..stores.raw import RawStore


class Collect:
    def __init__(self, raw: RawStore, out_q: asyncio.Queue, counters: Counters):
        self.raw = raw
        self.out_q = out_q
        self.c = counters
        self.overflow = 0

    def accept(self, ev: RawEvent) -> None:
        """Called by every source. The raw copy is persisted before anything else happens."""
        ev.rid = self.raw.next_id()
        self.raw.add(ev.rid, ev.eid, ev.source, None, ev.collect_ts, ev.body)
        self.c.raw_in += 1
        try:
            self.out_q.put_nowait(ev)
        except asyncio.QueueFull:
            # back-pressure: raw copy is kept, processing is shed and counted
            self.overflow += 1

    async def flusher(self) -> None:
        while True:
            await asyncio.sleep(0.2)
            await asyncio.to_thread(self.raw.flush)
