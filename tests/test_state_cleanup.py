from __future__ import annotations

import json
import time

from lark_doc_whisper.state import cleanup
from lark_doc_whisper.state.cleanup import (
    StateCleanupService,
    prune_doc_cache,
    prune_seen_events,
    prune_user_memory,
)
from lark_doc_whisper.state.seen_events import SqliteSeenEventsStore
from lark_doc_whisper.state.user_memory import SqliteUserMemoryStore


def _write_doc_cache(dir_path, token: str, *, ts: float, text: str = "hello") -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{token}.json").write_text(json.dumps({"ts": ts, "text": text}))


def test_prune_doc_cache_deletes_expired(tmp_path, monkeypatch):
    cache_dir = tmp_path / "doc_cache"
    monkeypatch.setattr(cleanup, "DOC_CACHE_DIR", cache_dir)
    now = time.time()
    _write_doc_cache(cache_dir, "old", ts=now - 1000)

    deleted = prune_doc_cache(ttl_sec=300, now=now)

    assert deleted == 1
    assert not (cache_dir / "old.json").exists()


def test_prune_doc_cache_keeps_fresh(tmp_path, monkeypatch):
    cache_dir = tmp_path / "doc_cache"
    monkeypatch.setattr(cleanup, "DOC_CACHE_DIR", cache_dir)
    now = time.time()
    _write_doc_cache(cache_dir, "fresh", ts=now - 10)

    deleted = prune_doc_cache(ttl_sec=300, now=now)

    assert deleted == 0
    assert (cache_dir / "fresh.json").exists()


def test_prune_doc_cache_falls_back_to_mtime_for_broken_json(tmp_path, monkeypatch):
    cache_dir = tmp_path / "doc_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cleanup, "DOC_CACHE_DIR", cache_dir)
    broken = cache_dir / "broken.json"
    broken.write_text("{ this is not valid json")
    import os

    old = time.time() - 1000
    os.utime(broken, (old, old))

    deleted = prune_doc_cache(ttl_sec=300, now=time.time())

    assert deleted == 1
    assert not broken.exists()


def test_prune_doc_cache_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "DOC_CACHE_DIR", tmp_path / "nope")
    assert prune_doc_cache(ttl_sec=300, now=time.time()) == 0


def test_prune_seen_events_deletes_expired(tmp_path):
    db_path = tmp_path / "seen_events.db"
    store = SqliteSeenEventsStore(db_path)
    store.mark_seen("evt_old")
    store.mark_seen("evt_new")
    now = time.time()
    with store._connect() as conn:
        conn.execute("UPDATE seen_events SET seen_at = ? WHERE event_id = ?", (now - 1000, "evt_old"))

    deleted = prune_seen_events(db_path, ttl_sec=300, now=now)

    assert deleted == 1
    with store._connect() as conn:
        rows = {r[0] for r in conn.execute("SELECT event_id FROM seen_events").fetchall()}
    assert rows == {"evt_new"}


def test_prune_user_memory_deletes_expired(tmp_path):
    db_path = tmp_path / "user_memory.db"
    store = SqliteUserMemoryStore(db_path)
    store.add_episode("ou_user", "doc_a", "cmt_1", "旧记录", ["k"])
    store.add_episode("ou_user", "doc_b", "cmt_2", "新记录", ["k"])
    now = time.time()
    with store._connect() as conn:
        conn.execute("UPDATE user_memory_episodes SET created_at = ? WHERE comment_id = ?", (now - 1000, "cmt_1"))

    deleted = prune_user_memory(db_path, ttl_sec=300, now=now)

    assert deleted == 1
    with store._connect() as conn:
        rows = {r[0] for r in conn.execute("SELECT comment_id FROM user_memory_episodes").fetchall()}
    assert rows == {"cmt_2"}


def test_prune_missing_db_is_noop(tmp_path):
    assert prune_seen_events(tmp_path / "nope.db", ttl_sec=300) == 0
    assert prune_user_memory(tmp_path / "nope.db", ttl_sec=300) == 0


def test_cleanup_once_isolates_subtask_failures(tmp_path, monkeypatch, caplog):
    # doc_cache pruning blows up; the other two must still run.
    cache_dir = tmp_path / "doc_cache"
    monkeypatch.setattr(cleanup, "DOC_CACHE_DIR", cache_dir)

    seen_db = tmp_path / "seen_events.db"
    mem_db = tmp_path / "user_memory.db"
    seen_store = SqliteSeenEventsStore(seen_db)
    mem_store = SqliteUserMemoryStore(mem_db)
    now = time.time()
    seen_store.mark_seen("evt_old")
    mem_store.add_episode("ou_user", "doc_a", "cmt_1", "旧记录", ["k"])
    with seen_store._connect() as conn:
        conn.execute("UPDATE seen_events SET seen_at = ?", (now - 1000,))
    with mem_store._connect() as conn:
        conn.execute("UPDATE user_memory_episodes SET created_at = ?", (now - 1000,))

    def boom(*_a, **_k):
        raise RuntimeError("doc cache boom")

    monkeypatch.setattr(cleanup, "prune_doc_cache", boom)

    service = StateCleanupService(
        interval_sec=600,
        doc_cache_ttl_sec=300,
        event_dedup_ttl_sec=300,
        user_memory_ttl_sec=300,
        seen_events_db_path=seen_db,
        user_memory_db_path=mem_db,
    )
    service.cleanup_once(now=now)

    with seen_store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM seen_events").fetchone()[0] == 0
    with mem_store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_memory_episodes").fetchone()[0] == 0


def test_service_start_stop(tmp_path):
    service = StateCleanupService(
        interval_sec=600,
        doc_cache_ttl_sec=300,
        event_dedup_ttl_sec=300,
        user_memory_ttl_sec=300,
        seen_events_db_path=tmp_path / "seen_events.db",
        user_memory_db_path=tmp_path / "user_memory.db",
    )
    service.start()
    service.stop(timeout_sec=2.0)
