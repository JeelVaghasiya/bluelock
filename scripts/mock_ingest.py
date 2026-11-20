#!/usr/bin/env python3
"""Mock SaaS ingest for developing the collector without the full stack.

A real mutual-TLS 1.3 server (same CA and server cert as nginx-ap) that validates every
payload against trustline.v1, de-duplicates by payload_id and acks like ingest-api.

  python scripts/mock_ingest.py [--port 18443] [--fail-for 0]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "schema"))
from trustline_schema import Payload  # noqa: E402

CERTS = ROOT / "deploy" / "certs" / "out"
SEEN: dict[str, dict] = {}
STATS = {"stored": 0, "duplicate": 0, "rejected": 0, "records": {}, "redactions": 0, "seqs": set()}
LOCK = threading.Lock()
DOWN_UNTIL = 0.0


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, code: int, obj: dict) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/v1/actions"):
            return self._json(200, {"actions": []})
        if self.path == "/stats":
            with LOCK:
                s = {**STATS, "seqs": None, "distinct_seqs": len(STATS["seqs"]),
                     "min_seq": min(STATS["seqs"], default=0), "max_seq": max(STATS["seqs"], default=0)}
            return self._json(200, s)
        self._json(404, {})

    def do_POST(self):
        global DOWN_UNTIL
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        if self.path.startswith("/admin/down"):
            DOWN_UNTIL = time.time() + float(self.path.rsplit("=", 1)[-1])
            return self._json(200, {"down_for": self.path.rsplit("=", 1)[-1]})
        if time.time() < DOWN_UNTIL:
            return self._json(503, {"error": "queue_unavailable"})
        cert = self.connection.getpeercert()
        subject = dict(x[0] for x in cert.get("subject", ())) if cert else {}
        tenant = subject.get("organizationName", "").removeprefix("tenant-")
        try:
            p = Payload.model_validate_json(gzip.decompress(raw))
        except Exception as e:
            with LOCK:
                STATS["rejected"] += 1
            return self._json(422, {"error": "schema_rejected", "detail": str(e)[:300]})
        if p.collector_id != subject.get("commonName"):
            return self._json(403, {"error": "identity_mismatch"})
        pid = str(p.payload_id)
        with LOCK:
            dup = pid in SEEN
            if dup:
                STATS["duplicate"] += 1
            else:
                SEEN[pid] = {"sha256": hashlib.sha256(raw).hexdigest(), "tenant": tenant}
                STATS["stored"] += 1
                STATS["seqs"].add(p.seq)
                STATS["redactions"] += p.redactions
                for r in p.records:
                    STATS["records"][r.type] = STATS["records"].get(r.type, 0) + 1
        self._json(200, {"payload_id": pid, "status": "duplicate" if dup else "stored", "seq": p.seq,
                         "recv_ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")})


def main() -> None:
    global DOWN_UNTIL
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=18443)
    ap.add_argument("--fail-for", type=float, default=0, help="return 503 for the first N seconds")
    a = ap.parse_args()
    DOWN_UNTIL = time.time() + a.fail_for
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(CERTS / "servers" / "nginx-ap" / "server.crt", CERTS / "servers" / "nginx-ap" / "server.key")
    ctx.load_verify_locations(CERTS / "ca.crt")
    ctx.verify_mode = ssl.CERT_REQUIRED
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print(f"mock ingest on https://localhost:{a.port} (mTLS, TLS 1.3)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
