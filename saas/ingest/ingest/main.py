"""ingest-api (one per region). Stateless: verify identity -> residency -> schema -> queue -> ack.

Identity comes ONLY from the client certificate nginx verified (X-Client-DN); any tenant in the
body is impossible (schema has no such field). Acks only after JetStream confirms the write,
with the payload_id as Nats-Msg-Id, so a retry inside the 2-minute window is dropped there and
acknowledged as 'duplicate'.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import logging
import os
import re
import time
import zlib
from contextlib import asynccontextmanager

import httpx
import nats
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from pydantic import ValidationError
from tl_common.config import iso, now, parse_ts
from tl_common.db import create_pool, tenant_tx
from trustline_schema import Payload
from trustline_schema.ingest import MAX_BODY_BYTES, MAX_INFLATED_BYTES

log = logging.getLogger("ingest")
REGION = os.environ.get("REGION", "ap-south")
CN_RE = re.compile(r"CN=(col-[0-9a-f]+)")
O_RE = re.compile(r"O=tenant-([a-z0-9-]+)")


class Registry:
    """Tenant -> home region and revoked serials, cached from the control plane."""

    def __init__(self, url: str | None):
        self.url = url
        self.home: dict[str, str] = {}
        self.revoked: set[str] = set()
        self.fetched = 0.0

    async def refresh_loop(self) -> None:
        async with httpx.AsyncClient(timeout=3) as c:
            while True:
                try:
                    d = (await c.get(f"{self.url}/internal/registry")).json()
                    self.home = {t: v["home_region"] for t, v in d["tenants"].items()}
                    self.revoked = {s.lower().lstrip("0") for s in d.get("revoked_serials", [])}
                    self.fetched = time.time()
                except Exception as e:
                    log.warning("registry refresh failed: %s", e)
                await asyncio.sleep(10 if self.fetched else 2)


STATE: dict = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    reg = Registry(os.environ.get("CONTROL_INTERNAL", "http://control:8000"))
    STATE["registry"] = reg
    STATE["pool"] = await _retry(lambda: create_pool(os.environ["DATABASE_URL"], max_size=6), "postgres")
    nc = await _retry(lambda: nats.connect(os.environ["NATS_URL"], max_reconnect_attempts=-1), "nats")
    js = nc.jetstream()
    cfg = StreamConfig(name="INGEST", subjects=["ingest.>"], storage=StorageType.FILE,
                       retention=RetentionPolicy.LIMITS, duplicate_window=120, max_age=86400)
    try:
        await js.add_stream(cfg)
    except Exception:
        await js.update_stream(cfg)
    STATE["nc"], STATE["js"] = nc, js
    task = asyncio.create_task(reg.refresh_loop())
    log.info("ingest-api ready for region %s", REGION)
    yield
    task.cancel()
    await nc.drain()


async def _retry(fn, what: str, tries: int = 60):
    for i in range(tries):
        try:
            return await fn()
        except Exception as e:
            log.warning("waiting for %s (%s)", what, e)
            await asyncio.sleep(min(5, 0.5 * (i + 1)))
    raise SystemExit(f"{what} unavailable")


app = FastAPI(title="TrustLine ingest-api", lifespan=lifespan, docs_url=None, redoc_url=None)


def err(status: int, code: str, detail: str = "") -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail[:300]}, status_code=status)


def identity(req: Request) -> tuple[str, str, str] | None:
    if req.headers.get("x-client-verify") != "SUCCESS":
        return None
    dn = req.headers.get("x-client-dn", "")
    cn, org = CN_RE.search(dn), O_RE.search(dn)
    if not cn or not org:
        return None
    return org.group(1), cn.group(1), (req.headers.get("x-client-serial") or "").lower().lstrip("0")


async def audit(tenant: str, **row) -> None:
    try:
        async with tenant_tx(STATE["pool"], tenant) as conn:
            await conn.execute(
                "INSERT INTO ingest_audit(tenant_id, collector_id, payload_id, seq, sha256, region, result, detail,"
                " attempt, lane, attempt_ts, skew_ms) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                tenant, row.get("collector"), row.get("payload_id"), row.get("seq"), row.get("sha"), REGION,
                row["result"], row.get("detail"), row.get("attempt"), row.get("lane"), row.get("attempt_ts"),
                row.get("skew_ms"))
    except Exception as e:  # auditing must never take ingest down
        log.error("audit write failed: %s", e)


@app.post("/v1/ingest")
async def ingest(req: Request):
    recv = now()
    ident = identity(req)
    if not ident:
        return err(403, "unauthenticated", "client certificate not verified")
    tenant, cn, serial = ident
    reg: Registry = STATE["registry"]
    if serial and serial in reg.revoked:
        await audit(tenant, collector=cn, result="revoked", detail=f"serial {serial}")
        return err(403, "revoked", "certificate serial revoked")
    home = reg.home.get(tenant)
    if home != REGION:
        await audit(tenant, collector=cn, result="residency_denied", detail=f"home={home} this={REGION}")
        return err(403, "residency_denied", f"tenant {tenant} is homed in {home}, not {REGION}")
    raw = await req.body()
    if len(raw) > MAX_BODY_BYTES:
        return err(413, "too_large")
    sha = hashlib.sha256(raw).hexdigest()
    attempt = int(req.headers.get("x-tl-attempt", "1") or 1)
    lane = req.headers.get("x-tl-lane", "live")
    attempt_ts = parse_ts(req.headers.get("x-tl-attempt-ts"))
    try:
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        body = d.decompress(raw, MAX_INFLATED_BYTES)
        if d.unconsumed_tail:
            return err(413, "too_large", "inflated body too large")
        payload = Payload.model_validate_json(body)
    except (zlib.error, gzip.BadGzipFile, OSError) as e:
        await audit(tenant, collector=cn, sha=sha, result="bad_encoding", detail=str(e))
        return err(400, "bad_encoding", str(e))
    except ValidationError as e:
        first = e.errors()[0]
        detail = f"{first.get('type')}: {'.'.join(str(x) for x in first.get('loc', ()))}"
        await audit(tenant, collector=cn, sha=sha, result="schema_rejected", detail=detail)
        return err(422, "schema_rejected", detail)
    if payload.collector_id != cn or payload.region != REGION:
        await audit(tenant, collector=cn, payload_id=payload.payload_id, seq=payload.seq, sha=sha,
                    result="identity_mismatch", detail=f"body collector={payload.collector_id} region={payload.region}")
        return err(403, "identity_mismatch", "payload collector/region does not match certificate/region")
    skew = (recv - attempt_ts).total_seconds() * 1000 if attempt_ts and lane == "live" else None
    headers = {"Nats-Msg-Id": str(payload.payload_id), "TL-Tenant": tenant, "TL-Collector": cn, "TL-Sha256": sha,
               "TL-Recv-Ts": iso(recv), "TL-Lane": lane, "TL-Attempt": str(attempt)}
    try:
        ack = await STATE["js"].publish(f"ingest.{REGION}.{tenant}", body, headers=headers, timeout=2)
    except Exception as e:
        return err(503, "queue_unavailable", str(e))
    status = "duplicate" if ack.duplicate else "stored"
    await audit(tenant, collector=cn, payload_id=payload.payload_id, seq=payload.seq, sha=sha, result=status,
                attempt=attempt, lane=lane, attempt_ts=attempt_ts, skew_ms=skew)
    try:
        async with tenant_tx(STATE["pool"], tenant) as conn:
            await conn.execute(
                "INSERT INTO collectors(tenant_id, collector_id, region, last_seen, cert_serial, skew_ms)"
                " VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (tenant_id, collector_id) DO UPDATE SET"
                " last_seen = EXCLUDED.last_seen, cert_serial = EXCLUDED.cert_serial,"
                " skew_ms = COALESCE(EXCLUDED.skew_ms, collectors.skew_ms)",
                tenant, cn, REGION, recv, serial, skew)
            if ack.duplicate:
                await conn.execute(
                    "INSERT INTO delivery_counters(tenant_id, collector_id, js_duplicates) VALUES ($1,$2,1)"
                    " ON CONFLICT (tenant_id, collector_id) DO UPDATE SET"
                    " js_duplicates = delivery_counters.js_duplicates + 1", tenant, cn)
    except Exception as e:
        log.error("collector upsert failed: %s", e)
    return {"payload_id": str(payload.payload_id), "status": status, "seq": payload.seq, "recv_ts": iso(recv)}


@app.get("/v1/actions")
async def actions(req: Request, collector: str = ""):
    """Approved remediation actions for THIS collector, pulled outbound by the site (hop H2b)."""
    ident = identity(req)
    if not ident:
        return err(403, "unauthenticated")
    tenant, cn, _ = ident
    if collector and collector != cn:
        return err(403, "identity_mismatch")
    async with tenant_tx(STATE["pool"], tenant) as conn:
        rows = await conn.fetch(
            "UPDATE remediations SET state = 'dispatched', dispatched_ts = now()"
            " WHERE collector_id = $1 AND state = 'approved'"
            " RETURNING action_id, action_key, params, rollback_of", cn)
    return {"actions": [{"action_id": str(r["action_id"]), "action_key": r["action_key"],
                         "target": (r["params"] or {}).get("target"), "params": r["params"] or {},
                         "rollback_of": str(r["rollback_of"]) if r["rollback_of"] else None} for r in rows]}


@app.get("/v1/config")
async def config(req: Request):
    ident = identity(req)
    if not ident:
        return err(403, "unauthenticated")
    tenant = ident[0]
    async with tenant_tx(STATE["pool"], tenant) as conn:
        r = await conn.fetchrow("SELECT doc, updated_ts FROM thresholds_override WHERE tenant_id = $1", tenant)
    return {"tenant_overrides": r["doc"] if r else {}, "updated_ts": iso(r["updated_ts"]) if r else None}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "region": REGION, "registry_age_s": round(time.time() - STATE["registry"].fetched, 1)}
