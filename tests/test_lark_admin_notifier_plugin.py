from __future__ import annotations

import asyncio
import json
import logging
import time
from types import SimpleNamespace

import pytest
from lark_oapi.core.enum import AccessTokenType, HttpMethod

from lark_doc_whisper.plugins.lark_admin_notifier import (
    LarkAdminNotifierPlugin,
    SUPPRESSION_WINDOW_SEC,
)
from lark_doc_whisper.state.failure_events import FailureEvent


def _event(**overrides) -> FailureEvent:
    base = dict(
        event_id="evt_backend",
        file_token="doc_token",
        comment_id="c1",
        reply_id="r1",
        user_id="ou_user",
        session_id="sid",
        stage="backend_chat",
        error_type="RuntimeError",
        error_message="boom",
        fallback_reply_text="soft-fail",
        fallback_reply_succeeded=True,
        created_at=1000.0,
    )
    base.update(overrides)
    return FailureEvent(**base)


class _Response:
    def __init__(self, ok: bool = True, code: int = 0, msg: str = "ok") -> None:
        self.code = code
        self.msg = msg
        self.raw = SimpleNamespace(content=b"{}")
        self._ok = ok

    def success(self) -> bool:
        return self._ok


class _RecordingClient:
    def __init__(self, response: _Response) -> None:
        self._response = response
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return self._response


class _RecordingStore:
    def __init__(self) -> None:
        self.notified: list[str] = []

    def mark_notified(self, event_id: str) -> None:
        self.notified.append(event_id)


def _body_of(req) -> dict:
    body = req.body
    return body.__dict__ if not isinstance(body, dict) else body


def test_admin_notifier_posts_interactive_card_with_file_token():
    client = _RecordingClient(_Response(ok=True))
    store = _RecordingStore()

    plugin = LarkAdminNotifierPlugin(
        client=client,
        recipients=({"receive_id_type": "user_id", "receive_id": "u_admin"},),
        failure_store=store,
    )

    plugin.on_failure(_event())

    assert len(client.requests) == 1
    req = client.requests[0]
    assert req.http_method == HttpMethod.POST
    assert req.uri == "/open-apis/im/v1/messages"
    assert AccessTokenType.TENANT in req.token_types
    body = _body_of(req)
    assert body.get("receive_id") == "u_admin"
    assert body.get("msg_type") == "interactive"

    card = json.loads(body["content"])
    # Basic card 2.0 structure.
    assert "header" in card
    assert card["header"]["template"] in {"red", "orange"}
    title = card["header"]["title"]["content"]
    assert "backend_chat" in title
    # Body must surface the key incident fields including file_token.
    dumped = json.dumps(card, ensure_ascii=False)
    assert "doc_token" in dumped
    assert "RuntimeError" in dumped
    assert "boom" in dumped
    assert "c1" in dumped
    # Marked notified after success.
    assert store.notified == ["evt_backend"]


def test_admin_notifier_suppresses_same_stage_error_within_window():
    client = _RecordingClient(_Response(ok=True))
    store = _RecordingStore()

    plugin = LarkAdminNotifierPlugin(
        client=client,
        recipients=({"receive_id_type": "user_id", "receive_id": "u1"},),
        failure_store=store,
    )

    # Ten failures with the same (stage, error_type) fired back-to-back.
    for i in range(10):
        plugin.on_failure(_event(event_id=f"evt_{i}"))

    # Only the first one is sent; the rest are suppressed.
    assert len(client.requests) == 1
    # Suppressed events are still marked notified (won't retry).
    assert store.notified == [f"evt_{i}" for i in range(10)]


def test_admin_notifier_lets_different_error_types_through():
    client = _RecordingClient(_Response(ok=True))
    store = _RecordingStore()

    plugin = LarkAdminNotifierPlugin(
        client=client,
        recipients=({"receive_id_type": "user_id", "receive_id": "u1"},),
        failure_store=store,
    )

    plugin.on_failure(_event(event_id="e1", stage="backend_chat", error_type="RuntimeError"))
    plugin.on_failure(_event(event_id="e2", stage="backend_chat", error_type="TimeoutError"))
    plugin.on_failure(_event(event_id="e3", stage="post_reply", error_type="RuntimeError"))
    # Duplicate the first key — should be suppressed.
    plugin.on_failure(_event(event_id="e4", stage="backend_chat", error_type="RuntimeError"))

    assert len(client.requests) == 3


