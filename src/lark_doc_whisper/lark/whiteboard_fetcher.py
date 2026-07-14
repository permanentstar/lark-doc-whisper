"""Fetch Feishu whiteboard nodes as a text flow skeleton."""
from __future__ import annotations

import logging
from typing import Any

import lark_oapi as lark
from lark_oapi.api.board.v1 import ListWhiteboardNodeRequest

logger = logging.getLogger(__name__)


def _node_text(node: Any) -> str:
    text_obj = getattr(node, "text", None)
    plain = str(getattr(text_obj, "text", "") or "").strip()
    if plain:
        return plain
    rich = getattr(text_obj, "rich_text", None)
    parts: list[str] = []
    for paragraph in (getattr(rich, "paragraphs", None) or []):
        for element in (getattr(paragraph, "elements", None) or []):
            text_element = getattr(element, "text_element", None)
            value = str(getattr(text_element, "text", "") or "").strip()
            if value:
                parts.append(value)
    return "".join(parts).strip()


def _connector_caption(connector: Any) -> str:
    captions = getattr(connector, "captions", None)
    parts = [
        str(getattr(text, "text", "") or "").strip()
        for text in (getattr(captions, "data", None) or [])
        if str(getattr(text, "text", "") or "").strip()
    ]
    return " / ".join(parts)


def fetch_whiteboard_text(client: lark.Client, whiteboard_token: str) -> str:
    """Return nodes and connector relationships from a whiteboard."""
    req = ListWhiteboardNodeRequest.builder().whiteboard_id(whiteboard_token).build()
    try:
        resp = client.board.v1.whiteboard_node.list(req)
    except Exception:
        logger.warning("whiteboard node list failed token=%s", whiteboard_token, exc_info=True)
        return ""
    if not resp.success() or not getattr(resp, "data", None):
        logger.warning(
            "whiteboard node list failed token=%s code=%s msg=%s",
            whiteboard_token, getattr(resp, "code", "?"), getattr(resp, "msg", ""),
        )
        return ""

    nodes = list(getattr(resp.data, "nodes", None) or [])
    if not nodes:
        return ""

    labels: dict[str, str] = {}
    normal_lines: list[str] = []
    connector_lines: list[str] = []
    for node in nodes:
        node_id = str(getattr(node, "id", "") or "")
        node_type = str(getattr(node, "type", "") or "unknown")
        text = _node_text(node)
        if node_id:
            labels[node_id] = text or node_id
        if node_type == "connector" or getattr(node, "connector", None):
            continue
        normal_lines.append(f"- {node_id} [{node_type}] {text or '(无文本)'}")

    for node in nodes:
        connector = getattr(node, "connector", None)
        if not connector:
            continue
        start_id = str(getattr(getattr(connector, "start_object", None), "id", "") or "")
        end_id = str(getattr(getattr(connector, "end_object", None), "id", "") or "")
        start = labels.get(start_id, start_id or "?")
        end = labels.get(end_id, end_id or "?")
        caption = _connector_caption(connector)
        connector_lines.append(f"- {start} --{caption}--> {end}" if caption else f"- {start} --> {end}")

    lines = [
        f"飞书画板流程信息：共 {len(nodes)} 个节点",
        "",
        "节点：",
        *(normal_lines or ["- (无可读文本节点)"]),
    ]
    if connector_lines:
        lines.extend(["", "连线：", *connector_lines])
    return "\n".join(lines)
