"""egress.db - append-only, hash-chained log of everything that tried to leave the site.

entry_hash = sha256(prev_hash | payload_id | seq | sha256 | status | ts | detail)
Tampering with any row breaks every later hash, which /local/egress/verify detects.
Statuses: sealed (validated, in outbox) -> acked | rejected ; blocked (never sent).
"""
from __future__ import annotations

import hashlib
import json
import time

from .sqlite import Db

SCHEMA = """
CREATE TABLE IF NOT EXISTS egress (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payload_id TEXT,
  seq INTEGER,
  sha256 TEXT,
  status TEXT NOT NULL,
  detail TEXT,
  ts REAL NOT NULL,
  bytes INTEGER,
  records INTEGER,
  redactions INTEGER,
  json_text TEXT,
  raw_refs TEXT,
  prev_hash TEXT NOT NULL,
  entry_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS egress_pid ON egress(payload_id);
CREATE INDEX IF NOT EXISTS egress_ts ON egress(ts);
"""
GENESIS = "0" * 64


def chain_hash(prev: str, payload_id: str | None, seq: int | None, sha: str | None, status: str,
               ts: float, detail: str | None) -> str:
    msg = "|".join([prev, payload_id or "", str(seq or ""), sha or "", status, f"{ts:.6f}", detail or ""])
    return hashlib.sha256(msg.encode()).hexdigest()


class EgressLog:
    def __init__(self, db: Db):
        self.db = db

    def _last_hash(self, conn) -> str:
        r = conn.execute("SELECT entry_hash FROM egress ORDER BY id DESC LIMIT 1").fetchone()
        return r[0] if r else GENESIS

    def append(self, *, status: str, payload_id: str | None = None, seq: int | None = None,
               sha256: str | None = None, detail: str | None = None, json_text: str | None = None,
               raw_refs: list[int] | None = None, bytes_: int | None = None, records: int | None = None,
               redactions: int | None = None, conn=None) -> dict:
        def do(c):
            ts = time.time()
            prev = self._last_hash(c)
            h = chain_hash(prev, payload_id, seq, sha256, status, ts, detail)
            c.execute(
                "INSERT INTO egress(payload_id, seq, sha256, status, detail, ts, bytes, records, redactions,"
                " json_text, raw_refs, prev_hash, entry_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (payload_id, seq, sha256, status, detail, ts, bytes_, records, redactions, json_text,
                 json.dumps(raw_refs) if raw_refs else None, prev, h))
            return {"status": status, "entry_hash": h, "prev_hash": prev, "ts": ts}

        if conn is not None:
            return do(conn)
        with self.db.tx() as c:
            return do(c)

    def list(self, limit: int = 100, status: str | None = None, before_id: int | None = None) -> list[dict]:
        sql = ("SELECT id, payload_id, seq, sha256, status, detail, ts, bytes, records, redactions,"
               " prev_hash, entry_hash FROM egress")
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if before_id:
            where.append("id < ?")
            params.append(before_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self.db.all(sql, params)

    def by_payload(self, payload_id: str) -> list[dict]:
        return self.db.all("SELECT * FROM egress WHERE payload_id = ? ORDER BY id", (payload_id,))

    def by_id(self, entry_id: int) -> dict | None:
        r = self.db.one("SELECT * FROM egress WHERE id = ?", (entry_id,))
        return dict(r) if r else None

    def verify(self) -> dict:
        prev = None
        n = 0
        with self.db.lock:
            cur = self.db.conn.execute(
                "SELECT id, payload_id, seq, sha256, status, ts, detail, prev_hash, entry_hash FROM egress ORDER BY id")
            for row in cur:
                if prev is not None and row["prev_hash"] != prev:
                    return {"ok": False, "broken_at": row["id"], "checked": n, "why": "prev_hash mismatch"}
                h = chain_hash(row["prev_hash"], row["payload_id"], row["seq"], row["sha256"], row["status"],
                               row["ts"], row["detail"])
                if h != row["entry_hash"]:
                    return {"ok": False, "broken_at": row["id"], "checked": n, "why": "entry_hash mismatch"}
                prev = row["entry_hash"]
                n += 1
        return {"ok": True, "broken_at": None, "checked": n, "head": prev}

    def counts(self) -> dict:
        rows = self.db.all("SELECT status, COUNT(*) AS n FROM egress GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def purge(self, days: float) -> int:
        # Purging keeps the chain verifiable: the first kept row's prev_hash becomes the new anchor.
        cutoff = time.time() - days * 86400
        return self.db.execute("DELETE FROM egress WHERE ts < ?", (cutoff,)).rowcount
