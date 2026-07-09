"""User-scoped recent Q/A episode memory.

This is not deerflow's long-term user profile memory. It stores lightweight,
recent Q/A summaries so the model can explicitly search cross-document context
when useful.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import STATE_DIR

DEFAULT_DB_PATH = STATE_DIR / "user_memory.db"


@dataclass(frozen=True)
class UserMemoryEpisode:
    user_id: str
    doc_token: str
    comment_id: str
    summary: str
    keywords: list[str]
    created_at: float


class SqliteUserMemoryStore:
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
                CREATE TABLE IF NOT EXISTS user_memory_episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    doc_token TEXT NOT NULL,
                    comment_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user_memory_user_time ON user_memory_episodes(user_id, created_at)"
            )

    def add_episode(
        self,
        user_id: str,
        doc_token: str,
        comment_id: str,
        summary: str,
        keywords: list[str],
    ) -> None:
        if not user_id or not summary:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_memory_episodes(user_id, doc_token, comment_id, summary, keywords_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    doc_token or "",
                    comment_id or "",
                    summary,
                    json.dumps(keywords, ensure_ascii=False),
                    time.time(),
                ),
            )

    def search(self, user_id: str, query: str, *, limit: int, ttl_sec: int) -> list[UserMemoryEpisode]:
        if not user_id:
            return []
        cutoff = time.time() - ttl_sec
        terms = [t.casefold() for t in query.replace("_", " ").split() if t.strip()]
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, doc_token, comment_id, summary, keywords_json, created_at
                FROM user_memory_episodes
                WHERE user_id = ? AND created_at >= ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, cutoff, max(limit * 4, limit)),
            ).fetchall()

        episodes = [self._row_to_episode(row) for row in rows]
        if terms:
            scored = []
            for ep in episodes:
                haystack = " ".join([ep.summary, *ep.keywords]).casefold()
                score = sum(1 for term in terms if term in haystack)
                if score:
                    scored.append((score, ep.created_at, ep))
            scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return [ep for _, _, ep in scored[:limit]]
        return episodes[:limit]

    @staticmethod
    def _row_to_episode(row) -> UserMemoryEpisode:
        keywords_raw = row[4] or "[]"
        try:
            keywords = json.loads(keywords_raw)
        except json.JSONDecodeError:
            keywords = []
        return UserMemoryEpisode(
            user_id=row[0],
            doc_token=row[1],
            comment_id=row[2],
            summary=row[3],
            keywords=keywords if isinstance(keywords, list) else [],
            created_at=float(row[5]),
        )


default_store = SqliteUserMemoryStore()
