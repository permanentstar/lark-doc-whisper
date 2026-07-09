from __future__ import annotations

from lark_doc_whisper.agent.doc_context import DocPromptContext, current_doc_context
from lark_doc_whisper.agent.user_history import current_user_memory_store, search_user_recent_history_tool
from lark_doc_whisper.state.user_memory import SqliteUserMemoryStore


def test_search_user_recent_history_tool_uses_current_user_only(tmp_path):
    store = SqliteUserMemoryStore(tmp_path / "user_memory.db")
    store.add_episode("ou_user", "doc_a", "cmt_1", "用户讨论接口契约", ["接口契约"])
    store.add_episode("ou_other", "doc_b", "cmt_2", "其他用户讨论接口契约", ["接口契约"])
    ctx_token = current_doc_context.set(
        DocPromptContext(file_token="doc_cur", comment_id="cmt_cur", user_id="ou_user")
    )
    store_token = current_user_memory_store.set(store)

    try:
        text = search_user_recent_history_tool.invoke({"query": "接口契约", "limit": 5})
    finally:
        current_user_memory_store.reset(store_token)
        current_doc_context.reset(ctx_token)

    assert "用户讨论接口契约" in text
    assert "其他用户" not in text
    assert "doc_a" in text


def test_search_user_recent_history_tool_requires_current_user(tmp_path):
    store = SqliteUserMemoryStore(tmp_path / "user_memory.db")
    store_token = current_user_memory_store.set(store)

    try:
        text = search_user_recent_history_tool.invoke({"query": "接口契约", "limit": 5})
    finally:
        current_user_memory_store.reset(store_token)

    assert "unavailable" in text
