from __future__ import annotations

import asyncio

import pytest

from lark_doc_whisper.config import AppConfig
from lark_doc_whisper.gateway import ws_gateway


def _cfg() -> AppConfig:
    return AppConfig(
        file_type_default="docx",
        doc_cache_ttl_sec=300,
        event_dedup_ttl_sec=86400,
        user_memory_ttl_sec=2592000,
        state_cleanup_interval_sec=600,
        deerflow_checkpointer_cfg={"type": "memory"},
    )


def test_run_gateway_resolves_bot_open_id_at_startup(monkeypatch):
    api_client = object()
    captured = {}

    class StopBeforeStartingWorkers(RuntimeError):
        pass

    def fake_context(**kwargs):
        captured.update(kwargs)
        raise StopBeforeStartingWorkers

    monkeypatch.setattr(ws_gateway, "get_client", lambda: api_client)
    monkeypatch.setattr(ws_gateway, "resolve_bot_open_id", lambda client: "ou_bot")
    monkeypatch.setattr(ws_gateway, "DeerFlowBackend", lambda **_: object())
    monkeypatch.setattr(ws_gateway, "HandlerContext", fake_context)

    with pytest.raises(StopBeforeStartingWorkers):
        ws_gateway._run_gateway(_cfg(), env={})

    assert captured["api_client"] is api_client
    assert captured["bot_open_id"] == "ou_bot"


def test_run_gateway_fails_fast_when_bot_open_id_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(ws_gateway, "get_client", lambda: object())

    def fail(_client):
        raise RuntimeError("failed to resolve bot open_id")

    monkeypatch.setattr(ws_gateway, "resolve_bot_open_id", fail)

    with pytest.raises(RuntimeError, match="failed to resolve bot open_id"):
        ws_gateway._run_gateway(_cfg(), env={})


def test_enqueue_event_returns_false_when_queue_full():
    async def run():
        q = asyncio.Queue(maxsize=1)
        assert await ws_gateway._enqueue_event(q, "evt_1") is True
        assert await ws_gateway._enqueue_event(q, "evt_2") is False

    asyncio.run(run())


def test_event_worker_consumes_until_sentinel(monkeypatch):
    async def run():
        handled = []

        async def fake_handle(event, ctx):
            handled.append((event, ctx))

        monkeypatch.setattr(ws_gateway, "handle_comment_event", fake_handle)
        q = asyncio.Queue()
        await q.put("evt_1")
        await q.put(None)
        await ws_gateway._event_worker(q, ctx="ctx", worker_id=0)

        assert handled == [("evt_1", "ctx")]
        assert q.empty()

    asyncio.run(run())
