"""event_id-based dedup backed by SQLite.

The previous JSON implementation had a process-local cache and was not safe
for same-host multi-instance gateway runs. SQLite gives us file-level locking,
WAL, and atomic ``INSERT OR IGNORE`` with no extra service dependency.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .paths import STATE_DIR

DEFAULT_DB_PATH = STATE_DIR / "seen_events.db"


class SqliteSeenEventsStore:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _setup(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_events (
                    event_id TEXT PRIMARY KEY,
                    seen_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_seen_events_seen_at ON seen_events(seen_at)"
            )

    def is_seen(self, event_id: str, *, ttl_sec: int) -> bool:
        if not event_id:
            return False
        cutoff = time.time() - ttl_sec
        with self._connect() as conn:
            row = conn.execute(
                "SELECT seen_at FROM seen_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return row is not None and float(row[0]) >= cutoff

    def mark_seen(self, event_id: str) -> None:
        if not event_id:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO seen_events(event_id, seen_at) VALUES (?, ?)",
                (event_id, time.time()),
            )


_store = SqliteSeenEventsStore()


def is_seen(event_id: str, *, ttl_sec: int) -> bool:
    return _store.is_seen(event_id, ttl_sec=ttl_sec)


def mark_seen(event_id: str) -> None:
    _store.mark_seen(event_id)
