"""Strict read-only URL fetch tool for comment Q&A."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import socket
import time
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx
import lark_oapi as lark
from langchain_core.tools import tool
from lark_oapi.api.wiki.v2 import GetNodeSpaceRequest

from .doc_context import current_doc_context_bag
from .github_urls import is_github_url
from ..config import UrlAuthorizationConfig, UrlFetchConfig
from ..lark.bitable_fetcher import fetch_bitable_text
from ..lark.doc_fetcher import fetch_doc_text, fetch_doc_text_with_user_access_token
from ..lark.drive_fetcher import fetch_file_metadata_text
from ..lark.sheets_fetcher import fetch_sheet_text
from ..lark.slides_fetcher import fetch_slides_text
from ..lark.whiteboard_fetcher import fetch_whiteboard_text
from ..security.policy import AllowedUrl
from ..state.user_doc_tokens import InMemoryUserDocTokenStore


@dataclass(frozen=True)
class UrlFetchContext:
    client: lark.Client | object
    cfg: UrlFetchConfig
    allowed_urls: tuple[AllowedUrl, ...]
    user_open_id: str = ""
    user_doc_token_store: InMemoryUserDocTokenStore | None = None


@dataclass(frozen=True)
class UrlAuthorizationRequest:
    source_file_token: str
    source_file_type: str
    comment_id: str
    reply_id: str
    user_open_id: str


@dataclass(frozen=True)
class UrlPreflightResult:
    allowed: bool
    url: str = ""
    reason: str = ""
    reply_text: str = ""
    authorization_url: str = ""


current_url_fetch_context: ContextVar[UrlFetchContext | None] = ContextVar(
    "lark_doc_whisper_url_fetch_context",
    default=None,
)


FEISHU_PERMISSION_REPLY_TEXT = "我暂时没有权限访问这个链接。请先完成授权或把文档权限共享给机器人，然后重新 @我。"

_UNSUPPORTED_FEISHU_KIND_LABELS: dict[str, str] = {
    "feishu_sheets": "飞书电子表格",
    "feishu_bitable": "飞书多维表格",
    "feishu_docs": "旧版飞书文档",
    "feishu_mindnote": "飞书思维笔记",
    "feishu_slides": "飞书幻灯片",
    "feishu_file": "飞书云盘文件",
    "feishu_whiteboard": "飞书画板",
}


def _unsupported_feishu_reply(kind: str) -> str:
    label = _UNSUPPORTED_FEISHU_KIND_LABELS.get(kind, "该飞书链接类型")
    return (
        f"我暂时还不支持读取{label}的内容，"
        "可以把关键数据粘贴进评论，或换成 /docx/ 或 /wiki/ 的文档链接后再 @我。"
    )


def _encode_authorization_state(
    *,
    link_url: str,
    link_kind: str,
    auth_request: UrlAuthorizationRequest,
    state_secret: str,
) -> str:
    payload = {
        "action": "feishu_link_doc_authorization",
        "created_at": int(time.time()),
        "link_url": link_url,
        "link_kind": link_kind,
        "source_file_token": auth_request.source_file_token,
        "source_file_type": auth_request.source_file_type,
        "comment_id": auth_request.comment_id,
        "reply_id": auth_request.reply_id,
        "user_open_id": auth_request.user_open_id,
    }
    payload_raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    signature = hmac.new(
        state_secret.encode("utf-8"),
        payload_raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    envelope = {"payload": payload, "sig": signature}
    raw = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_authorization_state(state: str, state_secret: str) -> dict[str, object]:
    """Decode and verify an OAuth state value produced by this module."""
    try:
        padded = state + "=" * (-len(state) % 4)
        envelope = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception as exc:
        raise ValueError("invalid authorization state encoding") from exc

    payload = envelope.get("payload")
    signature = str(envelope.get("sig") or "")
    if not isinstance(payload, dict) or not signature or not state_secret:
        raise ValueError("invalid authorization state signature")

    payload_raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    expected = hmac.new(
        state_secret.encode("utf-8"),
        payload_raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid authorization state signature")
    return payload


def _build_authorization_url(
    *,
    auth_cfg: UrlAuthorizationConfig,
    app_id: str,
    state_secret: str,
    link_url: str,
    link_kind: str,
    auth_request: UrlAuthorizationRequest | None,
) -> str:
    scopes = tuple(
        scope.strip() for scope in auth_cfg.scopes
        if scope.strip() and scope.strip().lower() != "offline_access"
    )
    if (
        not auth_cfg.enabled
        or not app_id
        or not state_secret
        or not auth_cfg.authorize_base_url
        or not auth_cfg.redirect_uri
        or not scopes
        or auth_request is None
    ):
        return ""
    params = {
        "client_id": app_id,
        "response_type": "code",
        "redirect_uri": auth_cfg.redirect_uri,
        "state": _encode_authorization_state(
            link_url=link_url,
            link_kind=link_kind,
            auth_request=auth_request,
            state_secret=state_secret,
        ),
    }
    params["scope"] = " ".join(scopes)
    return f"{auth_cfg.authorize_base_url}?{urlencode(params)}"


def _build_permission_reply_text(*, authorization_url: str) -> str:
    if not authorization_url:
        return FEISHU_PERMISSION_REPLY_TEXT
    return (
        "我可以回复当前文档，但还没有权限读取授权链接里的文档。\n"
        f"请点击下面的飞书授权链接：{authorization_url}\n"
        "授权后我会在 token 有效期内仅以你的身份读取这个链接文档；"
        "服务重启、授权过期或读取失败后，需要重新授权。"
        "完成后请重新 @我。"
    )


def _normalize(url: str) -> str:
    return url.strip().rstrip(").,]")


def _query_param(query: str, name: str) -> str | None:
    values = parse_qs(query).get(name) if query else None
    return values[0] if values else None


def _allowed_url(ctx: UrlFetchContext, url: str) -> AllowedUrl | None:
    normalized = _normalize(url)
    for candidate in ctx.allowed_urls:
        if _normalize(candidate.url) == normalized:
            return candidate
    return None


def _build_url_receipt(*, url: str, context_text: str) -> str:
    digest = hashlib.sha256(context_text.encode("utf-8")).hexdigest()[:16]
    return f"[url content attached: url={url}, chars={len(context_text)}, sha256={digest}]"


def _reject_private_host(url: str, *, allow_private_ip: bool) -> str:
    if allow_private_ip:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return f"unsupported_scheme:{parsed.scheme or 'missing'}"
    hostname = parsed.hostname
    if not hostname:
        return "missing_host"
    try:
        addresses = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        return f"dns_error:{exc.__class__.__name__}"
    for _, _, _, _, sockaddr in addresses:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            return f"private_address:{ip}"
    return ""


def _html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", "", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", "", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_external_text(ctx: UrlFetchContext, url: str) -> tuple[str, str]:
    current = url
    for _ in range(ctx.cfg.max_redirects + 1):
        host_error = _reject_private_host(current, allow_private_ip=ctx.cfg.allow_private_ip)
        if host_error:
            return "", host_error
        try:
            with httpx.Client(follow_redirects=False, timeout=ctx.cfg.timeout_sec) as client:
                with client.stream("GET", current, headers={"User-Agent": "lark-doc-whisper/0.1"}) as resp:
                    if 300 <= resp.status_code < 400 and "location" in resp.headers:
                        current = urljoin(current, resp.headers["location"])
                        continue
                    if resp.status_code >= 400:
                        return "", f"http_status:{resp.status_code}"
                    content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
                    if content_type not in ctx.cfg.allowed_content_types:
                        return "", f"unsupported_content_type:{content_type or 'unknown'}"
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > ctx.cfg.max_response_bytes:
                            return "", "response_too_large"
                        chunks.append(chunk)
                    text = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                    if content_type == "text/html":
                        text = _html_to_text(text)
                    return text.strip(), ""
        except httpx.HTTPError as exc:
            return "", f"http_error:{exc.__class__.__name__}"
    return "", "too_many_redirects"


def _fetch_feishu_text_as_bot(ctx: UrlFetchContext, url: str, kind: str) -> tuple[str, str]:
    parsed = urlparse(url)
    token = _normalize(parsed.path.rsplit("/", 1)[-1])
    if kind == "feishu_docx":
        text = fetch_doc_text(ctx.client, token, "docx", ttl_sec=300)
        return (text, "") if text else ("", "permission_or_auth_required")

    if kind == "feishu_sheets":
        sheet_id = _query_param(parsed.query, "sheet")
        text = fetch_sheet_text(ctx.client, token, sheet_id=sheet_id, max_rows=200)
        return (text, "") if text else ("", "permission_or_auth_required")

    if kind == "feishu_bitable":
        table_id = _query_param(parsed.query, "table") or _query_param(parsed.query, "table_id")
        text = fetch_bitable_text(ctx.client, token, table_id=table_id, max_rows=200)
        return (text, "") if text else ("", "permission_or_auth_required")

    if kind == "feishu_slides":
        text = fetch_slides_text(ctx.client, token)
        return (text, "") if text else ("", "permission_or_auth_required")

    if kind == "feishu_file":
        text = fetch_file_metadata_text(ctx.client, token, "file")
        return (text, "") if text else ("", "permission_or_auth_required")

    if kind == "feishu_whiteboard":
        text = fetch_whiteboard_text(ctx.client, token)
        return (text, "") if text else ("", "permission_or_auth_required")

    if kind != "feishu_wiki":
        return "", f"unsupported_feishu_type:{kind}"

    req = GetNodeSpaceRequest.builder().token(token).build()
    resp = ctx.client.wiki.v2.space.get_node(req)
    if not resp.success() or not resp.data or not resp.data.node:
        return "", "permission_or_auth_required"
    obj_type = str(resp.data.node.obj_type or "")
    obj_token = str(resp.data.node.obj_token or "")
    if obj_type == "docx":
        text = fetch_doc_text(ctx.client, obj_token, "docx", ttl_sec=300)
        return (text, "") if text else ("", "permission_or_auth_required")
    if obj_type in {"sheet", "sheets"}:
        text = fetch_sheet_text(ctx.client, obj_token, sheet_id=None, max_rows=200)
        return (text, "") if text else ("", "permission_or_auth_required")
    if obj_type == "bitable":
        text = fetch_bitable_text(ctx.client, obj_token, table_id=None, max_rows=200)
        return (text, "") if text else ("", "permission_or_auth_required")
    if obj_type == "slides":
        text = fetch_slides_text(ctx.client, obj_token)
        return (text, "") if text else ("", "permission_or_auth_required")
    if obj_type == "file":
        text = fetch_file_metadata_text(ctx.client, obj_token, "file")
        return (text, "") if text else ("", "permission_or_auth_required")
    if obj_type in {"whiteboard", "board"}:
        text = fetch_whiteboard_text(ctx.client, obj_token)
        return (text, "") if text else ("", "permission_or_auth_required")
    return "", f"unsupported_feishu_type:{obj_type}"


def _fetch_feishu_text_with_user_token(ctx: UrlFetchContext, url: str, kind: str) -> tuple[str, str]:
    if ctx.user_doc_token_store is None or not ctx.user_open_id:
        return "", "permission_or_auth_required"
    access_token = ctx.user_doc_token_store.get(ctx.user_open_id, url)
    if not access_token:
        return "", "permission_or_auth_required"

    token = _normalize(urlparse(url).path.rsplit("/", 1)[-1])
    if kind != "feishu_docx":
        return "", f"unsupported_feishu_user_token_type:{kind}"

    text = fetch_doc_text_with_user_access_token(
        access_token,
        token,
        "docx",
        timeout_sec=ctx.cfg.timeout_sec,
    )
    if text:
        return text, ""
    ctx.user_doc_token_store.delete(ctx.user_open_id, url)
    return "", "permission_or_auth_required"


def _fetch_feishu_text(ctx: UrlFetchContext, url: str, kind: str) -> tuple[str, str]:
    text, error = _fetch_feishu_text_as_bot(ctx, url, kind)
    if not error:
        return text, ""
    if error != "permission_or_auth_required":
        return "", error
    return _fetch_feishu_text_with_user_token(ctx, url, kind)


def preflight_feishu_urls(
    *,
    client: lark.Client | object,
    cfg: UrlFetchConfig,
    allowed_urls: tuple[AllowedUrl, ...],
    app_id: str = "",
    state_secret: str = "",
    auth_request: UrlAuthorizationRequest | None = None,
    user_doc_token_store: InMemoryUserDocTokenStore | None = None,
) -> UrlPreflightResult:
    for candidate in allowed_urls:
        if not candidate.kind.startswith("feishu_"):
            continue
        try:
            _, error = _fetch_feishu_text(
                UrlFetchContext(
                    client=client,
                    cfg=cfg,
                    allowed_urls=allowed_urls,
                    user_open_id=auth_request.user_open_id if auth_request else "",
                    user_doc_token_store=user_doc_token_store,
                ),
                candidate.url,
                candidate.kind,
            )
        except Exception:
            error = "permission_or_auth_required"
        if error == "permission_or_auth_required":
            authorization_url = _build_authorization_url(
                auth_cfg=cfg.authorization,
                app_id=app_id,
                state_secret=state_secret,
                link_url=candidate.url,
                link_kind=candidate.kind,
                auth_request=auth_request,
            )
            return UrlPreflightResult(
                allowed=False,
                url=candidate.url,
                reason=error,
                reply_text=_build_permission_reply_text(authorization_url=authorization_url),
                authorization_url=authorization_url,
            )
        if error:
            if error.startswith("unsupported_feishu_type:"):
                return UrlPreflightResult(
                    allowed=False,
                    url=candidate.url,
                    reason=error,
                    reply_text=_unsupported_feishu_reply(candidate.kind),
                )
            return UrlPreflightResult(
                allowed=False,
                url=candidate.url,
                reason=error,
                reply_text=f"我暂时无法读取这个飞书链接（{error}）。请确认链接类型和权限后重新 @我。",
            )
    return UrlPreflightResult(allowed=True)


@tool("fetch_url_content")
def fetch_url_content_tool(url: str, reason: str = "") -> str:
    """Attach content from an approved read-only URL for this comment request."""
    ctx = current_url_fetch_context.get()
    if ctx is None or not ctx.cfg.enabled:
        return "[url content unavailable: no active url fetch context]"

    candidate = _allowed_url(ctx, url)
    if candidate is None:
        return f"[url content unavailable: url={url}, reason=not allowed]"

    if candidate.kind.startswith("feishu_"):
        content, error = _fetch_feishu_text(ctx, candidate.url, candidate.kind)
    elif is_github_url(candidate.url):
        return f"[url content unavailable: url={candidate.url}, reason=GitHub URL must use GitHub MCP]"
    else:
        content, error = _fetch_external_text(ctx, candidate.url)

    if error:
        return f"[url content unavailable: url={candidate.url}, reason={error}]"

    bag = current_doc_context_bag.get()
    if bag is None:
        bag = {}
        current_doc_context_bag.set(bag)
    bag["url_content"] = f"<url-content url=\"{candidate.url}\">\n{content}\n</url-content>"
    return _build_url_receipt(url=candidate.url, context_text=bag["url_content"])