def test_admin_notifier_reopens_window_after_expiry(monkeypatch):
    client = _RecordingClient(_Response(ok=True))
    store = _RecordingStore()

    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    plugin = LarkAdminNotifierPlugin(
        client=client,
        recipients=({"receive_id_type": "user_id", "receive_id": "u1"},),
        failure_store=store,
    )

    plugin.on_failure(_event(event_id="a"))
    now[0] += SUPPRESSION_WINDOW_SEC + 1
    plugin.on_failure(_event(event_id="b"))

    assert len(client.requests) == 2


def test_admin_notifier_evicts_expired_suppression_keys(monkeypatch):
    client = _RecordingClient(_Response(ok=True))
    store = _RecordingStore()

    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    plugin = LarkAdminNotifierPlugin(
        client=client,
        recipients=({"receive_id_type": "user_id", "receive_id": "u1"},),
        failure_store=store,
    )

    # Fire two distinct keys, populating the suppression map.
    plugin.on_failure(_event(event_id="a", error_type="RuntimeError"))
    plugin.on_failure(_event(event_id="b", error_type="TimeoutError"))
    assert len(plugin._last_sent_by_key) == 2

    # Jump past the window; the next call must evict stale keys.
    now[0] += SUPPRESSION_WINDOW_SEC + 1
    plugin.on_failure(_event(event_id="c", error_type="ValueError"))
    assert set(plugin._last_sent_by_key.keys()) == {("backend_chat", "ValueError")}


def test_admin_notifier_isolates_recipient_failure(caplog):
    responses = iter([_Response(ok=False, code=999, msg="deny"), _Response(ok=True)])

    class _MultiClient:
        def __init__(self) -> None:
            self.requests = []

        def request(self, request):
            self.requests.append(request)
            return next(responses)

    client = _MultiClient()
    store = _RecordingStore()

    plugin = LarkAdminNotifierPlugin(
        client=client,
        recipients=(
            {"receive_id_type": "user_id", "receive_id": "u_bad"},
            {"receive_id_type": "user_id", "receive_id": "u_good"},
        ),
        failure_store=store,
    )

    with caplog.at_level(logging.WARNING, logger="lark_doc_whisper.plugins.lark_admin_notifier"):
        plugin.on_failure(_event())

    assert len(client.requests) == 2
    # Not marked, because at least one recipient failed.
    assert store.notified == []
    warnings = [r.getMessage() for r in caplog.records]
    assert any("u_bad" in msg for msg in warnings)


def test_admin_notifier_does_nothing_on_mention_event():
    client = _RecordingClient(_Response(ok=True))
    store = _RecordingStore()

    plugin = LarkAdminNotifierPlugin(
        client=client,
        recipients=({"receive_id_type": "user_id", "receive_id": "u1"},),
        failure_store=store,
    )

    plugin.on_mention_event(header=object(), meta=object())

    assert client.requests == []
    assert store.notified == []


def test_admin_notifier_delivers_async_and_does_not_block_loop():
    class _SlowClient:
        def __init__(self) -> None:
            self.requests: list = []

        def request(self, request):
            self.requests.append(request)
            time.sleep(0.05)
            return _Response(ok=True)

    async def _run():
        client = _SlowClient()
        store = _RecordingStore()
        plugin = LarkAdminNotifierPlugin(
            client=client,
            recipients=({"receive_id_type": "user_id", "receive_id": "u1"},),
            failure_store=store,
        )

        start = time.perf_counter()
        plugin.on_failure(_event())
        elapsed = time.perf_counter() - start

        # on_failure returned immediately — the blocking request runs in a thread.
        assert elapsed < 0.02
        assert plugin.pending_tasks, "expected a scheduled delivery task"

        await asyncio.gather(*plugin.pending_tasks)
        assert len(client.requests) == 1
        assert store.notified == ["evt_backend"]

    asyncio.run(_run())
