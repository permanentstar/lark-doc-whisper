from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.docx.v1 import ListDocumentBlockRequest

from ..config import AppConfig
from ..lark.comments import CommentContext, get_comment_thread_history
from ..lark.doc_fetcher import fetch_doc_text


def _text_elements_content(text_obj) -> str:
    if text_obj is None or not getattr(text_obj, "elements", None):
        return ""
    parts: list[str] = []
    for element in text_obj.elements:
        text_run = getattr(element, "text_run", None)
        content = getattr(text_run, "content", None)
        if content:
            parts.append(content)
    return "".join(parts).strip()


def _block_heading_level(block) -> Optional[int]:
    for level in range(1, 10):
        if getattr(block, f"heading{level}", None) is not None:
            return level
    return None


def _block_inline_text(block) -> str:
    for attr in ("heading1", "heading2", "heading3", "heading4", "heading5", "heading6", "heading7", "heading8", "heading9"):
        text = _text_elements_content(getattr(block, attr, None))
        if text:
            return text
    for attr in ("text", "bullet", "ordered", "code", "quote", "todo"):
        text = _text_elements_content(getattr(block, attr, None))
        if text:
            return text
    return ""


@dataclass
class CommentContextProvider:
    client: lark.Client
    file_token: str
    file_type: str
    comment_id: str = ""
    current_reply_id: str = ""
    quote: str = ""
    anchor_block_id: str = ""
    default_nearby_before: int = 1
    default_nearby_after: int = 1
    max_nearby_before: int = 3
    max_nearby_after: int = 3
    default_thread_history_replies: int = 8
    default_thread_history_chars: int = 3_000
    max_thread_history_replies: int = 30
    max_thread_history_chars: int = 8_000
    max_context_chars: int = 12_000
    document_summary_chars: int = 4_000
    doc_cache_ttl_sec: int = 300
    enable_section: bool = True
    enable_document_summary: bool = True
    max_fetch_rounds: int = 4
    max_context_chars_total: int = 24_000

    _blocks_by_id: dict[str, object] | None = None
    _top_level_ids: list[str] | None = None
    _fetch_rounds_used: int = 0
    _total_context_chars: int = 0
    _delivered_hashes: set[str] = field(default_factory=set)
    last_unavailable_reason: str = ""

    def resolve(
        self,
        mode: str,
        *,
        before_blocks: Optional[int] = None,
        after_blocks: Optional[int] = None,
        max_chars: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> str:
        self.last_unavailable_reason = ""
        if self._fetch_rounds_used >= self.max_fetch_rounds:
            self.last_unavailable_reason = "max_fetch_rounds"
            return ""

        if mode == "nearby":
            text = self._resolve_nearby(
                before_blocks=before_blocks,
                after_blocks=after_blocks,
                max_chars=max_chars,
            )
            return self._consume_budget(text)
        if mode == "section":
            if not self.enable_section:
                self.last_unavailable_reason = "mode_disabled"
                return ""
            return self._consume_budget(self._resolve_section(max_chars=max_chars))
        if mode in {"document_summary", "summary"}:
            if not self.enable_document_summary:
                self.last_unavailable_reason = "mode_disabled"
                return ""
            return self._consume_budget(self._resolve_document_summary(max_chars=max_chars))
        if mode == "full_excerpt":
            return self._consume_budget(
                self._resolve_section(max_chars=max_chars)
                or self._resolve_document_summary(max_chars=max_chars)
            )
        if mode == "comment_thread_history":
            return self._consume_budget(
                self._resolve_comment_thread_history(limit=limit, max_chars=max_chars)
            )
        self.last_unavailable_reason = "unsupported_mode"
        return ""

    def _requested_max_chars(self, requested: Optional[int], fallback: int) -> int:
        if requested is None:
            return fallback
        return max(1, min(int(requested), fallback))

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        marker = "\n...[truncated]"
        if limit <= len(marker):
            return text[:limit]
        return text[: limit - len(marker)] + marker

    def _consume_budget(self, context_text: str) -> str:
        if not context_text:
            if not self.last_unavailable_reason:
                self.last_unavailable_reason = "no_context"
            return ""

        remaining_chars = self.max_context_chars_total - self._total_context_chars
        if remaining_chars <= 0:
            self.last_unavailable_reason = "max_context_chars_total"
            return ""
        context_text = self._truncate(context_text, remaining_chars)

        digest = hashlib.sha256(context_text.encode("utf-8")).hexdigest()
        if digest in self._delivered_hashes:
            self.last_unavailable_reason = "no_new_info"
            return ""

        self._delivered_hashes.add(digest)
        self._fetch_rounds_used += 1
        self._total_context_chars += len(context_text)
        self.last_unavailable_reason = ""
        return context_text

    def _ensure_loaded(self) -> None:
        if self.file_type != "docx":
            self._blocks_by_id = {}
            self._top_level_ids = []
            return
        if self._blocks_by_id is not None and self._top_level_ids is not None:
            return

        items: list[object] = []
        page_token = ""
        while True:
            builder = (
                ListDocumentBlockRequest.builder()
                .document_id(self.file_token)
                .page_size(200)
            )
            if page_token:
                builder = builder.page_token(page_token)
            resp = self.client.docx.v1.document_block.list(builder.build())
            if not resp.success() or not resp.data or not resp.data.items:
                break
            items.extend(resp.data.items)
            if not resp.data.has_more:
                break
            page_token = resp.data.page_token or ""
            if not page_token:
                break

        self._blocks_by_id = {block.block_id: block for block in items if getattr(block, "block_id", "")}

        root = self._blocks_by_id.get(self.file_token)
        self._top_level_ids = list(getattr(root, "children", []) or [])

    def _top_level_anchor_id(self) -> str:
        self._ensure_loaded()
        if not self._blocks_by_id or self._top_level_ids is None:
            return ""

        if self.anchor_block_id:
            current = self._blocks_by_id.get(self.anchor_block_id)
            while current is not None:
                parent_id = getattr(current, "parent_id", "")
                if parent_id == self.file_token:
                    return current.block_id
                current = self._blocks_by_id.get(parent_id)

        if self.quote:
            for block_id in self._top_level_ids:
                text = self._render_block_text(block_id)
                if self.quote and self.quote in text:
                    return block_id
        return ""

    def _render_block_text(self, block_id: str, *, _seen: Optional[set[str]] = None) -> str:
        self._ensure_loaded()
        if not self._blocks_by_id:
            return ""
        if _seen is None:
            _seen = set()
        if block_id in _seen:
            return ""
        _seen.add(block_id)

        block = self._blocks_by_id.get(block_id)
        if block is None:
            return ""
        parts: list[str] = []
        inline = _block_inline_text(block)
        if inline:
            parts.append(inline)
        for child_id in getattr(block, "children", []) or []:
            child_text = self._render_block_text(child_id, _seen=_seen)
            if child_text:
                parts.append(child_text)
        return "\n".join(part for part in parts if part).strip()

    def _join_blocks(self, block_ids: list[str], *, max_chars: Optional[int] = None) -> str:
        texts = [self._render_block_text(block_id) for block_id in block_ids]
        joined = "\n\n".join(text for text in texts if text).strip()
        return self._truncate(joined, self._requested_max_chars(max_chars, self.max_context_chars))

    def _resolve_nearby(
        self,
        *,
        before_blocks: Optional[int] = None,
        after_blocks: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> str:
        self._ensure_loaded()
        if not self._top_level_ids:
            self.last_unavailable_reason = "no_blocks"
            return ""
        anchor_id = self._top_level_anchor_id()
        if not anchor_id:
            self.last_unavailable_reason = "no_anchor"
            return ""
        try:
            idx = self._top_level_ids.index(anchor_id)
        except ValueError:
            self.last_unavailable_reason = "anchor_not_top_level"
            return ""

        before = self.default_nearby_before if before_blocks is None else max(0, int(before_blocks))
        after = self.default_nearby_after if after_blocks is None else max(0, int(after_blocks))
        before = min(before, self.max_nearby_before)
        after = min(after, self.max_nearby_after)
        start = max(0, idx - before)
        end = min(len(self._top_level_ids), idx + after + 1)
        return self._join_blocks(self._top_level_ids[start:end], max_chars=max_chars)

    def _resolve_section(self, *, max_chars: Optional[int] = None) -> str:
        self._ensure_loaded()
        if not self._top_level_ids or not self._blocks_by_id:
            self.last_unavailable_reason = "no_blocks"
            return ""
        anchor_id = self._top_level_anchor_id()
        if not anchor_id:
            self.last_unavailable_reason = "no_anchor"
            return ""
        try:
            idx = self._top_level_ids.index(anchor_id)
        except ValueError:
            self.last_unavailable_reason = "anchor_not_top_level"
            return ""

        start_idx = idx
        start_level: Optional[int] = None
        for i in range(idx, -1, -1):
            candidate = self._blocks_by_id.get(self._top_level_ids[i])
            level = _block_heading_level(candidate)
            if level is not None:
                start_idx = i
                start_level = level
                break

        if start_level is None:
            return self._join_blocks([anchor_id], max_chars=max_chars)

        end_idx = len(self._top_level_ids)
        for i in range(start_idx + 1, len(self._top_level_ids)):
            candidate = self._blocks_by_id.get(self._top_level_ids[i])
            level = _block_heading_level(candidate)
            if level is not None and level <= start_level:
                end_idx = i
                break
        return self._join_blocks(self._top_level_ids[start_idx:end_idx], max_chars=max_chars)

    def _resolve_document_summary(self, *, max_chars: Optional[int] = None) -> str:
        text = fetch_doc_text(
            self.client,
            self.file_token,
            self.file_type,
            ttl_sec=self.doc_cache_ttl_sec,
        )
        if not text:
            self.last_unavailable_reason = "no_document"
            return ""
        return self._truncate(
            text,
            self._requested_max_chars(max_chars, self.document_summary_chars),
        )

    def _resolve_comment_thread_history(
        self,
        *,
        limit: Optional[int] = None,
        max_chars: Optional[int] = None,
    ) -> str:
        if not self.comment_id:
            self.last_unavailable_reason = "no_comment_id"
            return ""
        requested_limit = (
            self.default_thread_history_replies
            if limit is None
            else max(1, min(int(limit), self.max_thread_history_replies))
        )
        requested_chars = (
            self.default_thread_history_chars
            if max_chars is None
            else max(1, min(int(max_chars), self.max_thread_history_chars))
        )
        text = get_comment_thread_history(
            self.client,
            self.file_token,
            self.file_type,
            self.comment_id,
            self.current_reply_id,
            limit=requested_limit,
            max_chars=requested_chars,
        )
        if not text:
            self.last_unavailable_reason = "no_comment_thread_history"
            return ""
        return text


def build_comment_context_provider(
    client: lark.Client,
    cfg: AppConfig,
    *,
    file_token: str,
    file_type: str,
    comment_ctx: CommentContext,
    comment_id: str = "",
    current_reply_id: str = "",
) -> Optional[CommentContextProvider]:
    if file_type != "docx":
        return None
    if not comment_ctx.quote and not comment_ctx.anchor_block_id:
        return None
    return CommentContextProvider(
        client=client,
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        current_reply_id=current_reply_id,
        quote=comment_ctx.quote,
        anchor_block_id=comment_ctx.anchor_block_id,
        default_nearby_before=cfg.comment_context.default_nearby_before,
        default_nearby_after=cfg.comment_context.default_nearby_after,
        max_nearby_before=cfg.comment_context.max_nearby_before,
        max_nearby_after=cfg.comment_context.max_nearby_after,
        default_thread_history_replies=cfg.comment_context.default_thread_history_replies,
        default_thread_history_chars=cfg.comment_context.default_thread_history_chars,
        max_thread_history_replies=cfg.comment_context.max_thread_history_replies,
        max_thread_history_chars=cfg.comment_context.max_thread_history_chars,
        max_context_chars=cfg.comment_context.max_context_chars,
        max_fetch_rounds=cfg.comment_context.max_fetch_rounds,
        max_context_chars_total=cfg.comment_context.max_context_chars_total,
        document_summary_chars=cfg.comment_context.document_summary_chars,
        doc_cache_ttl_sec=cfg.doc_cache_ttl_sec,
        enable_section=cfg.comment_context.enable_section,
        enable_document_summary=cfg.comment_context.enable_document_summary,
    )
