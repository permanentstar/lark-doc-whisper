from __future__ import annotations

import asyncio

import pytest

from lark_doc_whisper.config import AppConfig
from lark_doc_whisper.config import OAuthCallbackConfig, UrlAuthorizationConfig, UrlFetchConfig
from lark_doc_whisper.gateway import ws_gateway
from lark_doc_whisper.state.user_doc_tokens import InMemoryUserDocTokenStore


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


def test_oauth_callback_service_requires_authorization_scopes():
    cfg = _cfg()
    object.__setattr__(cfg, "oauth_callback", OAuthCallbackConfig(enabled=True, host="127.0.0.1", port=0))
    object.__setattr__(
        cfg,
        "url_fetch",
        UrlFetchConfig(
            authorization=UrlAuthorizationConfig(
                enabled=True,
                redirect_uri="http://127.0.0.1:8088/oauth/callback",
                scopes=(),
            )
        ),
    )

    with pytest.raises(RuntimeError, match="oauth_callback enabled"):
        ws_gateway._start_oauth_callback_service(
            cfg=cfg,
            env={"LARK_APP_ID": "cli_test", "LARK_APP_SECRET": "secret"},
            token_store=InMemoryUserDocTokenStore(),
        )


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
