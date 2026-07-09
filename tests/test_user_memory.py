from __future__ import annotations

import time

from lark_doc_whisper.state.user_memory import SqliteUserMemoryStore


def test_user_memory_search_returns_matching_episode_for_same_user(tmp_path):
    store = SqliteUserMemoryStore(tmp_path / "user_memory.db")
    store.add_episode(
        user_id="ou_user",
        doc_token="doc_a",
        comment_id="cmt_1",
        summary="用户询问 Worker 和 Harness 的接口契约。",
        keywords=["worker", "harness", "接口契约"],
    )

    results = store.search("ou_user", "harness 接口", limit=5, ttl_sec=86400)

    assert len(results) == 1
    assert results[0].doc_token == "doc_a"
    assert "接口契约" in results[0].summary


def test_user_memory_is_isolated_by_user(tmp_path):
    store = SqliteUserMemoryStore(tmp_path / "user_memory.db")
    store.add_episode("ou_a", "doc_a", "cmt_1", "A 用户关心接口契约", ["接口契约"])
    store.add_episode("ou_b", "doc_b", "cmt_2", "B 用户关心接口契约", ["接口契约"])

    results = store.search("ou_a", "接口契约", limit=5, ttl_sec=86400)

    assert [r.user_id for r in results] == ["ou_a"]
    assert [r.doc_token for r in results] == ["doc_a"]


def test_user_memory_filters_expired_episodes(tmp_path):
    store = SqliteUserMemoryStore(tmp_path / "user_memory.db")
    store.add_episode("ou_user", "doc_a", "cmt_1", "旧接口契约问题", ["接口契约"])
    with store._connect() as conn:
        conn.execute("UPDATE user_memory_episodes SET created_at = ?", (time.time() - 120,))

    results = store.search("ou_user", "接口契约", limit=5, ttl_sec=60)

    assert results == []
