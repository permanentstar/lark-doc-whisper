from __future__ import annotations

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from lark_doc_whisper.agent.doc_context import (
    DocPromptContext,
    DocumentContextMiddleware,
    build_context_receipt,
    current_doc_context,
    current_doc_context_bag,
    current_doc_context_provider,
    get_doc_context_tool,
)


def _request(messages: list) -> ModelRequest:
    return ModelRequest(model=object(), messages=messages)


def test_injects_ephemeral_context_after_leading_system_messages():
    middleware = DocumentContextMiddleware()
    original_messages = [
        SystemMessage(content="base system"),
        HumanMessage(content="总结下"),
    ]
    ctx = DocPromptContext(
        file_token="doc_1",
        comment_id="cmt_1",
        quote="接口契约原文",
        contexts={"nearby": "NEARBY_CONTEXT_SHOULD_BE_VISIBLE_TO_MODEL"},
    )
    token = current_doc_context.set(ctx)
    seen_messages = []

    def handler(request: ModelRequest) -> ModelResponse:
        seen_messages.extend(request.messages)
        return ModelResponse(result=[AIMessage(content="ok")])

    try:
        middleware.wrap_model_call(_request(original_messages), handler)
    finally:
        current_doc_context.reset(token)

    assert [m.content for m in original_messages] == ["base system", "总结下"]
    assert seen_messages[0].content == "base system"
    assert isinstance(seen_messages[1], SystemMessage)
    assert "接口契约原文" in seen_messages[1].content
    assert "NEARBY_CONTEXT_SHOULD_BE_VISIBLE_TO_MODEL" in seen_messages[1].content
    assert seen_messages[2].content == "总结下"


def test_no_context_passes_request_through_unchanged():
    middleware = DocumentContextMiddleware()
    messages = [HumanMessage(content="hello")]
    seen_request = None

    def handler(request: ModelRequest) -> ModelResponse:
        nonlocal seen_request
        seen_request = request
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request(messages), handler)

    assert seen_request is not None
    assert seen_request.messages is messages


def test_context_receipt_does_not_include_large_context_text():
    receipt = build_context_receipt(
        mode="nearby",
        context_text="A" * 5000,
    )

    assert "A" * 100 not in receipt
    assert "mode=nearby" in receipt
    assert "chars=5000" in receipt
    assert "sha256=" in receipt


def test_get_doc_context_tool_stores_large_context_in_ephemeral_bag():
    ctx = DocPromptContext(
        file_token="doc_1",
        comment_id="cmt_1",
        contexts={"nearby": "LARGE_CONTEXT_FROM_PROVIDER"},
    )
    token = current_doc_context.set(ctx)
    bag_token = current_doc_context_bag.set({})

    try:
        receipt = get_doc_context_tool.invoke({"mode": "nearby", "reason": "need more evidence"})
        bag = current_doc_context_bag.get()
    finally:
        current_doc_context_bag.reset(bag_token)
        current_doc_context.reset(token)

    assert "LARGE_CONTEXT_FROM_PROVIDER" not in receipt
    assert "mode=nearby" in receipt
    assert bag == {"nearby": "LARGE_CONTEXT_FROM_PROVIDER"}


def test_middleware_injects_tool_fetched_context_from_bag():
    middleware = DocumentContextMiddleware()
    messages = [HumanMessage(content="总结下")]
    ctx = DocPromptContext(file_token="doc_1", comment_id="cmt_1", quote="接口契约原文")
    token = current_doc_context.set(ctx)
    bag_token = current_doc_context_bag.set({"nearby": "NEARBY_CONTEXT_FROM_TOOL"})
    seen_messages = []

    def handler(request: ModelRequest) -> ModelResponse:
        seen_messages.extend(request.messages)
        return ModelResponse(result=[AIMessage(content="ok")])

    try:
        middleware.wrap_model_call(_request(messages), handler)
    finally:
        current_doc_context_bag.reset(bag_token)
        current_doc_context.reset(token)

    injected = seen_messages[0]
    assert isinstance(injected, SystemMessage)
    assert "接口契约原文" in injected.content
    assert "NEARBY_CONTEXT_FROM_TOOL" in injected.content


