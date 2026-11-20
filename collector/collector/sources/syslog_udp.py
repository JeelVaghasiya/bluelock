"""Syslog UDP listener (hop H1, site network only)."""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from ..models import RawEvent


class SyslogProtocol(asyncio.DatagramProtocol):
    def __init__(self, emit: Callable[[RawEvent], None]):
        self.emit = emit

    def datagram_received(self, data: bytes, addr) -> None:
        for line in data.decode("utf-8", errors="replace").splitlines():
            if line.strip():
                self.emit(RawEvent(source="syslog", body=line.strip(), collect_ts=time.time()))


async def start(port: int, emit: Callable[[RawEvent], None]):
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: SyslogProtocol(emit), local_addr=("0.0.0.0", port))
    return transport
