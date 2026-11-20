"""Global control plane: tenant registry, collector enrolment, certificate authority.

Holds NO telemetry. Two listeners:
  :8444 TLS (server-auth)  POST /v1/enrol        one-time token + CSR -> signed client cert (hop H6)
  :8000 internal           GET  /internal/registry, POST /internal/revoke
The CA private key is mounted only into this container.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

log = logging.getLogger("control")
TENANTS_FILE = Path(os.environ.get("TENANTS_FILE", "/config/tenants.yaml"))
CA_DIR = Path(os.environ.get("CA_DIR", "/ca"))
TLS_DIR = Path(os.environ.get("TLS_DIR", "/tls"))
DATA = Path(os.environ.get("DATA_DIR", "/data"))
CERT_DAYS = int(os.environ.get("CERT_DAYS", "7"))


def tenants() -> dict:
    return yaml.safe_load(TENANTS_FILE.read_text(encoding="utf-8")) or {}


def _load_json(name: str, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default


def _save_json(name: str, obj) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, indent=1))


def enrol_tokens() -> dict[str, str]:
    out = {}
    for part in filter(None, os.environ.get("ENROL_TOKENS", "").split(",")):
        t, _, tok = part.partition("=")
        out[tok.strip()] = t.strip()
    return out


# ------------------------------------------------------------------ internal API
internal = FastAPI(title="TrustLine control (internal)", docs_url=None, redoc_url=None)


@internal.get("/internal/registry")
def registry():
    t = tenants()
    revoked = sorted(set(t.get("revoked_serials") or []) | set(_load_json("revoked.json", [])))
    return {
        "tenants": {k: {"name": v.get("name", k), "home_region": v["home_region"],
                        "collectors": [c["id"] for c in v.get("collectors", [])]}
                    for k, v in (t.get("tenants") or {}).items()},
        "regions": t.get("regions") or {},
        "revoked_serials": revoked,
        "issued": _load_json("issued.json", []),
    }


class Revoke(BaseModel):
    serial: str


@internal.post("/internal/revoke")
def revoke(r: Revoke):
    cur = set(_load_json("revoked.json", []))
    cur.add(r.serial.lower().lstrip("0"))
    _save_json("revoked.json", sorted(cur))
    return {"revoked": sorted(cur)}


@internal.get("/healthz")
def health():
    return {"ok": True}


# ------------------------------------------------------------------ enrolment API (TLS)
public = FastAPI(title="TrustLine control (enrolment)", docs_url=None, redoc_url=None)


class Enrol(BaseModel):
    token: str
    csr: str


@public.post("/v1/enrol")
def enrol(req: Enrol):
    tokens = enrol_tokens()
    used = set(_load_json("used_tokens.json", []))
    tenant = tokens.get(req.token)
    if not tenant or req.token in used:
        raise HTTPException(403, "invalid or already used enrolment token")
    csr = x509.load_pem_x509_csr(req.csr.encode())
    if not csr.is_signature_valid:
        raise HTTPException(400, "bad CSR signature")
    cn = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    org = csr.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value
    t = (tenants().get("tenants") or {}).get(tenant) or {}
    allowed = {c["id"] for c in t.get("collectors", [])}
    if org != f"tenant-{tenant}" or cn not in allowed or not re.fullmatch(r"col-[0-9a-f]{4,12}", cn):
        raise HTTPException(403, f"CSR subject must be CN=<registered collector>, O=tenant-{tenant}")
    ca_key = serialization.load_pem_private_key((CA_DIR / "ca.key").read_bytes(), None)
    ca_cert = x509.load_pem_x509_certificate((CA_DIR / "ca.crt").read_bytes())
    now = datetime.now(timezone.utc)
    serial = x509.random_serial_number()
    cert = (x509.CertificateBuilder()
            .subject_name(csr.subject).issuer_name(ca_cert.subject).public_key(csr.public_key())
            .serial_number(serial).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=CERT_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .sign(ca_key, hashes.SHA256()))
    used.add(req.token)
    _save_json("used_tokens.json", sorted(used))
    issued = _load_json("issued.json", [])
    issued.append({"serial": format(serial, "x"), "cn": cn, "tenant": tenant, "issued": now.isoformat(),
                   "expires": cert.not_valid_after_utc.isoformat()})
    _save_json("issued.json", issued[-200:])
    region = t["home_region"]
    base = ((tenants().get("regions") or {}).get(region) or {}).get("ingest_base")
    log.info("enrolled %s for tenant %s (serial %x)", cn, tenant, serial)
    return {"cert": cert.public_bytes(serialization.Encoding.PEM).decode(),
            "ca": ca_cert.public_bytes(serialization.Encoding.PEM).decode(),
            "ingest_base": base, "region": region, "expires": cert.not_valid_after_utc.isoformat()}


def new_token() -> str:
    return secrets.token_urlsafe(24)


async def amain() -> None:
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    servers = [uvicorn.Server(uvicorn.Config(internal, host=os.environ.get("BIND_HOST", "0.0.0.0"), port=int(os.environ.get("INTERNAL_PORT", "8000")), log_level="warning"))]
    crt, key = TLS_DIR / "server.crt", TLS_DIR / "server.key"
    if crt.exists() and (CA_DIR / "ca.key").exists():
        servers.append(uvicorn.Server(uvicorn.Config(public, host=os.environ.get("BIND_HOST", "0.0.0.0"), port=int(os.environ.get("ENROL_PORT", "8444")), log_level="warning",
                                                     ssl_certfile=str(crt), ssl_keyfile=str(key),
                                                     ssl_version=ssl.PROTOCOL_TLS_SERVER)))
    else:
        log.warning("no TLS material at %s - enrolment endpoint disabled", TLS_DIR)
    await asyncio.gather(*(s.serve() for s in servers))


if __name__ == "__main__":
    asyncio.run(amain())
