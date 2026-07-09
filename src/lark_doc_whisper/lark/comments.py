"""Read replies + post a reply (with optional @-user) on a doc comment."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import lark_oapi as lark
from lark_oapi.api.drive.v1 import (
    BatchQueryFileCommentRequest,
    BatchQueryFileCommentRequestBody,
    CreateFileCommentReplyRequest,
    FileCommentReply,
    ListFileCommentReplyRequest,
    ReplyContent,
    ReplyElement,
)


@dataclass(frozen=True)
class CommentContext:
    quote: str = ""
    is_whole: bool = False
    anchor_block_id: str = ""


def _anchor_block_id_from_relation(relation) -> str:
    if relation is None or bool(getattr(relation, "content_deleted", False)):
        return ""
    raw = getattr(relation, "relation", "") or ""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except Exception:
        return ""
    for value in data.values():
        position_info = value.get("positionInfo") or {}
        block_id = position_info.get("blockID") or ""
        if block_id:
            return str(block_id)
    return ""


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    marker = "\n...[truncated]"
    if limit <= len(marker):
        return text[:limit]
    return text[: limit - len(marker)] + marker


def _reply_plain_text(reply) -> str:
    if reply is None or not getattr(reply, "content", None) or not getattr(reply.content, "elements", None):
        return ""
    parts: list[str] = []
    for el in reply.content.elements:
        if el.type == "text_run" and el.text_run and el.text_run.text:
            parts.append(el.text_run.text)
        elif el.type == "docs_link" and getattr(el, "docs_link", None):
            url = str(getattr(el.docs_link, "url", "") or "").strip()
            if url:
                parts.append(url)
    return " ".join(part.strip() for part in parts if part and part.strip()).strip()


def get_reply_text(
    client: lark.Client,
    file_token: str,
    file_type: str,
    comment_id: str,
    reply_id: Optional[str],
) -> str:
    """Pull the source reply (or its parent comment's last reply) text.

    Returns "" if anything fails — callers are expected to handle the empty case.
    """
    req = (
        ListFileCommentReplyRequest.builder()
        .file_token(file_token)
        .comment_id(comment_id)
        .file_type(file_type)
        .user_id_type("open_id")
        .page_size(100)
        .build()
    )
    resp = client.drive.v1.file_comment_reply.list(req)
    if not resp.success():
        return ""
    items = (resp.data.items if resp.data and resp.data.items else []) or []
    target = None
    if reply_id:
        for r in items:
            if r.reply_id == reply_id:
                target = r
                break
    if target is None and items:
        target = items[-1]
    return _reply_plain_text(target)


def get_comment_thread_history(
    client: lark.Client,
    file_token: str,
    file_type: str,
    comment_id: str,
    current_reply_id: Optional[str],
    *,
    limit: int,
    max_chars: int,
) -> str:
    """Return recent replies before the current reply in the same comment thread."""
    if not comment_id or limit <= 0 or max_chars <= 0:
        return ""
    req = (
        ListFileCommentReplyRequest.builder()
        .file_token(file_token)
        .comment_id(comment_id)
        .file_type(file_type)
        .user_id_type("open_id")
        .page_size(100)
        .build()
    )
    try:
        resp = client.drive.v1.file_comment_reply.list(req)
    except Exception:
        return ""
    if not resp.success():
        return ""

    items = (resp.data.items if resp.data and resp.data.items else []) or []
    if not items:
        return ""

    current_idx = len(items)
    if current_reply_id:
        for idx, reply in enumerate(items):
            if getattr(reply, "reply_id", "") == current_reply_id:
                current_idx = idx
                break
    candidates = items[:current_idx]

    rows: list[tuple[str, str]] = []
    for reply in candidates:
        text = _reply_plain_text(reply)
        if text:
            rows.append((str(getattr(reply, "reply_id", "") or ""), text))
    rows = rows[-max(1, int(limit)):]
    if not rows:
        return ""

    lines: list[str] = []
    for idx, (reply_id, text) in enumerate(rows, start=1):
        lines.extend([
            f'<reply index="{idx}" reply_id="{reply_id}">',
            text,
            "</reply>",
        ])
    return _truncate("\n".join(lines), int(max_chars))


def get_comment_context(
    client: lark.Client,
    file_token: str,
    file_type: str,
    comment_id: str,
) -> CommentContext:
    """Fetch comment metadata for both whole-doc and anchored comments."""
    if not comment_id:
        return CommentContext()

    body = (
        BatchQueryFileCommentRequestBody.builder()
        .comment_ids([comment_id])
        .build()
    )
    req = (
        BatchQueryFileCommentRequest.builder()
        .file_token(file_token)
        .file_type(file_type)
        .user_id_type("open_id")
        .request_body(body)
        .build()
    )
    resp = client.drive.v1.file_comment.batch_query(req)
    if not resp.success() or not resp.data or not resp.data.items:
        return CommentContext()

    for item in resp.data.items:
        if str(item.comment_id or "") == comment_id:
            return CommentContext(
                quote=(item.quote or "").strip(),
                is_whole=bool(item.is_whole),
                anchor_block_id=_anchor_block_id_from_relation(getattr(item, "relation", None)),
            )

    return CommentContext()


def post_reply(
    client: lark.Client,
    file_token: str,
    file_type: str,
    comment_id: str,
    *,
    at_user_open_id: Optional[str],
    body_text: str,
) -> Optional[str]:
    """Post a text reply (optionally @ a user) to a comment thread.

    Returns the new reply_id on success, ``None`` on failure.
    """
    elements: list[ReplyElement] = []
    if at_user_open_id:
        person = ReplyElement.builder().type("person").build()
        person.person = {"user_id": at_user_open_id}
        elements.append(person)
        sp = ReplyElement.builder().type("text_run").build()
        sp.text_run = {"text": " "}
        elements.append(sp)
    tr = ReplyElement.builder().type("text_run").build()
    tr.text_run = {"text": body_text}
    elements.append(tr)

    content = ReplyContent.builder().elements(elements).build()
    body = FileCommentReply.builder().content(content).build()
    req = (
        CreateFileCommentReplyRequest.builder()
        .file_token(file_token)
        .comment_id(comment_id)
        .file_type(file_type)
        .user_id_type("open_id")
        .request_body(body)
        .build()
    )
    resp = client.drive.v1.file_comment_reply.create(req)
    if not resp.success():
        return None
    return resp.data.reply_id if resp.data else None
