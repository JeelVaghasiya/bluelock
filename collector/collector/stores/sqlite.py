"""Tiny SQLite helper: one file per store, WAL mode, serialised writes."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


class Db:
    def __init__(self, path: str | Path, schema: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.executescript(schema)

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows) -> None:
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                self.conn.executemany(sql, rows)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def one(self, sql: str, params=()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def all(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def tx(self):
        return _Tx(self)


class _Tx:
    def __init__(self, db: Db):
        self.db = db

    def __enter__(self) -> sqlite3.Connection:
        self.db.lock.acquire()
        self.db.conn.execute("BEGIN IMMEDIATE")
        return self.db.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db.lock.release()
        return False
