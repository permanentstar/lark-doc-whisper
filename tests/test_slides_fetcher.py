"""Unit tests for Feishu Slides XML fetcher."""
from __future__ import annotations

import json
from types import SimpleNamespace

from lark_doc_whisper.lark.slides_fetcher import fetch_slides_text


def _client(payload):
    calls = []

    def _request(req):
        calls.append(req)
        return SimpleNamespace(
            success=lambda: int(payload.get("code", 0)) == 0,
            code=int(payload.get("code", 0)),
            msg=str(payload.get("msg") or ""),
            raw=SimpleNamespace(content=json.dumps(payload).encode("utf-8")),
        )

    return SimpleNamespace(request=_request), calls


def test_fetch_slides_text_extracts_visible_text_from_xml():
    xml = (
        '<presentation><slide><shape><text><p>第一页标题</p><p>要点 A</p></text></shape></slide>'
        '<slide><shape><text><p>第二页</p></text></shape></slide></presentation>'
    )
    client, calls = _client(
        {
            "code": 0,
            "data": {
                "xml_presentation": {
                    "content": xml,
                    "presentation_id": "sl_tok",
                    "revision_id": 12,
                }
            },
        }
    )

    text = fetch_slides_text(client, "sl_tok")
    assert "飞书幻灯片" in text
    assert "revision_id=12" in text
    assert "第一页标题 要点 A 第二页" in text
    assert calls[0].uri == "/open-apis/slides_ai/v1/xml_presentations/sl_tok"


def test_fetch_slides_text_returns_empty_on_failure():
    client, _ = _client({"code": 91403, "msg": "permission denied"})
    assert fetch_slides_text(client, "sl_tok") == ""