def test_middleware_lets_bag_override_initial_context_with_same_name():
    middleware = DocumentContextMiddleware()
    messages = [HumanMessage(content="继续")]
    ctx = DocPromptContext(
        file_token="doc_1",
        comment_id="cmt_1",
        contexts={"comment_thread_history": "DEFAULT_HISTORY"},
    )
    token = current_doc_context.set(ctx)
    bag_token = current_doc_context_bag.set({"comment_thread_history": "EXPANDED_HISTORY"})
    seen_messages = []

    def handler(request: ModelRequest) -> ModelResponse:
        seen_messages.extend(request.messages)
        return ModelResponse(result=[AIMessage(content="ok")])

    try:
        middleware.wrap_model_call(_request(messages), handler)
    finally:
        current_doc_context_bag.reset(bag_token)
        current_doc_context.reset(token)

    injected = seen_messages[0]
    assert isinstance(injected, SystemMessage)
    assert "EXPANDED_HISTORY" in injected.content
    assert "DEFAULT_HISTORY" not in injected.content


def test_get_doc_context_tool_prefers_request_scoped_provider():
    class _Provider:
        def __init__(self) -> None:
            self.calls = 0
            self.args = []

        def resolve(self, mode: str, *, before_blocks=None, after_blocks=None, max_chars=None, limit=None) -> str:
            self.calls += 1
            self.args.append((mode, before_blocks, after_blocks, max_chars, limit))
            return "NEARBY_SMALL" if self.calls == 1 else "NEARBY_LARGE"

    provider = _Provider()
    ctx = DocPromptContext(file_token="doc_1", comment_id="cmt_1", quote="接口契约原文")
    token = current_doc_context.set(ctx)
    bag_token = current_doc_context_bag.set({})
    provider_token = current_doc_context_provider.set(provider)

    try:
        first = get_doc_context_tool.invoke({"mode": "nearby", "reason": "first", "before_blocks": 1, "after_blocks": 2, "max_chars": 300})
        second = get_doc_context_tool.invoke({"mode": "nearby", "reason": "second", "before_blocks": 3, "after_blocks": 4, "max_chars": 500})
        bag = current_doc_context_bag.get()
    finally:
        current_doc_context_provider.reset(provider_token)
        current_doc_context_bag.reset(bag_token)
        current_doc_context.reset(token)

    assert "mode=nearby" in first
    assert "mode=nearby" in second
    assert provider.args == [
        ("nearby", 1, 2, 300, None),
        ("nearby", 3, 4, 500, None),
    ]
    assert bag == {"nearby": "NEARBY_LARGE"}


def test_get_doc_context_tool_passes_thread_history_limit_to_provider():
    class _Provider:
        def __init__(self) -> None:
            self.args = []

        def resolve(self, mode: str, *, before_blocks=None, after_blocks=None, max_chars=None, limit=None) -> str:
            self.args.append((mode, max_chars, limit))
            return "THREAD_HISTORY"

    provider = _Provider()
    ctx = DocPromptContext(file_token="doc_1", comment_id="cmt_1", quote="接口契约原文")
    token = current_doc_context.set(ctx)
    bag_token = current_doc_context_bag.set({})
    provider_token = current_doc_context_provider.set(provider)

    try:
        receipt = get_doc_context_tool.invoke(
            {
                "mode": "comment_thread_history",
                "reason": "need earlier turn",
                "limit": 20,
                "max_chars": 6000,
            }
        )
        bag = current_doc_context_bag.get()
    finally:
        current_doc_context_provider.reset(provider_token)
        current_doc_context_bag.reset(bag_token)
        current_doc_context.reset(token)

    assert "mode=comment_thread_history" in receipt
    assert provider.args == [("comment_thread_history", 6000, 20)]
    assert bag == {"comment_thread_history": "THREAD_HISTORY"}


def test_get_doc_context_tool_returns_provider_unavailable_reason_without_injecting():
    class _Provider:
        last_unavailable_reason = "no_new_info"

        def resolve(self, mode: str, *, before_blocks=None, after_blocks=None, max_chars=None, limit=None) -> str:
            return ""

    ctx = DocPromptContext(file_token="doc_1", comment_id="cmt_1", quote="接口契约原文")
    token = current_doc_context.set(ctx)
    bag_token = current_doc_context_bag.set({})
    provider_token = current_doc_context_provider.set(_Provider())

    try:
        receipt = get_doc_context_tool.invoke({"mode": "nearby", "reason": "same window", "before_blocks": 1})
        bag = current_doc_context_bag.get()
    finally:
        current_doc_context_provider.reset(provider_token)
        current_doc_context_bag.reset(bag_token)
        current_doc_context.reset(token)

    assert "reason=no_new_info" in receipt
    assert bag == {}
