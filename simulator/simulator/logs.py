"""Log generators: application logs (with personal data) and sshd syslog lines.

Deliberately includes identities (emails, IPs, hostnames) and, on injection, real-looking
secrets - the collector must keep these on-site and strip them before egress.
"""
from __future__ import annotations

import numpy as np

from .sites import Site

PATHS = ["/api/orders", "/api/cart", "/api/users/me", "/login", "/api/search?q=shoes", "/api/payments"]

# Fake-but-realistic secrets. AKIAIOSFODNN7EXAMPLE is AWS's documented example key.
SECRETS = [
    ("aws_access_key", "upload failed: credentials AKIAIOSFODNN7EXAMPLE rejected by s3 for bucket backups"),
    ("github_token", "POST /login token=ghp_x8Kq2LmN4pR7sT9vW1yZ3bC5dF6gH8jK0aB2 user {user}"),
    ("bearer", "proxy forwarded Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ7dXNlcn0ifQ.c2lnbmF0dXJlLXZhbHVl"),
    ("password_kv", "db connect retry host=db-01 user=app password=Sup3rS3cret!2026 timeout=5s"),
    ("slack_token", "notify webhook using xoxb-2481-5520-aZ8kQ2mN7pL4vR9t failed with 403"),
    ("card", "payment declined card=4111 1111 1111 1111 exp=12/29 for {user}"),
    ("url_credentials", "fetching config from https://svc_backup:hunter2pass@config.acme.internal/v1"),
    ("pem_private_key", "loaded key -----BEGIN PRIVATE KEY----- MIIEvQIBADANBgkqhkiG9w0BAQEFAASC -----END PRIVATE KEY-----"),
]


def app_log(rng: np.random.Generator, site: Site, host: str, app: str) -> dict | None:
    r = rng.random()
    user = site.users[int(rng.integers(0, 4))]
    ip = site.internal_ips[int(rng.integers(0, len(site.internal_ips)))]
    if r < 0.30:
        return {"level": "debug", "msg": f"cache lookup key=session:{int(rng.integers(1e5, 1e6))} hit"}
    if r < 0.55:
        return {"level": "info", "msg": f"GET /healthz 200 in {int(rng.integers(1, 4))}ms"}
    if r < 0.90:
        path = PATHS[int(rng.integers(0, len(PATHS)))]
        ms = int(rng.lognormal(3, 0.4))
        return {"level": "info", "msg": f"GET {path} 200 in {ms}ms user={user} from {ip}"}
    if r < 0.97:
        return {"level": "warn", "msg": f"slow query on {host} took {int(rng.integers(300, 900))}ms for user={user}"}
    return {"level": "error", "msg": f"upstream timeout calling db from {host} after 5000ms request_id={rng.integers(1e8, 1e9)}"}


def secret_log(rng: np.random.Generator, site: Site, which: str | None = None) -> tuple[str, dict]:
    if which:
        cands = [s for s in SECRETS if s[0] == which] or SECRETS
    else:
        cands = SECRETS
    rule, msg = cands[int(rng.integers(0, len(cands)))]
    user = site.users[0]
    return rule, {"level": "error", "msg": msg.format(user=user)}


def sshd_line(ts_syslog: str, host: str, kind: str, user: str, ip: str, pid: int) -> str:
    port = 40000 + pid % 20000
    if kind == "fail":
        return f"<38>{ts_syslog} {host} sshd[{pid}]: Failed password for {user} from {ip} port {port} ssh2"
    if kind == "invalid":
        return f"<38>{ts_syslog} {host} sshd[{pid}]: Failed password for invalid user {user} from {ip} port {port} ssh2"
    return f"<38>{ts_syslog} {host} sshd[{pid}]: Accepted password for {user} from {ip} port {port} ssh2"
