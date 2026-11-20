"""Collector enrolment (hop H6, once).

ENROL_MODE=preissued : use the certificate mounted at /certs/client (demo default).
ENROL_MODE=token     : generate a P-256 key + CSR (CN=<collector_id>, O=tenant-<site>), POST it
                       with a one-time token to the control plane, store the signed cert in
                       /data/pki. The private key never leaves the site.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from .settings import Settings

log = logging.getLogger("collector.enrol")


def _cert_valid(path: Path, min_left: timedelta = timedelta(hours=12)) -> bool:
    if not path.exists():
        return False
    from cryptography import x509

    cert = x509.load_pem_x509_certificate(path.read_bytes())
    return cert.not_valid_after_utc - datetime.now(timezone.utc) > min_left


async def ensure_certificate(s: Settings) -> None:
    if s.enrol_mode != "token":
        crt, key = s.client_cert
        if not Path(crt).exists() or not Path(key).exists():
            raise SystemExit(f"client certificate missing at {s.cert_dir} - run `python scripts/tl.py certs`")
        return
    pki = s.cert_dir
    pki.mkdir(parents=True, exist_ok=True)
    if _cert_valid(pki / "client.crt"):
        log.info("enrolment: existing certificate still valid")
        return

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, s.collector_id),
                                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, f"tenant-{s.site}")]))
           .sign(key, hashes.SHA256()))
    body = {"token": s.enrol_token, "csr": csr.public_bytes(serialization.Encoding.PEM).decode()}
    # first contact: we only have the CA bundle that shipped with the installer
    async with httpx.AsyncClient(verify=str(Path("/certs/ca.crt")), timeout=10) as c:
        r = await c.post(f"{s.control_url}/v1/enrol", json=body)
    if r.status_code != 200:
        raise SystemExit(f"enrolment refused: {r.status_code} {r.text[:200]}")
    res = r.json()
    (pki / "client.key").write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                                       serialization.PrivateFormat.PKCS8,
                                                       serialization.NoEncryption()))
    (pki / "client.crt").write_text(res["cert"])
    (pki / "ca.crt").write_text(res["ca"])
    log.info("enrolled %s, certificate valid until %s, region %s", s.collector_id, res.get("expires"),
             res.get("region"))


def renew_due(s: Settings) -> bool:
    return s.enrol_mode == "token" and not _cert_valid(s.cert_dir / "client.crt", timedelta(days=1))


__all__ = ["ensure_certificate", "renew_due", "timedelta"]
