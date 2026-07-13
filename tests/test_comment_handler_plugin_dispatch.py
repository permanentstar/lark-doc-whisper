from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from lark_doc_whisper.config import AppConfig
from lark_doc_whisper.handlers import comment_handler
from lark_doc_whisper.handlers.comment_handler import HandlerContext, handle_comment_event
from lark_doc_whisper.lark.comments import CommentContext
from lark_doc_whisper.plugins.base import CommentPluginRegistry


class _Backend:
    def chat(self, thread_id, user_query, *, doc_context=None, doc_context_provider=None, url_fetch_context=None):
        return "answer"


class _ExplodingBackend:
    def chat(self, *args, **kwargs):
        raise RuntimeError("boom")


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
        self.mentions = []
        self.failures = []

    def on_mention_event(self, header, meta) -> None:
        self.mentions.append((header, meta))

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


def _event(is_mentioned: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        header=SimpleNamespace(
            event_id="evt_dispatch",
            event_type="drive.comment.add_v1",
            tenant_key="tkey",
            app_id="cli_test",
            create_time="1700000000",
        ),
        event=SimpleNamespace(
            comment_id="c1",
            reply_id="r1",
            is_mentioned=is_mentioned,
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
    async def _fake_summarize(user_query, answer, *, quote="", timeout_sec=10.0):
        return ("summary", ["kw"])

    monkeypatch.setattr(comment_handler, "summarize_episode", _fake_summarize)
    monkeypatch.setattr(comment_handler, "user_memory_store", _MemoryStore())
    monkeypatch.setattr(comment_handler.seen_events, "is_seen", lambda *_, **__: False)
    monkeypatch.setattr(comment_handler.seen_events, "mark_seen", lambda *_, **__: None)


def _drive(event, ctx) -> None:
    async def _run():
        await handle_comment_event(event, ctx)
        pending = list(comment_handler._memory_tasks)
        if pending:
            await asyncio.gather(*pending)

    asyncio.run(_run())


def test_dispatch_mention_fires_after_extract(monkeypatch):
    plugin = _RecordingPlugin()
    ctx = HandlerContext(
        cfg=_cfg(),
        api_client=object(),
        backend=_Backend(),
        bot_open_id="ou_bot",
        plugins=CommentPluginRegistry((plugin,)),
    )

    monkeypatch.setattr(comment_handler, "get_reply_text", lambda *_, **__: "总结下")
    monkeypatch.setattr(
        comment_handler,
        "get_comment_context",
        lambda *_, **__: CommentContext(quote="原文", is_whole=False, anchor_block_id="blk"),
    )
    monkeypatch.setattr(comment_handler, "fetch_doc_text", lambda *_, **__: "")
    monkeypatch.setattr(comment_handler, "post_reply", lambda *_, **__: "reply_id")

    _drive(_event(), ctx)

    assert len(plugin.mentions) == 1
    header, meta = plugin.mentions[0]
    assert header.event_id == "evt_dispatch"
    assert meta.file_token == "doc_token"
    assert meta.is_mentioned is True


def test_dispatch_mention_still_fires_when_not_at_mention(monkeypatch):
    plugin = _RecordingPlugin()
    ctx = HandlerContext(
        cfg=_cfg(),
        api_client=object(),
        backend=_Backend(),
        bot_open_id="ou_bot",
        plugins=CommentPluginRegistry((plugin,)),
    )

    _drive(_event(is_mentioned=False), ctx)

    # Non-@ event still audited so ops can see traffic.
    assert len(plugin.mentions) == 1
    assert plugin.mentions[0][1].is_mentioned is False


def test_dispatch_failure_on_backend_error(monkeypatch):
    plugin = _RecordingPlugin()
    ctx = HandlerContext(
        cfg=_cfg(),
        api_client=object(),
        backend=_ExplodingBackend(),
        bot_open_id="ou_bot",
        plugins=CommentPluginRegistry((plugin,)),
    )
    failure_store = _FailureStore()

    monkeypatch.setattr(comment_handler, "get_reply_text", lambda *_, **__: "总结下")
    monkeypatch.setattr(
        comment_handler,
        "get_comment_context",
        lambda *_, **__: CommentContext(quote="原文", is_whole=False),
    )
    monkeypatch.setattr(comment_handler, "fetch_doc_text", lambda *_, **__: "")
    monkeypatch.setattr(comment_handler, "post_reply", lambda *_, **__: "reply_fail")
    monkeypatch.setattr(comment_handler, "failure_event_store", failure_store)

    _drive(_event(), ctx)

    assert len(failure_store.events) == 1
    assert len(plugin.failures) == 1
    assert plugin.failures[0].stage == "backend_chat"
    assert plugin.failures[0].event_id == failure_store.events[0].event_id
