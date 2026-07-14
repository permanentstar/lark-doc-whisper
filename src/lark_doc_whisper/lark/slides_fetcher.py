"""Fetch Feishu Slides XML and render visible text for LLM context."""
from __future__ import annotations

import json
import logging
import re
from html import unescape
from urllib.parse import quote

import lark_oapi as lark
from lark_oapi.core.enum import AccessTokenType, HttpMethod

logger = logging.getLogger(__name__)

MAX_SLIDES_TEXT_CHARS = 50_000


def _visible_text_from_xml(xml: str) -> str:
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", xml)
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def fetch_slides_text(client: lark.Client, presentation_token: str) -> str:
    """Return a compact text view of a Slides presentation."""
    req = (
        lark.BaseRequest.builder()
        .http_method(HttpMethod.GET)
        .uri(f"/open-apis/slides_ai/v1/xml_presentations/{quote(presentation_token)}")
        .token_types({AccessTokenType.TENANT})
        .build()
    )
    try:
        resp = client.request(req)
    except Exception:
        logger.warning("slides xml presentation get failed token=%s", presentation_token, exc_info=True)
        return ""
    if not resp.success():
        logger.warning(
            "slides xml presentation get failed token=%s code=%s msg=%s",
            presentation_token, getattr(resp, "code", "?"), getattr(resp, "msg", ""),
        )
        return ""
    content = resp.raw.content if resp.raw is not None else b""
    if not content:
        return ""
    try:
        payload = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content)
    except Exception:
        logger.warning("slides xml presentation returned non-json token=%s", presentation_token, exc_info=True)
        return ""

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    presentation = (data or {}).get("xml_presentation") or {}
    xml = str(presentation.get("content") or "")
    if not xml:
        return ""
    visible = _visible_text_from_xml(xml)
    if len(visible) > MAX_SLIDES_TEXT_CHARS:
        visible = visible[:MAX_SLIDES_TEXT_CHARS] + "\n...[truncated]"
    return (
        "飞书幻灯片："
        f"presentation_id={presentation.get('presentation_id') or presentation_token}, "
        f"revision_id={presentation.get('revision_id')}\n"
        f"{visible}"
    )
