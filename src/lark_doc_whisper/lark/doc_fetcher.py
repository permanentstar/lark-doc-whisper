"""Fetch a Lark doc's plain text with a 5 min on-disk cache.

Currently only handles docx (the default file_type for our test bot). Other
file types raise — extend when needed.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx
import lark_oapi as lark
from lark_oapi.api.docx.v1 import RawContentDocumentRequest

from ..state.paths import DOC_CACHE_DIR

logger = logging.getLogger(__name__)

MAX_DOC_TEXT_CHARS = 50_000  # safety cap so a huge doc cannot blow the LLM context


def _cache_path(file_token: str) -> Path:
    return DOC_CACHE_DIR / f"{file_token}.json"


def _read_cache(file_token: str, ttl_sec: int) -> str | None:
    p = _cache_path(file_token)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception:
        return None
    if time.time() - float(data.get("ts", 0)) >= ttl_sec:
        return None
    return data.get("text")


def _write_cache(file_token: str, text: str) -> None:
    DOC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _cache_path(file_token).with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump({"ts": time.time(), "text": text}, f)
    tmp.replace(_cache_path(file_token))


def fetch_doc_text(
    client: lark.Client,
    file_token: str,
    file_type: str,
    *,
    ttl_sec: int = 300,
) -> str:
    """Return plain text of a doc (cached up to *ttl_sec*).

    Truncated to MAX_DOC_TEXT_CHARS to keep prompts bounded.
    """
    if file_type != "docx":
        raise NotImplementedError(f"doc_fetcher only supports docx, got {file_type}")

    cached = _read_cache(file_token, ttl_sec)
    if cached is not None:
        return cached

    req = RawContentDocumentRequest.builder().document_id(file_token).build()
    resp = client.docx.v1.document.raw_content(req)
    if not resp.success():
        logger.warning(
            "raw_content failed token=%s code=%s msg=%s",
            file_token, resp.code, resp.msg,
        )
        return ""
    text = (resp.data.content or "") if resp.data else ""
    if len(text) > MAX_DOC_TEXT_CHARS:
        text = text[:MAX_DOC_TEXT_CHARS] + "\n...[truncated]"
    _write_cache(file_token, text)
    return text


def fetch_doc_text_with_user_access_token(
    access_token: str,
    file_token: str,
    file_type: str,
    *,
    timeout_sec: int = 8,
) -> str:
    """Return doc text with a short-lived user token, without persistent cache."""
    if file_type != "docx":
        raise NotImplementedError(f"doc_fetcher only supports docx, got {file_type}")
    if not access_token:
        return ""

    try:
        resp = httpx.get(
            f"https://open.feishu.cn/open-apis/docx/v1/documents/{file_token}/raw_content",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=timeout_sec,
        )
        if resp.status_code >= 400:
            logger.warning(
                "user raw_content failed token=%s status=%s",
                file_token, resp.status_code,
            )
            return ""
        data = resp.json()
    except Exception:
        logger.warning("user raw_content request failed token=%s", file_token, exc_info=True)
        return ""

    if int(data.get("code") or 0) != 0:
        logger.warning(
            "user raw_content failed token=%s code=%s msg=%s",
            file_token, data.get("code"), data.get("msg"),
        )
        return ""
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    text = str((payload or {}).get("content") or "")
    if len(text) > MAX_DOC_TEXT_CHARS:
        text = text[:MAX_DOC_TEXT_CHARS] + "\n...[truncated]"
    return text
