"""Trust-boundary attacks against the SaaS ingest, fired from the site (laptop).

Each test sends ONE crafted request at the real ingest (nginx mTLS -> ingest-api) and checks that
the SaaS refuses it the way the design says it must. Nothing here weakens the collector: the
tests use their own TLS contexts and never touch the collector's outbox.
"""
from __future__ import annotations

import gzip
import json
import os
import secrets
import ssl
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import socket

import httpx

# Same name pins as the collector: bore.pub (ISP sinkholes) and INGEST_HOSTS (LAN mode, e.g.
# "nginx-ap=192.168.1.20", so TLS still verifies the server certificate by its real name).
_PINS = {"bore.pub": os.environ.get("BORE_PUB_IP", "159.223.110.159")}
for _item in os.environ.get("INGEST_HOSTS", "").split(","):
    if "=" in _item:
        _PINS[_item.split("=", 1)[0].strip()] = _item.split("=", 1)[1].strip()
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host, port, *args, **kwargs):
    return _orig_getaddrinfo(_PINS.get(host, host), port, *args, **kwargs)


socket.getaddrinfo = _getaddrinfo

ROOT = Path(__file__).resolve().parents[2]
INGEST_BASE = os.environ.get("INGEST_BASE", "https://127.0.0.1:8443")
CERT_ROOT = Path(os.environ.get("CERT_ROOT", ROOT / "deploy" / "certs" / "out"))
COLLECTOR_LOCAL = os.environ.get("COLLECTOR_LOCAL", "http://127.0.0.1:9090")

# test id -> (title, what it simulates, expected refusal)
TESTS: dict[str, tuple[str, str, str]] = {
    "rogue_ca": ("Rogue collector", "client cert signed by an attacker's own CA", "TLS handshake refused"),
    "no_cert": ("No client certificate", "plain TLS client with no identity", "refused (no verified cert)"),
    "tls12": ("TLS 1.2 downgrade", "valid cert, but the client caps TLS at 1.2", "handshake refused (TLS 1.3 only)"),
    "spoof_identity": ("Identity spoof", "acme's cert sends a body claiming to be globex's collector", "403 identity_mismatch"),
    "wrong_region": ("Wrong region", "globex (home eu-west) sends to the ap-south ingest", "403 residency_denied"),
    "bad_schema": ("Raw field smuggled", "valid identity, but a record carries an extra raw_msg field", "422 schema_rejected"),
    "tamper_gzip": ("Corrupt body", "valid identity, garbage instead of gzip", "400 bad_encoding"),
    "oversize": ("Oversized body", "6 MB body (limit 5 MB)", "413 too_large"),
    "replay": ("Replay", "re-send an already-acked payload byte-for-byte", "200 duplicate (deduplicated, stored once)"),
}


def uuid7() -> str:
    ms = int(time.time() * 1000)
    r = secrets.randbits(74)
    v = (ms << 80) | (0x7 << 76) | (((r >> 62) & 0xFFF) << 64) | (0b10 << 62) | (r & ((1 << 62) - 1))
    return str(uuid.UUID(int=v))


def _ctx(cert: str | None, tls12: bool = False) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=str(CERT_ROOT / "ca.crt"))
    if tls12:
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    else:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    if cert:
        d = CERT_ROOT / cert
        ctx.load_cert_chain(str(d / "client.crt"), str(d / "client.key"))
    return ctx


def _template() -> dict:
    """The newest payload the collector actually sent (so every other field is valid)."""
    with httpx.Client(base_url=COLLECTOR_LOCAL, timeout=5) as c:
        rows = c.get("/local/egress", params={"status": "acked", "limit": 5}).json()
        for row in rows:
            if row.get("payload_id"):
                d = c.get(f"/local/egress/{row['payload_id']}").json()
                if d.get("json"):
                    return d["json"]
    raise RuntimeError("the collector has not had a payload acked yet - start the site and wait a few seconds")


