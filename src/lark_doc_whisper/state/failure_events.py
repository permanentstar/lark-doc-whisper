"""Durable operational failure events backed by SQLite."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .paths import STATE_DIR

DEFAULT_DB_PATH = STATE_DIR / "failure_events.db"

# Discrete failure stages the handler emits. Kept as a Literal so mypy /
# pyright catches typos at the call site without needing a full StrEnum.
Stage = Literal["url_fetch", "comment_context", "backend_chat", "post_reply"]


@dataclass(frozen=True)
class FailureEvent:
    event_id: str
    file_token: str
    comment_id: str
    reply_id: str
    user_id: str
    session_id: str
    stage: str
    error_type: str
    error_message: str
    fallback_reply_text: str
    fallback_reply_succeeded: bool
    created_at: float
    notified_at: float | None = None


class SqliteFailureEventStore:
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
                CREATE TABLE IF NOT EXISTS failure_events (
                    event_id TEXT PRIMARY KEY,
                    file_token TEXT NOT NULL,
                    comment_id TEXT NOT NULL,
                    reply_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    fallback_reply_text TEXT NOT NULL,
                    fallback_reply_succeeded INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    notified_at REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_failure_events_pending ON failure_events(notified_at, created_at)"
            )

    def add_event(self, event: FailureEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO failure_events(
                    event_id, file_token, comment_id, reply_id, user_id, session_id,
                    stage, error_type, error_message, fallback_reply_text,
                    fallback_reply_succeeded, created_at, notified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.file_token,
                    event.comment_id,
                    event.reply_id,
                    event.user_id,
                    event.session_id,
                    event.stage,
                    event.error_type,
                    event.error_message,
                    event.fallback_reply_text,
                    int(event.fallback_reply_succeeded),
                    event.created_at,
                    event.notified_at,
                ),
            )

    def list_pending(self, *, limit: int) -> list[FailureEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, file_token, comment_id, reply_id, user_id, session_id,
                       stage, error_type, error_message, fallback_reply_text,
                       fallback_reply_succeeded, created_at, notified_at
                FROM failure_events
                WHERE notified_at IS NULL
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            FailureEvent(
                event_id=row[0],
                file_token=row[1],
                comment_id=row[2],
                reply_id=row[3],
                user_id=row[4],
                session_id=row[5],
                stage=row[6],
                error_type=row[7],
                error_message=row[8],
                fallback_reply_text=row[9],
                fallback_reply_succeeded=bool(row[10]),
                created_at=float(row[11]),
                notified_at=(float(row[12]) if row[12] is not None else None),
            )
            for row in rows
        ]

    def mark_notified(self, event_id: str, *, notified_at: float | None = None) -> None:
        ts = time.time() if notified_at is None else notified_at
        with self._connect() as conn:
            conn.execute(
                "UPDATE failure_events SET notified_at = ? WHERE event_id = ?",
                (ts, event_id),
            )


default_store = SqliteFailureEventStore()
