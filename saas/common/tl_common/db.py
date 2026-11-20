"""Tenant-scoped database access - the ONLY way services query the regional database.

    async with tenant_tx(pool, "acme") as conn:
        await conn.fetch("SELECT ... FROM incidents")   # RLS limits rows to acme

`set_config('app.tenant_id', $1, true)` is transaction-local, so a pooled connection can
never carry one tenant's setting into another tenant's request. Forgetting to call
tenant_tx means current_setting(...) is NULL and every RLS policy returns zero rows.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg


def _dumps(o) -> str:
    return json.dumps(o, default=str)


async def _init(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=_dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=_dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, min_size: int = 1, max_size: int = 8) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, init=_init,
                                     command_timeout=10)


@asynccontextmanager
async def tenant_tx(pool: asyncpg.Pool, tenant: str) -> AsyncIterator[asyncpg.Connection]:
    if not tenant or tenant == "*":
        raise ValueError("tenant required")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant)
            yield conn


class LazyPool:
    """A pool that connects on first use - lets the api start while a region is down."""

    def __init__(self, dsn: str | None):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def get(self) -> asyncpg.Pool:
        if not self.dsn:
            raise RuntimeError("region not configured")
        if self.pool is None:
            self.pool = await create_pool(self.dsn)
        return self.pool
