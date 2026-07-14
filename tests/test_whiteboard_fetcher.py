"""Unit tests for Feishu whiteboard flow extraction."""
from __future__ import annotations

from types import SimpleNamespace

from lark_doc_whisper.lark.whiteboard_fetcher import fetch_whiteboard_text


def _text(value: str):
    return SimpleNamespace(text=value)


def _node(node_id: str, node_type: str, text: str = "", connector=None):
    return SimpleNamespace(id=node_id, type=node_type, text=_text(text) if text else None, connector=connector)


def test_fetch_whiteboard_text_extracts_nodes_and_connectors():
    calls = []

    def _list(req):
        calls.append(req)
        connector = SimpleNamespace(
            start_object=SimpleNamespace(id="n1"),
            end_object=SimpleNamespace(id="n2"),
            captions=SimpleNamespace(data=[_text("通过")]),
        )
        return SimpleNamespace(
            success=lambda: True,
            code=0,
            msg="",
            data=SimpleNamespace(
                nodes=[
                    _node("n1", "shape", "开始"),
                    _node("n2", "shape", "结束"),
                    _node("c1", "connector", "", connector=connector),
                ]
            ),
        )

    client = SimpleNamespace(board=SimpleNamespace(v1=SimpleNamespace(whiteboard_node=SimpleNamespace(list=_list))))

    text = fetch_whiteboard_text(client, "board_tok")
    assert "飞书画板流程信息" in text
    assert "- n1 [shape] 开始" in text
    assert "- n2 [shape] 结束" in text
    assert "- 开始 --通过--> 结束" in text
    assert calls[0].whiteboard_id == "board_tok"


def test_fetch_whiteboard_text_returns_empty_on_failure():
    def _list(req):
        return SimpleNamespace(success=lambda: False, code=91403, msg="permission denied", data=None)

    client = SimpleNamespace(board=SimpleNamespace(v1=SimpleNamespace(whiteboard_node=SimpleNamespace(list=_list))))
    assert fetch_whiteboard_text(client, "board_tok") == ""
