from __future__ import annotations

import threading

from lark_doc_whisper.agent.deerflow_backend import DeerFlowBackend
from lark_doc_whisper.agent.doc_context import (
    DocPromptContext,
    current_doc_context,
    current_doc_context_bag,
    current_doc_context_provider,
    get_doc_context_tool,
)
from lark_doc_whisper.agent.url_fetch import UrlFetchContext, current_url_fetch_context
from lark_doc_whisper.config import UrlFetchConfig


class _FakeClient:
    def __init__(self) -> None:
        self.calls = []
        self.contexts_seen = []

    def chat(self, message: str, *, thread_id: str) -> str:
        self.calls.append((message, thread_id))
        self.contexts_seen.append(current_doc_context.get())
        return "answer"


def test_chat_passes_only_user_query_to_deerflow_client():
    backend = object.__new__(DeerFlowBackend)
    fake_client = _FakeClient()
    backend._client = fake_client

    result = backend.chat(
        "doc__file__user__ou_x",
        "总结下",
        doc_context="DOCUMENT_CONTEXT_SHOULD_NOT_BE_IN_HUMAN_MESSAGE",
    )

    assert result == "answer"
    assert fake_client.calls == [("总结下", "doc__file__user__ou_x")]
    assert fake_client.contexts_seen[0] is not None
    assert fake_client.contexts_seen[0].contexts["document"] == "DOCUMENT_CONTEXT_SHOULD_NOT_BE_IN_HUMAN_MESSAGE"
    assert current_doc_context.get() is None


def test_chat_resets_context_after_client_error():
    backend = object.__new__(DeerFlowBackend)

    class FailingClient:
        def chat(self, message: str, *, thread_id: str) -> str:
            assert current_doc_context.get() is not None
            raise RuntimeError("boom")

    backend._client = FailingClient()

    try:
        backend.chat("doc__file__user__ou_x", "总结下", doc_context="ctx")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError")

    assert current_doc_context.get() is None


def test_chat_resets_url_fetch_context_after_client_error():
    class _Client:
        def chat(self, user_query: str, *, thread_id: str):
            assert current_url_fetch_context.get() is not None
            raise RuntimeError("boom")

    backend = object.__new__(DeerFlowBackend)
    backend._client = _Client()

    try:
        backend.chat(
            "doc__doc_token__user__ou_user",
            "hello",
            doc_context=DocPromptContext(file_token="doc_token", comment_id="comment_1"),
            url_fetch_context=UrlFetchContext(client=object(), cfg=UrlFetchConfig(), allowed_urls=()),
        )
    except RuntimeError:
        pass

    assert current_url_fetch_context.get() is None


def test_chat_rejects_provider_without_doc_context():
    backend = object.__new__(DeerFlowBackend)
    backend._client = _FakeClient()

    try:
        backend.chat(
            "doc__doc_token__user__ou_user",
            "hello",
            doc_context_provider=object(),
        )
    except RuntimeError as exc:
        assert str(exc) == "doc_context_provider requires doc_context"
    else:
        raise AssertionError("expected RuntimeError")

    assert current_doc_context.get() is None
    assert current_doc_context_bag.get() is None
    assert current_doc_context_provider.get() is None


def test_concurrent_chat_calls_keep_contexts_isolated():
    backend = object.__new__(DeerFlowBackend)
    barrier = threading.Barrier(2)
    seen = []

    class BlockingClient:
        def chat(self, message: str, *, thread_id: str) -> str:
            barrier.wait(timeout=2)
            ctx = current_doc_context.get()
            seen.append((message, thread_id, ctx.contexts["document"]))
            return "ok"

    backend._client = BlockingClient()

    t1 = threading.Thread(target=backend.chat, args=("tid_1", "q1"), kwargs={"doc_context": "ctx_1"})
    t2 = threading.Thread(target=backend.chat, args=("tid_2", "q2"), kwargs={"doc_context": "ctx_2"})

    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert sorted(seen) == [
        ("q1", "tid_1", "ctx_1"),
        ("q2", "tid_2", "ctx_2"),
    ]
    assert current_doc_context.get() is None


def test_chat_resets_ephemeral_bag_after_tool_use():
    backend = object.__new__(DeerFlowBackend)

    class ToolCallingClient:
        def chat(self, message: str, *, thread_id: str) -> str:
            receipt = get_doc_context_tool.invoke({"mode": "document", "reason": "test"})
            assert "chars=3" in receipt
            assert current_doc_context_bag.get() == {"document": "ctx"}
            return "ok"

    backend._client = ToolCallingClient()

    assert backend.chat("tid_1", "q", doc_context="ctx") == "ok"
    assert current_doc_context.get() is None
    assert current_doc_context_bag.get() is None
    assert current_doc_context_provider.get() is None
