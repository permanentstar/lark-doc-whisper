"""Ephemeral document context for deerflow model calls.

The checkpoint should persist the user's question and the assistant's answer,
not large document snippets. This module carries per-call document context in a
ContextVar and injects it only at the model-call boundary.
"""
from __future__ import annotations

import hashlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Protocol

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langchain_core.tools import tool


@dataclass(frozen=True)
class DocPromptContext:
    file_token: str
    comment_id: str
    user_id: str = ""
    quote: str = ""
    contexts: dict[str, str] = field(default_factory=dict)
    user_memory_ttl_sec: int = 30 * 24 * 60 * 60


class DocContextProvider(Protocol):
    last_unavailable_reason: str

    def resolve(
        self,
        mode: str,
        *,
        before_blocks: int | None = None,
        after_blocks: int | None = None,
        max_chars: int | None = None,
        limit: int | None = None,
    ) -> str:
        ...


current_doc_context: ContextVar[DocPromptContext | None] = ContextVar(
    "lark_doc_whisper_doc_context",
    default=None,
)
current_doc_context_bag: ContextVar[dict[str, str] | None] = ContextVar(
    "lark_doc_whisper_doc_context_bag",
    default=None,
)
current_doc_context_provider: ContextVar[DocContextProvider | None] = ContextVar(
    "lark_doc_whisper_doc_context_provider",
    default=None,
)


def build_context_receipt(*, mode: str, context_text: str) -> str:
    digest = hashlib.sha256(context_text.encode("utf-8")).hexdigest()[:16]
    return f"[doc context attached: mode={mode}, chars={len(context_text)}, sha256={digest}]"


def _build_context_message(ctx: DocPromptContext) -> SystemMessage:
    bag = current_doc_context_bag.get() or {}
    lines = [
        "<doc-comment-context>",
        f"<file_token>{ctx.file_token}</file_token>",
        f"<comment_id>{ctx.comment_id}</comment_id>",
    ]
    if ctx.quote:
        lines.extend(["<quote>", ctx.quote, "</quote>"])
    for name, text in ctx.contexts.items():
        if name in bag:
            continue
        if not text:
            continue
        lines.extend([f"<{name}>", text, f"</{name}>"])
    for name, text in bag.items():
        if not text:
            continue
        lines.extend([f"<{name}>", text, f"</{name}>"])
    lines.append("</doc-comment-context>")
    return SystemMessage(content="\n".join(lines))


def _insert_after_leading_system_messages(messages: list, injected: SystemMessage) -> list:
    idx = 0
    while idx < len(messages) and isinstance(messages[idx], SystemMessage):
        idx += 1
    return [*messages[:idx], injected, *messages[idx:]]


class DocumentContextMiddleware(AgentMiddleware[AgentState]):
    """Inject current document context into model requests without persisting it."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        ctx = current_doc_context.get()
        if ctx is None:
            return handler(request)
        messages = _insert_after_leading_system_messages(
            request.messages,
            _build_context_message(ctx),
        )
        return handler(request.override(messages=messages))


def _context_for_mode(
    ctx: DocPromptContext,
    mode: str,
    *,
    before_blocks: int | None = None,
    after_blocks: int | None = None,
    max_chars: int | None = None,
    limit: int | None = None,
) -> tuple[str, str]:
    provider = current_doc_context_provider.get()
    if provider is not None:
        text = provider.resolve(
            mode,
            before_blocks=before_blocks,
            after_blocks=after_blocks,
            max_chars=max_chars,
            limit=limit,
        )
        if text:
            return text, ""
        return "", getattr(provider, "last_unavailable_reason", "") or ""
    if mode in ctx.contexts:
        return ctx.contexts[mode], ""
    if mode in {"full_excerpt", "document_summary", "summary"}:
        return ctx.contexts.get("document", ""), ""
    return "", ""


@tool("get_doc_context")
def get_doc_context_tool(
    mode: str,
    reason: str = "",
    before_blocks: int | None = None,
    after_blocks: int | None = None,
    max_chars: int | None = None,
    limit: int | None = None,
) -> str:
    """Attach more document context for this comment without returning it inline.

    Use this when the visible quote is insufficient to answer. Supported modes:
    ``nearby``, ``section``, ``document_summary``, ``full_excerpt``, and
    ``comment_thread_history``. For ``nearby``, the model may request
    ``before_blocks`` and ``after_blocks``; for thread history it may request
    ``limit``. Program guards clamp requests to configured limits. The tool
    returns a short receipt; the actual context is injected into the next model
    call.
    """
    ctx = current_doc_context.get()
    if ctx is None:
        return "[doc context unavailable: no active document context]"

    context_text, unavailable_reason = _context_for_mode(
        ctx,
        mode,
        before_blocks=before_blocks,
        after_blocks=after_blocks,
        max_chars=max_chars,
        limit=limit,
    )
    if not context_text:
        final_reason = unavailable_reason or reason or "unspecified"
        return f"[doc context unavailable: mode={mode}, reason={final_reason}]"

    bag = current_doc_context_bag.get()
    if bag is None:
        bag = {}
        current_doc_context_bag.set(bag)
    bag[mode] = context_text
    return build_context_receipt(mode=mode, context_text=context_text)
