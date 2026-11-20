"""raw.db - original, unredacted events. Never leaves the site. 7-day retention."""
from __future__ import annotations

import time

from .sqlite import Db

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (
  id INTEGER PRIMARY KEY,
  eid TEXT,
  source TEXT NOT NULL,
  event_ts REAL,
  collect_ts REAL NOT NULL,
  body TEXT NOT NULL,
  spans TEXT
);
CREATE INDEX IF NOT EXISTS raw_collect ON raw(collect_ts);
"""


class RawStore:
    def __init__(self, path: str):
        self.db = Db(path, SCHEMA)
        row = self.db.one("SELECT COALESCE(MAX(id), 0) AS m FROM raw")
        self._next = int(row["m"]) + 1
        self._pending: list[tuple] = []

    def next_id(self) -> int:
        i = self._next
        self._next += 1
        return i

    def add(self, rid: int, eid: str | None, source: str, event_ts: float | None, collect_ts: float, body: str) -> None:
        self._pending.append((rid, eid, source, event_ts, collect_ts, body))

    def flush(self) -> int:
        rows, self._pending = self._pending, []
        if rows:
            self.db.executemany(
                "INSERT OR IGNORE INTO raw(id, eid, source, event_ts, collect_ts, body) VALUES (?,?,?,?,?,?)", rows)
        return len(rows)

    def set_spans(self, updates: list[tuple[str, int]]) -> None:
        if updates:
            self.db.executemany("UPDATE raw SET spans=? WHERE id=?", updates)

    def query(self, limit: int = 200, q: str | None = None, only_secrets: bool = False) -> list[dict]:
        sql = "SELECT * FROM raw"
        where, params = [], []
        if q:
            where.append("body LIKE ?")
            params.append(f"%{q}%")
        if only_secrets:
            where.append("spans IS NOT NULL")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self.db.all(sql, params)

    def get_many(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return self.db.all(f"SELECT * FROM raw WHERE id IN ({marks}) ORDER BY id", ids)

    def stats(self) -> dict:
        r = self.db.one("SELECT COUNT(*) AS n, MIN(collect_ts) AS oldest FROM raw")
        return {"rows": r["n"], "oldest_ts": r["oldest"]}

    def purge(self, days: float) -> int:
        cutoff = time.time() - days * 86400
        return self.db.execute("DELETE FROM raw WHERE collect_ts < ?", (cutoff,)).rowcount
