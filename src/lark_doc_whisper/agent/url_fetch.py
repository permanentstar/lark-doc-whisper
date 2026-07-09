"""Strict read-only URL fetch tool for comment Q&A."""
from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
import lark_oapi as lark
from langchain_core.tools import tool
from lark_oapi.api.wiki.v2 import GetNodeSpaceRequest

from .doc_context import current_doc_context_bag
from .github_urls import is_github_url
from ..config import UrlFetchConfig
from ..lark.doc_fetcher import fetch_doc_text
from ..security.policy import AllowedUrl


@dataclass(frozen=True)
class UrlFetchContext:
    client: lark.Client | object
    cfg: UrlFetchConfig
    allowed_urls: tuple[AllowedUrl, ...]


@dataclass(frozen=True)
class UrlPreflightResult:
    allowed: bool
    url: str = ""
    reason: str = ""
    reply_text: str = ""


current_url_fetch_context: ContextVar[UrlFetchContext | None] = ContextVar(
    "lark_doc_whisper_url_fetch_context",
    default=None,
)


FEISHU_PERMISSION_REPLY_TEXT = "我暂时没有权限访问这个链接。请先完成授权或把文档权限共享给机器人，然后重新 @我。"


def _normalize(url: str) -> str:
    return url.strip().rstrip(").,]")


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


def _fetch_feishu_text(ctx: UrlFetchContext, url: str, kind: str) -> tuple[str, str]:
    token = _normalize(urlparse(url).path.rsplit("/", 1)[-1])
    if kind == "feishu_docx":
        text = fetch_doc_text(ctx.client, token, "docx", ttl_sec=300)
        return (text, "") if text else ("", "permission_or_auth_required")

    req = GetNodeSpaceRequest.builder().token(token).build()
    resp = ctx.client.wiki.v2.space.get_node(req)
    if not resp.success() or not resp.data or not resp.data.node:
        return "", "permission_or_auth_required"
    if resp.data.node.obj_type != "docx":
        return "", f"unsupported_feishu_type:{resp.data.node.obj_type}"

    text = fetch_doc_text(ctx.client, resp.data.node.obj_token, "docx", ttl_sec=300)
    return (text, "") if text else ("", "permission_or_auth_required")


def preflight_feishu_urls(
    *,
    client: lark.Client | object,
    cfg: UrlFetchConfig,
    allowed_urls: tuple[AllowedUrl, ...],
) -> UrlPreflightResult:
    for candidate in allowed_urls:
        if not candidate.kind.startswith("feishu_"):
            continue
        try:
            _, error = _fetch_feishu_text(
                UrlFetchContext(client=client, cfg=cfg, allowed_urls=allowed_urls),
                candidate.url,
                candidate.kind,
            )
        except Exception:
            error = "permission_or_auth_required"
        if error == "permission_or_auth_required":
            return UrlPreflightResult(
                allowed=False,
                url=candidate.url,
                reason=error,
                reply_text=FEISHU_PERMISSION_REPLY_TEXT,
            )
        if error:
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
