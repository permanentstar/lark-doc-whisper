"""Fetch a Feishu Bitable/Base as compact Markdown for LLM context."""
from __future__ import annotations

import json
import logging
from typing import Any

import lark_oapi as lark
from lark_oapi.api.bitable.v1 import (
    ListAppTableRecordRequest,
    ListAppTableFieldRequest,
    ListAppTableRequest,
)

logger = logging.getLogger(__name__)

MAX_BITABLE_ROWS = 200
MAX_BITABLE_FIELDS = 30


def _ok(resp: object) -> bool:
    try:
        return bool(resp.success())
    except Exception:
        return False


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).replace("|", "/").replace("\n", " ").strip()


def _render_row(values: list[Any]) -> str:
    return "| " + " | ".join(_cell(value) for value in values) + " |"


def _render_markdown(table_name: str, fields: list[str], records: list[dict[str, Any]]) -> str:
    if not fields:
        return f"### {table_name}\n(无字段)"
    visible_fields = fields[:MAX_BITABLE_FIELDS]
    lines = [
        f"### {table_name}",
        _render_row(visible_fields),
        "| " + " | ".join(["---"] * len(visible_fields)) + " |",
    ]
    for record in records:
        lines.append(_render_row([record.get(field) for field in visible_fields]))
    return "\n".join(lines)


def fetch_bitable_text(
    client: lark.Client,
    app_token: str,
    *,
    table_id: str | None,
    max_rows: int = MAX_BITABLE_ROWS,
) -> str:
    """Return a compact markdown snapshot of one bitable table.

    If ``table_id`` is missing or unknown, the first table is rendered and other
    tables are listed as a summary. Returns ``""`` on API failure.
    """
    table_req = (
        ListAppTableRequest.builder()
        .app_token(app_token)
        .page_size(100)
        .build()
    )
    try:
        table_resp = client.bitable.v1.app_table.list(table_req)
    except Exception:
        logger.warning("bitable table list failed app_token=%s", app_token, exc_info=True)
        return ""
    if not _ok(table_resp) or not getattr(table_resp, "data", None):
        logger.warning(
            "bitable table list failed app_token=%s code=%s msg=%s",
            app_token, getattr(table_resp, "code", "?"), getattr(table_resp, "msg", ""),
        )
        return ""

    tables = list(getattr(table_resp.data, "items", None) or [])
    if not tables:
        return ""

    target = None
    if table_id:
        for table in tables:
            if str(getattr(table, "table_id", "") or "") == table_id:
                target = table
                break
    if target is None:
        target = tables[0]

    target_id = str(getattr(target, "table_id", "") or "")
    target_name = str(getattr(target, "name", "") or target_id)

    field_req = (
        ListAppTableFieldRequest.builder()
        .app_token(app_token)
        .table_id(target_id)
        .page_size(100)
        .build()
    )
    try:
        field_resp = client.bitable.v1.app_table_field.list(field_req)
    except Exception:
        logger.warning("bitable field list failed app_token=%s table_id=%s", app_token, target_id, exc_info=True)
        return ""
    if not _ok(field_resp) or not getattr(field_resp, "data", None):
        return ""
    fields = [
        str(getattr(field, "field_name", "") or "")
        for field in (getattr(field_resp.data, "items", None) or [])
        if str(getattr(field, "field_name", "") or "")
    ]

    row_cap = max(1, min(int(max_rows), MAX_BITABLE_ROWS))
    record_req = (
        ListAppTableRecordRequest.builder()
        .app_token(app_token)
        .table_id(target_id)
        .page_size(row_cap)
        .text_field_as_array(False)
        .build()
    )
    try:
        record_resp = client.bitable.v1.app_table_record.list(record_req)
    except Exception:
        logger.warning("bitable record search failed app_token=%s table_id=%s", app_token, target_id, exc_info=True)
        return ""
    if not _ok(record_resp) or not getattr(record_resp, "data", None):
        return ""
    records = [
        dict(getattr(record, "fields", None) or {})
        for record in (getattr(record_resp.data, "items", None) or [])
    ]

    other = [
        f"- {getattr(table, 'name', '') or getattr(table, 'table_id', '')}"
        for table in tables
        if str(getattr(table, "table_id", "") or "") != target_id
    ]
    summary = [
        f"飞书多维表格：共 {len(tables)} 张表；当前渲染：{target_name}",
    ]
    if other:
        summary.append("其他表：")
        summary.extend(other)

    return "\n".join(summary + ["", _render_markdown(target_name, fields, records)])
