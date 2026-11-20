"""outbox.db - sealed payloads awaiting acknowledgement. Survives restarts and outages.

A payload's body is stored ONCE (gzip bytes) and every retry re-sends those exact bytes,
so payload_id, seq and sha256 never change between attempts.
"""
from __future__ import annotations

import time

from .sqlite import Db

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
  payload_id TEXT PRIMARY KEY,
  seq INTEGER UNIQUE NOT NULL,
  created_ts REAL NOT NULL,
  body BLOB NOT NULL,
  sha256 TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  records INTEGER NOT NULL,
  kinds TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_ts REAL NOT NULL DEFAULT 0,
  last_error TEXT,
  acked_ts REAL,
  ack_status TEXT
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(status, seq);
"""


class Outbox:
    def __init__(self, db: Db):
        self.db = db

    def add(self, conn, *, payload_id: str, seq: int, body: bytes, sha256: str, records: int,
            kinds: str, priority: int) -> None:
        conn.execute(
            "INSERT INTO outbox(payload_id, seq, created_ts, body, sha256, bytes, records, kinds, priority)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (payload_id, seq, time.time(), body, sha256, len(body), records, kinds, priority))

    def live(self, now: float, max_age: float = 10.0, limit: int = 4) -> list[dict]:
        return self.db.all(
            "SELECT * FROM outbox WHERE status='pending' AND attempts=0 AND created_ts >= ?"
            " ORDER BY priority DESC, seq LIMIT ?", (now - max_age, limit))

    def backlog(self, now: float, limit: int = 4, live_age: float = 10.0) -> list[dict]:
        """Retries and anything older than the live window, oldest sequence first."""
        return self.db.all(
            "SELECT * FROM outbox WHERE status='pending' AND next_attempt_ts <= ?"
            " AND (attempts > 0 OR created_ts < ?) ORDER BY seq LIMIT ?", (now, now - live_age, limit))

    def oldest_pending(self) -> dict | None:
        r = self.db.one("SELECT * FROM outbox WHERE status='pending' ORDER BY seq LIMIT 1")
        return dict(r) if r else None

    def mark_attempt_failed(self, payload_id: str, next_ts: float, err: str) -> None:
        self.db.execute(
            "UPDATE outbox SET attempts = attempts + 1, next_attempt_ts = ?, last_error = ? WHERE payload_id = ?",
            (next_ts, err[:300], payload_id))

    def mark_done(self, conn, payload_id: str, status: str, ack_status: str | None) -> None:
        conn.execute(
            "UPDATE outbox SET status = ?, ack_status = ?, acked_ts = ?, attempts = attempts + 1 WHERE payload_id = ?",
            (status, ack_status, time.time(), payload_id))

    def stats(self) -> dict:
        r = self.db.one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(bytes),0) AS b, MIN(created_ts) AS oldest,"
            " COALESCE(SUM(CASE WHEN attempts > 0 THEN 1 ELSE 0 END),0) AS retrying"
            " FROM outbox WHERE status='pending'")
        a = self.db.one("SELECT MAX(seq) AS s FROM outbox WHERE status='acked'")
        return {"depth": r["n"], "bytes": r["b"], "oldest_created_ts": r["oldest"],
                "retrying": r["retrying"], "last_acked_seq": a["s"] or 0}

    def recent(self, limit: int = 50) -> list[dict]:
        return self.db.all(
            "SELECT payload_id, seq, created_ts, sha256, bytes, records, kinds, priority, status, attempts,"
            " next_attempt_ts, last_error, acked_ts, ack_status FROM outbox ORDER BY seq DESC LIMIT ?", (limit,))

    def purge_acked(self, older_than_s: float = 3600) -> int:
        return self.db.execute("DELETE FROM outbox WHERE status != 'pending' AND acked_ts < ?",
                               (time.time() - older_than_s,)).rowcount
