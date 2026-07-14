"""Single-entry event handler.

  WS gateway → on_comment_add(event) → handle_comment_event(event_meta, ctx)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.drive.v1 import P2DriveNoticeCommentAddV1

from .. import thread_id as tid
from ..agent.backend import HarnessBackend
from ..agent.doc_context import DocPromptContext
from ..agent.episode_summarizer import summarize_episode
from ..agent.url_fetch import UrlAuthorizationRequest, UrlFetchContext, preflight_feishu_urls
from ..config import AppConfig
from ..lark.comments import (
    get_comment_context,
    get_comment_thread_history,
    get_reply_text,
    post_reply,
)
from ..lark.doc_fetcher import fetch_doc_text
from ..orchestrator.comment_context import build_comment_context_provider
from ..plugins.base import CommentPluginRegistry
from ..security.policy import AllowedUrl, evaluate_user_query, extract_allowed_urls
from ..state import seen_events
from ..state.failure_events import FailureEvent, Stage, default_store as failure_event_store
from ..state.user_doc_tokens import InMemoryUserDocTokenStore
from ..state.user_memory import default_store as user_memory_store

logger = logging.getLogger(__name__)

COMMENT_CONTEXT_MISSING_REPLY_TEXT = "我没能定位到这条评论对应的原文。请确认评论仍然绑定在文档内容上，然后重新 @我。"

# Keep strong references to fire-and-forget memory tasks so they aren't
# garbage-collected mid-flight (see asyncio.create_task docs).
_memory_tasks: set[asyncio.Task] = set()


@dataclass
class HandlerContext:
    cfg: AppConfig
    api_client: lark.Client
    backend: HarnessBackend
    # Runtime metadata resolved at startup; used for the self-trigger guard.
    bot_open_id: str
    # Lark app_id is public and is only used to build user-facing OAuth URLs.
    app_id: str = ""
    # In-memory HMAC secret for OAuth state integrity; never persisted or logged.
    authorization_state_secret: str = ""
    user_doc_token_store: InMemoryUserDocTokenStore = None  # type: ignore[assignment]
    plugins: CommentPluginRegistry = None  # type: ignore[assignment]

    # Per-thread asyncio.Lock for serializing same-thread requests.
    _thread_locks: dict[str, asyncio.Lock] = None  # type: ignore[assignment]
    _backend_semaphore: asyncio.Semaphore = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self._thread_locks is None:
            object.__setattr__(self, "_thread_locks", {})
        if self._backend_semaphore is None:
            object.__setattr__(
                self,
                "_backend_semaphore",
                asyncio.Semaphore(max(1, self.cfg.max_backend_in_flight)),
            )
        if self.user_doc_token_store is None:
            object.__setattr__(self, "user_doc_token_store", InMemoryUserDocTokenStore())
        if self.plugins is None:
            object.__setattr__(self, "plugins", CommentPluginRegistry(()))

    def lock_for(self, thread_id: str) -> asyncio.Lock:
        lock = self._thread_locks.get(thread_id)
        if lock is None:
            lock = asyncio.Lock()
            self._thread_locks[thread_id] = lock
        return lock

    @property
    def backend_semaphore(self) -> asyncio.Semaphore:
        return self._backend_semaphore


def _build_episode_summary(user_query: str, answer: str, *, max_chars: int = 800) -> str:
    summary = f"Q: {user_query.strip()}\nA: {answer.strip()}"
    return summary[:max_chars]


def _build_episode_keywords(*texts: str, max_keywords: int = 20) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()

    def add(word: str) -> None:
        word = word.strip()
        if len(word) < 2 or word in seen:
            return
        seen.add(word)
        keywords.append(word)

    for text in texts:
        for word in re.findall(r"[A-Za-z0-9_/-]{2,}", text):
            add(word.lower())
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            add(chunk[:8])
            # Include short phrase windows so queries like "接口契约" can match
            # longer strings such as "接口契约有什么问题".
            for size in (4, 3, 2):
                if len(chunk) < size:
                    continue
                for i in range(0, len(chunk) - size + 1):
                    add(chunk[i:i + size])
                    if len(keywords) >= max_keywords:
                        return keywords
        if len(keywords) >= max_keywords:
            break
    return keywords[:max_keywords]


def _merge_allowed_urls(*groups: tuple[AllowedUrl, ...]) -> tuple[AllowedUrl, ...]:
    merged: list[AllowedUrl] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item.url in seen:
                continue
            seen.add(item.url)
            merged.append(item)
    return tuple(merged)


async def _write_episode_memory(
    *,
    user_id: str,
    doc_token: str,
    comment_id: str,
    user_query: str,
    answer: str,
    quote: str,
    timeout_sec: float,
) -> None:
    """Distill and persist a user-memory episode. Best-effort, never raises.

    Prefer LLM distillation; on any failure fall back to the rule-based
    generators so the memory is still written.
    """
    try:
        try:
            summary, keywords = await summarize_episode(
                user_query, answer, quote=quote, timeout_sec=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "episode summarizer timed out after %.1fs; falling back to rules",
                timeout_sec,
            )
            summary = _build_episode_summary(user_query, answer)
            keywords = _build_episode_keywords(user_query, answer, quote)
        except Exception:
            logger.warning("episode summarizer failed; falling back to rules", exc_info=True)
            summary = _build_episode_summary(user_query, answer)
            keywords = _build_episode_keywords(user_query, answer, quote)
        await asyncio.to_thread(
            user_memory_store.add_episode,
            user_id, doc_token, comment_id, summary, keywords,
        )
    except Exception:
        logger.warning("failed to write user memory episode", exc_info=True)


def _schedule_episode_memory(**kwargs) -> "asyncio.Task":
    task = asyncio.create_task(_write_episode_memory(**kwargs))
    _memory_tasks.add(task)
    task.add_done_callback(_memory_tasks.discard)
    return task


def _build_failure_event(
    meta: "_EventMeta",
    *,
    session_id: str,
    stage: Stage,
    error: BaseException,
    fallback_reply_text: str,
    fallback_reply_succeeded: bool,
) -> FailureEvent:
    event_key = meta.event_id or f"{meta.file_token}:{meta.comment_id}:{meta.reply_id or ''}:{stage}"
    return FailureEvent(
        event_id=event_key,
        file_token=meta.file_token,
        comment_id=meta.comment_id,
        reply_id=meta.reply_id or "",
        user_id=meta.from_open_id or "",
        session_id=session_id,
        stage=stage,
        error_type=type(error).__name__,
        error_message=str(error),
        fallback_reply_text=fallback_reply_text,
        fallback_reply_succeeded=fallback_reply_succeeded,
        created_at=time.time(),
        notified_at=None,
    )


def _record_failure(
    ctx: HandlerContext,
    meta: "_EventMeta",
    *,
    session_id: str,
    stage: Stage,
    error: BaseException,
    fallback_reply_text: str,
    fallback_reply_succeeded: bool,
) -> FailureEvent:
    event = _build_failure_event(
        meta,
        session_id=session_id,
        stage=stage,
        error=error,
        fallback_reply_text=fallback_reply_text,
        fallback_reply_succeeded=fallback_reply_succeeded,
    )
    failure_event_store.add_event(event)
    ctx.plugins.dispatch_failure(event)
    return event


# deerflow's llm_error_handling_middleware swallows provider exceptions and
# returns a natural-language fallback AIMessage. HarnessBackend.chat flattens
# it to a str, so the only signal we get downstream is the message body. We
# match on prefixes emitted by that middleware (see deerflow source
# ``agents/middlewares/llm_error_handling_middleware.py``) — better than
# leaking a 404 stack straight into a user's comment thread.
_DEERFLOW_ERROR_ANSWER_PREFIXES: tuple[str, ...] = (
    "LLM request failed:",
    "The configured LLM provider ",
)


def _looks_like_backend_error(answer: str) -> bool:
    text = (answer or "").strip()
    return any(text.startswith(prefix) for prefix in _DEERFLOW_ERROR_ANSWER_PREFIXES)


@dataclass
class _EventMeta:
    # Feishu 事件唯一 ID，用于幂等去重。
    event_id: str
    # 当前评论所属文档 token，用于后续读评论、读文档和回帖。
    file_token: str
    # 文档类型，缺省时会退回到 app 配置里的默认 file_type。
    file_type: str
    # 评论线程 ID，用于定位 comment thread。
    comment_id: str
    # 当前触发事件对应的 reply ID，用于回读用户实际那条提问正文。
    reply_id: Optional[str]
    # 发起这条评论/回复的用户 open_id，也是 session_id 的一部分。
    from_open_id: Optional[str]
    # 被 @ 的对象 open_id；首次事件可借此学习 bot 自己的 open_id。
    to_open_id: Optional[str]
    # 是否明确 @ 了 bot；不是 @ 事件时直接跳过。
    is_mentioned: bool


def _extract(event: P2DriveNoticeCommentAddV1) -> Optional[_EventMeta]:
    if event.event is None:
        return None
    ev = event.event
    meta = ev.notice_meta
    if not meta:
        return None
    header = getattr(event, "header", None)
    event_id = getattr(header, "event_id", "") if header else ""
    return _EventMeta(
        event_id=event_id or "",
        file_token=meta.file_token or "",
        file_type=meta.file_type or "",
        comment_id=ev.comment_id or "",
        reply_id=ev.reply_id,
        from_open_id=(meta.from_user_id.open_id if meta.from_user_id else None),
        to_open_id=(meta.to_user_id.open_id if meta.to_user_id else None),
        is_mentioned=bool(ev.is_mentioned),
    )


async def handle_comment_event(
    event: P2DriveNoticeCommentAddV1,
    ctx: HandlerContext,
) -> None:
    meta = _extract(event)
    if meta is None:
        logger.warning("event payload missing notice_meta; skipping")
        return

    header = getattr(event, "header", None)
    ctx.plugins.dispatch_mention(header=header, meta=meta)

    logger.info(
        "event id=%s file=%s comment=%s reply=%s from=%s mentioned=%s",
        meta.event_id, meta.file_token, meta.comment_id, meta.reply_id,
        meta.from_open_id, meta.is_mentioned,
    )

    # 1. event_id dedup
    if meta.event_id and seen_events.is_seen(meta.event_id, ttl_sec=ctx.cfg.event_dedup_ttl_sec):
        logger.info("dedup hit event_id=%s; skipping", meta.event_id)
        return

    # 2. self-trigger guard
    if ctx.bot_open_id and meta.from_open_id == ctx.bot_open_id:
        logger.info("skip self-trigger from bot")
        if meta.event_id:
            seen_events.mark_seen(meta.event_id)
        return

    if not meta.is_mentioned:
        logger.info("event is not an @-mention; skipping")
        if meta.event_id:
            seen_events.mark_seen(meta.event_id)
        return

    if not meta.file_token or not meta.comment_id or not meta.from_open_id:
        logger.warning("missing required fields; cannot reply")
        return

    file_type = meta.file_type or ctx.cfg.file_type_default

    # 3. pull the user's actual question text from the source reply
    user_query = get_reply_text(
        ctx.api_client,
        meta.file_token,
        file_type,
        meta.comment_id,
        meta.reply_id,
        from_user_open_id=meta.from_open_id,
    )
    if not user_query:
        logger.warning("empty user_query; replying with a stub")
        user_query = "(用户的提问内容为空)"

    gate = evaluate_user_query(user_query)
    if gate.blocked:
        refusal_id = post_reply(
            ctx.api_client, meta.file_token, file_type, meta.comment_id,
            at_user_open_id=meta.from_open_id,
            body_text=gate.reply_text,
        )
        if meta.event_id and refusal_id:
            seen_events.mark_seen(meta.event_id)
        return

    thread_history = get_comment_thread_history(
        ctx.api_client,
        meta.file_token,
        file_type,
        meta.comment_id,
        meta.reply_id,
        limit=ctx.cfg.comment_context.default_thread_history_replies,
        max_chars=ctx.cfg.comment_context.default_thread_history_chars,
    )
    allowed_urls = _merge_allowed_urls(
        gate.allowed_urls,
        extract_allowed_urls(thread_history),
    )

    feishu_url_preflight = preflight_feishu_urls(
        client=ctx.api_client,
        cfg=ctx.cfg.url_fetch,
        allowed_urls=allowed_urls,
        app_id=ctx.app_id,
        state_secret=ctx.authorization_state_secret,
        auth_request=UrlAuthorizationRequest(
            source_file_token=meta.file_token,
            source_file_type=file_type,
            comment_id=meta.comment_id,
            reply_id=meta.reply_id or "",
            user_open_id=meta.from_open_id,
        ),
        user_doc_token_store=ctx.user_doc_token_store,
    )
    if not feishu_url_preflight.allowed:
        reply_id = post_reply(
            ctx.api_client, meta.file_token, file_type, meta.comment_id,
            at_user_open_id=meta.from_open_id,
            body_text=feishu_url_preflight.reply_text,
        )
        _record_failure(
            ctx,
            meta,
            session_id=tid.build(meta.file_token, meta.from_open_id),
            stage="url_fetch",
            error=RuntimeError(f"{feishu_url_preflight.reason}: {feishu_url_preflight.url}"),
            fallback_reply_text=feishu_url_preflight.reply_text,
            fallback_reply_succeeded=bool(reply_id),
        )
        if meta.event_id and reply_id:
            seen_events.mark_seen(meta.event_id)
        return

    url_fetch_context = UrlFetchContext(
        client=ctx.api_client,
        cfg=ctx.cfg.url_fetch,
        allowed_urls=allowed_urls,
        user_open_id=meta.from_open_id,
        user_doc_token_store=ctx.user_doc_token_store,
    )

    # 4. Pull the comment anchor. For anchored comments, the quote is the
    # primary context and we deliberately avoid fetching the whole document.
    comment_ctx = get_comment_context(
        ctx.api_client, meta.file_token, file_type, meta.comment_id,
    )
    if not comment_ctx.is_whole and not comment_ctx.quote and not comment_ctx.anchor_block_id:
        reply_id = post_reply(
            ctx.api_client, meta.file_token, file_type, meta.comment_id,
            at_user_open_id=meta.from_open_id,
            body_text=COMMENT_CONTEXT_MISSING_REPLY_TEXT,
        )
        _record_failure(
            ctx,
            meta,
            session_id=tid.build(meta.file_token, meta.from_open_id),
            stage="comment_context",
            error=RuntimeError("missing quote and anchor_block_id for partial comment"),
            fallback_reply_text=COMMENT_CONTEXT_MISSING_REPLY_TEXT,
            fallback_reply_succeeded=bool(reply_id),
        )
        if meta.event_id and reply_id:
            seen_events.mark_seen(meta.event_id)
        return

    context_parts: dict[str, str] = {}
    if comment_ctx.is_whole:
        doc_text = fetch_doc_text(
            ctx.api_client, meta.file_token, file_type,
            ttl_sec=ctx.cfg.doc_cache_ttl_sec,
        )
        if doc_text:
            context_parts["document"] = doc_text
    if thread_history:
        context_parts["comment_thread_history"] = thread_history

    doc_prompt_context = DocPromptContext(
        file_token=meta.file_token,
        comment_id=meta.comment_id,
        user_id=meta.from_open_id,
        quote=comment_ctx.quote,
        contexts=context_parts,
        user_memory_ttl_sec=ctx.cfg.user_memory_ttl_sec,
    )
    doc_context_provider = build_comment_context_provider(
        ctx.api_client,
        ctx.cfg,
        file_token=meta.file_token,
        file_type=file_type,
        comment_ctx=comment_ctx,
        comment_id=meta.comment_id,
        current_reply_id=meta.reply_id or "",
    )

    # 5. serialize same-thread requests; cross-thread runs in parallel
    session_id = tid.build(meta.file_token, meta.from_open_id)
    async with ctx.lock_for(session_id):
        try:
            async with ctx.backend_semaphore:
                answer = await asyncio.wait_for(
                    asyncio.to_thread(
                        ctx.backend.chat,
                        session_id,
                        user_query,
                        doc_context=doc_prompt_context,
                        doc_context_provider=doc_context_provider,
                        url_fetch_context=url_fetch_context,
                    ),
                    timeout=ctx.cfg.backend_timeout_sec,
                )
        except Exception as exc:
            timed_out = isinstance(exc, asyncio.TimeoutError)
            logger.exception("backend chat %s", "timed out" if timed_out else "failed")
            fallback_id = post_reply(
                ctx.api_client, meta.file_token, file_type, meta.comment_id,
                at_user_open_id=meta.from_open_id,
                body_text=ctx.cfg.failure_handling.polite_reply_text,
            )
            _record_failure(
                ctx,
                meta,
                session_id=session_id,
                stage="backend_chat",
                error=exc,
                fallback_reply_text=ctx.cfg.failure_handling.polite_reply_text,
                fallback_reply_succeeded=bool(fallback_id),
            )
            if meta.event_id and fallback_id:
                seen_events.mark_seen(meta.event_id)
            return

    if _looks_like_backend_error(answer or ""):
        logger.warning(
            "backend chat returned a deerflow error-fallback answer; treating as failure: %r",
            (answer or "")[:200],
        )
        fallback_id = post_reply(
            ctx.api_client, meta.file_token, file_type, meta.comment_id,
            at_user_open_id=meta.from_open_id,
            body_text=ctx.cfg.failure_handling.polite_reply_text,
        )
        _record_failure(
            ctx,
            meta,
            session_id=session_id,
            stage="backend_chat",
            error=RuntimeError((answer or "").strip()[:500] or "deerflow error fallback"),
            fallback_reply_text=ctx.cfg.failure_handling.polite_reply_text,
            fallback_reply_succeeded=bool(fallback_id),
        )
        if meta.event_id and fallback_id:
            seen_events.mark_seen(meta.event_id)
        return

    answer = (answer or "").strip() or "(模型返回空)"

    # 6. post the reply
    new_id = post_reply(
        ctx.api_client, meta.file_token, file_type, meta.comment_id,
        at_user_open_id=meta.from_open_id,
        body_text=answer,
    )
    if not new_id:
        _record_failure(
            ctx,
            meta,
            session_id=session_id,
            stage="post_reply",
            error=RuntimeError("post_reply returned no reply_id"),
            fallback_reply_text=ctx.cfg.failure_handling.polite_reply_text,
            fallback_reply_succeeded=False,
        )
        return
    logger.info("posted reply=%s for comment=%s", new_id, meta.comment_id)

    # Distill + persist user memory in the background so the reply path isn't
    # blocked by an extra LLM round-trip. Failures never affect mark_seen.
    _schedule_episode_memory(
        user_id=meta.from_open_id,
        doc_token=meta.file_token,
        comment_id=meta.comment_id,
        user_query=user_query,
        answer=answer,
        quote=comment_ctx.quote,
        timeout_sec=ctx.cfg.episode_summary_timeout_sec,
    )

    if meta.event_id:
        seen_events.mark_seen(meta.event_id)
