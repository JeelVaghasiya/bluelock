"""state.db - sequence counter, pseudonym reverse map (Tier 1 only), counters."""
from __future__ import annotations

import json

from .sqlite import Db

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS codes (code TEXT PRIMARY KEY, real TEXT NOT NULL, prefix TEXT NOT NULL,
                                  first_seen REAL DEFAULT (strftime('%s','now')));
"""


class StateStore:
    def __init__(self, db: Db):
        self.db = db

    def get(self, k: str, default=None):
        r = self.db.one("SELECT v FROM kv WHERE k = ?", (k,))
        return json.loads(r["v"]) if r else default

    def set(self, k: str, v, conn=None) -> None:
        sql = "INSERT INTO kv(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v"
        (conn or self.db).execute(sql, (k, json.dumps(v)))

    def next_seq(self, conn) -> int:
        """Allocate the next payload sequence number inside the caller's transaction."""
        r = conn.execute("SELECT v FROM kv WHERE k = 'seq'").fetchone()
        n = (json.loads(r[0]) if r else 0) + 1
        conn.execute("INSERT INTO kv(k, v) VALUES ('seq', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                     (json.dumps(n),))
        return n

    def peek_seq(self) -> int:
        return int(self.get("seq", 0))

    def remember_code(self, code: str, real: str, prefix: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO codes(code, real, prefix) VALUES (?,?,?)", (code, real, prefix))

    def load_codes(self) -> dict[str, str]:
        return {r["code"]: r["real"] for r in self.db.all("SELECT code, real FROM codes")}

    def lookup(self, code: str) -> dict | None:
        r = self.db.one("SELECT code, real, prefix, first_seen FROM codes WHERE code = ?", (code,))
        return dict(r) if r else None

    def search_codes(self, q: str, limit: int = 50) -> list[dict]:
        return self.db.all("SELECT code, real, prefix FROM codes WHERE code LIKE ? OR real LIKE ? LIMIT ?",
                           (f"%{q}%", f"%{q}%", limit))