def _post(cert: str | None, body: bytes, tls12: bool = False) -> tuple[int | None, str]:
    headers = {"Content-Type": "application/json", "Content-Encoding": "gzip", "X-TL-Attempt": "1", "X-TL-Lane": "live"}
    ctx = _ctx(cert, tls12)  # a missing cert/CA is a setup error, never a "refusal"
    try:
        with httpx.Client(base_url=INGEST_BASE, verify=ctx, timeout=10) as c:
            r = c.post("/v1/ingest", content=body, headers=headers)
        return r.status_code, r.text[:300]
    except (httpx.HTTPError, ssl.SSLError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"[:300]


def _declare_oversize(cert: str, size: int = 6 * 1024 * 1024) -> tuple[int | None, str]:
    """Announce a 6 MB body but send only the headers: nginx must answer 413 from Content-Length
    alone (no need to push 6 MB through a home uplink + tunnel)."""
    u = urlparse(INGEST_BASE)
    host, port = u.hostname or "127.0.0.1", u.port or 443
    ctx = _ctx(cert)
    head = (f"POST /v1/ingest HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\n"
            f"Content-Encoding: gzip\r\nContent-Length: {size}\r\n\r\n").encode()
    try:
        with socket.create_connection((host, port), timeout=15) as raw, ctx.wrap_socket(raw, server_hostname=host) as tls:
            tls.sendall(head + b"\x1f\x8b" * 512)
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 65536:
                chunk = tls.recv(4096)
                if not chunk:
                    break
                data += chunk
        line = data.split(b"\r\n", 1)[0].decode(errors="replace")
        parts = line.split()
        code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        return code, line or "connection closed without a response"
    except (ssl.SSLError, OSError) as e:
        return None, f"{type(e).__name__}: {e}"[:300]


def run(test: str) -> dict:
    if test not in TESTS:
        raise KeyError(test)
    title, what, expect = TESTS[test]
    t0 = time.time()
    status: int | None
    try:
        if test in ("rogue_ca", "no_cert", "tls12"):
            body = gzip.compress(b"{}")
            cert = {"rogue_ca": "rogue/client", "no_cert": None, "tls12": "collectors/col-ac01"}[test]
            status, detail = _post(cert, body, tls12=test == "tls12")
            tls_refused = status is None and any(x in detail for x in ("SSL", "TLS", "tls", "certificate", "handshake", "ReadError", "RemoteProtocolError"))
            passed = tls_refused or status in (400, 401, 403, 495, 496)
        elif test == "oversize":
            status, detail = _declare_oversize("collectors/col-ac01")
            passed = status == 413
        elif test == "tamper_gzip":
            status, detail = _post("collectors/col-ac01", b"this is not gzip" * 8)
            passed = status == 400
        else:
            p = _template()
            if test == "replay":
                body = gzip.compress(json.dumps(p, separators=(",", ":")).encode())
                status, detail = _post("collectors/col-ac01", body)
                passed = status == 200 and "duplicate" in detail
            elif test == "spoof_identity":
                q = {**p, "payload_id": uuid7(), "collector_id": "col-9b01"}
                status, detail = _post("collectors/col-ac01", gzip.compress(json.dumps(q).encode()))
                passed = status == 403 and "identity_mismatch" in detail
            elif test == "wrong_region":
                q = {**p, "payload_id": uuid7(), "collector_id": "col-9b01", "region": "eu-west"}
                status, detail = _post("collectors/col-9b01", gzip.compress(json.dumps(q).encode()))
                passed = status == 403 and "residency" in detail
            else:  # bad_schema
                recs = [dict(r) for r in p["records"]]
                recs[0]["raw_msg"] = "Failed password for alice from 203.0.113.7"
                q = {**p, "payload_id": uuid7(), "records": recs}
                status, detail = _post("collectors/col-ac01", gzip.compress(json.dumps(q).encode()))
                passed = status == 422
    except Exception as e:  # noqa: BLE001 - surface any setup problem in the UI
        status, detail, passed = None, f"could not run: {e}", False
        return {"test": test, "title": title, "expect": expect, "status": None, "detail": detail,
                "passed": False, "error": True, "ms": round((time.time() - t0) * 1000), "ts": time.time()}
    return {"test": test, "title": title, "expect": expect, "status": status, "detail": detail,
            "passed": passed, "error": False, "ms": round((time.time() - t0) * 1000), "ts": time.time()}
