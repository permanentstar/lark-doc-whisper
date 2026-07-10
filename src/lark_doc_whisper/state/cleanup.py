"""Unified background cleanup for runtime state.

Physical reclamation of expired runtime state lives here, off the request
path. The service runs on a dedicated daemon thread and periodically prunes:

- ``runtime/state/doc_cache/*.json`` — expired doc-text cache files
- ``runtime/state/seen_events.db`` — expired event-dedup rows
- ``runtime/state/user_memory.db`` — expired user Q/A episode rows

TTL semantics (how long a value counts as valid to readers) stay with the
owning modules; this service only deletes what is already past its TTL.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .paths import DOC_CACHE_DIR
from .seen_events import DEFAULT_DB_PATH as SEEN_EVENTS_DB_PATH
from .user_doc_tokens import InMemoryUserDocTokenStore
from .user_memory import DEFAULT_DB_PATH as USER_MEMORY_DB_PATH

logger = logging.getLogger(__name__)


def prune_doc_cache(ttl_sec: int, *, now: float | None = None) -> int:
    """Delete doc-cache files older than *ttl_sec*. Returns deleted count."""
    now = time.time() if now is None else now
    if not DOC_CACHE_DIR.exists():
        return 0
    deleted = 0
    for path in DOC_CACHE_DIR.glob("*.json"):
        ts = _doc_cache_timestamp(path)
        if ts is None or now - ts < ttl_sec:
            continue
        try:
            path.unlink()
            deleted += 1
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("failed to delete doc cache file %s", path, exc_info=True)
    return deleted


def _doc_cache_timestamp(path: Path) -> float | None:
    """Prefer the cached ``ts``; fall back to file mtime for broken files."""
    try:
        with open(path) as f:
            data = json.load(f)
        ts = float(data["ts"])
        return ts
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def prune_seen_events(db_path: Path, ttl_sec: int, *, now: float | None = None) -> int:
    """Delete dedup rows older than *ttl_sec*. Returns deleted count."""
    now = time.time() if now is None else now
    cutoff = now - ttl_sec
    return _delete_rows(db_path, "DELETE FROM seen_events WHERE seen_at < ?", (cutoff,))


def prune_user_memory(db_path: Path, ttl_sec: int, *, now: float | None = None) -> int:
    """Delete memory episodes older than *ttl_sec*. Returns deleted count."""
    now = time.time() if now is None else now
    cutoff = now - ttl_sec
    return _delete_rows(
        db_path,
        "DELETE FROM user_memory_episodes WHERE created_at < ?",
        (cutoff,),
    )


def _delete_rows(db_path: Path, sql: str, params: tuple) -> int:
    if not Path(db_path).exists():
        return 0
    conn = sqlite3.connect(db_path, timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0
    finally:
        conn.close()


class StateCleanupService:
    """Periodically reclaims expired runtime state on a daemon thread."""

    def __init__(
        self,
        *,
        interval_sec: int,
        doc_cache_ttl_sec: int,
        event_dedup_ttl_sec: int,
        user_memory_ttl_sec: int,
        seen_events_db_path: Path = SEEN_EVENTS_DB_PATH,
        user_memory_db_path: Path = USER_MEMORY_DB_PATH,
        user_doc_token_store: InMemoryUserDocTokenStore | None = None,
    ):
        self._interval_sec = max(1, interval_sec)
        self._doc_cache_ttl_sec = doc_cache_ttl_sec
        self._event_dedup_ttl_sec = event_dedup_ttl_sec
        self._user_memory_ttl_sec = user_memory_ttl_sec
        self._seen_events_db_path = Path(seen_events_db_path)
        self._user_memory_db_path = Path(user_memory_db_path)
        self._user_doc_token_store = user_doc_token_store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="whisper-state-cleanup", daemon=True
        )
        self._thread.start()
        logger.info(
            "state cleanup service started (interval=%ss)", self._interval_sec
        )

    def stop(self, timeout_sec: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout_sec)
        self._thread = None
        logger.info("state cleanup service stopped")

    def _run(self) -> None:
        while not self._stop.is_set():
            # Wait first so we don't pay a full scan the instant we boot.
            if self._stop.wait(self._interval_sec):
                break
            self.cleanup_once()

    def cleanup_once(self, *, now: float | None = None) -> None:
        """Run one cleanup pass. Each sub-task is isolated from the others."""
        started = time.perf_counter()
        doc_deleted = self._safe(
            "doc_cache", lambda: prune_doc_cache(self._doc_cache_ttl_sec, now=now)
        )
        seen_deleted = self._safe(
            "seen_events",
            lambda: prune_seen_events(
                self._seen_events_db_path, self._event_dedup_ttl_sec, now=now
            ),
        )
        mem_deleted = self._safe(
            "user_memory",
            lambda: prune_user_memory(
                self._user_memory_db_path, self._user_memory_ttl_sec, now=now
            ),
        )
        user_doc_tokens_deleted = self._safe(
            "user_doc_tokens",
            lambda: self._user_doc_token_store.prune_expired(now=now)
            if self._user_doc_token_store is not None else 0,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "state cleanup finished: doc_cache_deleted=%s seen_events_deleted=%s "
            "user_memory_deleted=%s user_doc_tokens_deleted=%s elapsed_ms=%s",
            doc_deleted, seen_deleted, mem_deleted, user_doc_tokens_deleted, elapsed_ms,
        )

    @staticmethod
    def _safe(name: str, fn) -> int | str:
        try:
            return fn()
        except Exception:
            logger.exception("state cleanup subtask %s failed", name)
            return "error"
