from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lark_doc_whisper.config import AppConfig
from lark_doc_whisper.handlers import comment_handler
from lark_doc_whisper.handlers.comment_handler import HandlerContext, handle_comment_event
from lark_doc_whisper.lark.comments import CommentContext
from lark_doc_whisper.plugins.base import CommentPluginRegistry


class _MemoryStore:
    def __init__(self) -> None:
        self.episodes = []

    def add_episode(self, *args, **kwargs) -> None:
        self.episodes.append((args, kwargs))


class _FailureStore:
    def __init__(self) -> None:
        self.events = []

    def add_event(self, event) -> None:
        self.events.append(event)


class _RecordingPlugin:
    name = "rec"

    def __init__(self) -> None:
        self.failures = []

    def on_mention_event(self, header, meta) -> None:
        pass

    def on_failure(self, event) -> None:
        self.failures.append(event)


def _cfg() -> AppConfig:
    return AppConfig(
        file_type_default="docx",
        doc_cache_ttl_sec=300,
        event_dedup_ttl_sec=86400,
        user_memory_ttl_sec=2592000,
        state_cleanup_interval_sec=600,
        deerflow_checkpointer_cfg={"type": "memory"},
        backend_timeout_sec=120,
    )


def _event() -> SimpleNamespace:
    return SimpleNamespace(
        header=SimpleNamespace(event_id="evt_deerflow_err"),
        event=SimpleNamespace(
            comment_id="123",
            reply_id="456",
            is_mentioned=True,
            notice_meta=SimpleNamespace(
                file_token="doc_token",
                file_type="docx",
                from_user_id=SimpleNamespace(open_id="ou_user"),
                to_user_id=SimpleNamespace(open_id="ou_bot"),
            ),
        ),
    )


@pytest.fixture(autouse=True)
def _stub_defaults(monkeypatch):
    async def _fake_summarize(*args, **kwargs):
        return ("s", ["k"])

    monkeypatch.setattr(comment_handler, "summarize_episode", _fake_summarize)
    monkeypatch.setattr(comment_handler, "user_memory_store", _MemoryStore())
    monkeypatch.setattr(comment_handler.seen_events, "is_seen", lambda *_, **__: False)
    monkeypatch.setattr(comment_handler.seen_events, "mark_seen", lambda *_, **__: None)


def _drive(event, ctx):
    async def _run():
        await handle_comment_event(event, ctx)
        pending = list(comment_handler._memory_tasks)
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(_run())


@pytest.mark.parametrize(
    "backend_answer",
    [
        "LLM request failed: Error code: 404 - {'error': {'code': 'InvalidEndpointOrModel.NotFound'}}",
        "The configured LLM provider is temporarily unavailable after multiple retries. Please wait a moment and continue the conversation.",
        "The configured LLM provider rejected the request because the account is out of quota, billing is unavailable, or usage is restricted. Please fix the provider account and try again.",
        "The configured LLM provider rejected the request because authentication or access is invalid. Please check the provider credentials and try again.",
        "The configured LLM provider is currently unavailable due to continuous failures. Circuit breaker is engaged to protect the system. Please wait a moment before trying again.",
    ],
)
def test_handler_treats_deerflow_error_fallback_answer_as_backend_failure(monkeypatch, backend_answer):
    class _Backend:
        def chat(self, *args, **kwargs):
            return backend_answer

    plugin = _RecordingPlugin()
    ctx = HandlerContext(
        cfg=_cfg(), api_client=object(), backend=_Backend(),
        bot_open_id="ou_bot",
        plugins=CommentPluginRegistry((plugin,)),
    )
    failure_store = _FailureStore()
    replies = []

    monkeypatch.setattr(comment_handler, "get_reply_text", lambda *_, **__: "总结下")
    monkeypatch.setattr(
        comment_handler, "get_comment_context",
        lambda *_, **__: CommentContext(quote="原文", is_whole=False, anchor_block_id="blk"),
    )
    monkeypatch.setattr(comment_handler, "fetch_doc_text", lambda *_, **__: "")
    monkeypatch.setattr(comment_handler, "failure_event_store", failure_store)

    def fake_post_reply(*args, **kwargs):
        replies.append(kwargs["body_text"])
        return "reply_fallback"

    monkeypatch.setattr(comment_handler, "post_reply", fake_post_reply)

    _drive(_event(), ctx)

    # User must NOT see the raw error text.
    assert backend_answer not in replies
    assert replies == ["目前在神游，稍后回来。"]
    # Failure event recorded so admin_notifier can react.
    assert len(failure_store.events) == 1
    assert failure_store.events[0].stage == "backend_chat"
    assert failure_store.events[0].fallback_reply_succeeded is True
    # Plugin dispatch happened.
    assert len(plugin.failures) == 1
    assert plugin.failures[0].stage == "backend_chat"


def test_handler_passes_normal_answer_through(monkeypatch):
    class _Backend:
        def chat(self, *args, **kwargs):
            return "接口契约的边界问题是……"

    plugin = _RecordingPlugin()
    ctx = HandlerContext(
        cfg=_cfg(), api_client=object(), backend=_Backend(),
        bot_open_id="ou_bot",
        plugins=CommentPluginRegistry((plugin,)),
    )
    failure_store = _FailureStore()
    replies = []

    monkeypatch.setattr(comment_handler, "get_reply_text", lambda *_, **__: "总结下")
    monkeypatch.setattr(
        comment_handler, "get_comment_context",
        lambda *_, **__: CommentContext(quote="原文", is_whole=False, anchor_block_id="blk"),
    )
    monkeypatch.setattr(comment_handler, "fetch_doc_text", lambda *_, **__: "")
    monkeypatch.setattr(comment_handler, "failure_event_store", failure_store)

    def fake_post_reply(*args, **kwargs):
        replies.append(kwargs["body_text"])
        return "reply_ok"

    monkeypatch.setattr(comment_handler, "post_reply", fake_post_reply)

    _drive(_event(), ctx)

    assert replies == ["接口契约的边界问题是……"]
    assert failure_store.events == []
    assert plugin.failures == []
