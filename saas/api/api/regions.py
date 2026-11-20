"""One DB pool + one NATS connection per region. A tenant's data is only ever read from
its home region (H8) - the api is global, the data is not."""
from __future__ import annotations

import asyncio
import logging
import os

import nats
from tl_common.config import home_region
from tl_common.db import create_pool

log = logging.getLogger("api.regions")

REGION_ENV = {"ap-south": ("DB_AP_URL", "NATS_AP_URL"), "eu-west": ("DB_EU_URL", "NATS_EU_URL")}


class Regions:
    def __init__(self):
        self.pools: dict[str, object] = {}
        self.nats: dict[str, object] = {}

    async def connect(self, on_live) -> None:
        async def one(region: str, db_env: str, nats_env: str):
            dsn, nurl = os.environ.get(db_env), os.environ.get(nats_env)
            if not dsn:
                return
            for i in range(120):
                try:
                    self.pools[region] = await create_pool(dsn, max_size=6)
                    break
                except Exception as e:
                    if i % 10 == 0:
                        log.info("region %s db not reachable yet (%s)", region, e)
                    await asyncio.sleep(2)
            if nurl:
                for i in range(120):
                    try:
                        nc = await nats.connect(nurl, max_reconnect_attempts=-1, name=f"api-{region}")
                        def _make_cb(r):
                            async def _cb(m):
                                await on_live(r, m)
                            return _cb
                        await nc.subscribe("live.>", cb=_make_cb(region))
                        self.nats[region] = nc
                        break
                    except Exception as e:
                        if i % 10 == 0:
                            log.info("region %s nats not reachable yet (%s)", region, e)
                        await asyncio.sleep(2)
            log.info("region %s connected (db=%s nats=%s)", region, region in self.pools, region in self.nats)

        await asyncio.gather(*(one(r, d, n) for r, (d, n) in REGION_ENV.items()))

    def pool_for(self, tenant: str):
        region = home_region(tenant)
        pool = self.pools.get(region)
        if pool is None:
            from fastapi import HTTPException
            raise HTTPException(503, f"region {region} (home of {tenant}) is not connected")
        return pool

    async def close(self) -> None:
        for nc in self.nats.values():
            await nc.drain()
        for p in self.pools.values():
            await p.close()


REGIONS = Regions()
