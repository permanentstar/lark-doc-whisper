"""Fetch a Feishu spreadsheet as plain-text Markdown for LLM consumption.

Only tenant-token (bot) reads are supported here; there is no user-token
fallback yet. All requests go through ``lark.Client.request`` with a
``lark.BaseRequest``, so tenant token acquisition and signing are handled
by the SDK.
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import lark_oapi as lark
from lark_oapi.core.enum import AccessTokenType, HttpMethod

logger = logging.getLogger(__name__)

# Bound per-sheet render size. The caller applies a global cap on top of this.
MAX_ROWS_PER_SHEET = 200
MAX_COLS_PER_SHEET = 26  # A..Z is plenty for a summary; wide sheets get truncated


def _request_json(client: lark.Client, *, method: HttpMethod, uri: str) -> dict[str, Any]:
    req = (
        lark.BaseRequest.builder()
        .http_method(method)
        .uri(uri)
        .token_types({AccessTokenType.TENANT})
        .build()
    )
    resp = client.request(req)
    if not resp.success():
        logger.warning(
            "sheets api failed uri=%s code=%s msg=%s",
            uri, getattr(resp, "code", "?"), getattr(resp, "msg", ""),
        )
        return {}
    content = resp.raw.content if resp.raw is not None else b""
    if not content:
        return {}
    try:
        return json.loads(content.decode("utf-8") if isinstance(content, bytes) else content)
    except Exception:
        logger.warning("sheets api returned non-json uri=%s", uri, exc_info=True)
        return {}


def _col_letter(index: int) -> str:
    """1-based column index -> spreadsheet letter (A, B, ..., Z, AA)."""
    result = ""
    n = index
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(ord("A") + rem) + result
    return result or "A"


def _render_row(cells: list[Any]) -> str:
    def _cell(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v).replace("|", "/").replace("\n", " ").strip()

    return "| " + " | ".join(_cell(v) for v in cells) + " |"


def _is_empty_cell(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _trim_trailing_empty_columns(values: list[list[Any]]) -> list[list[Any]]:
    if not values:
        return values
    max_cols = max(len(row) for row in values)
    last_non_empty = -1
    for col_idx in range(max_cols):
        if any(col_idx < len(row) and not _is_empty_cell(row[col_idx]) for row in values):
            last_non_empty = col_idx
    if last_non_empty < 0:
        return []
    return [list(row[: last_non_empty + 1]) for row in values]


def _render_markdown(sheet_title: str, values: list[list[Any]]) -> str:
    values = _trim_trailing_empty_columns(values)
    if not values:
        return f"### {sheet_title}\n(空)"
    header = values[0]
    body = values[1:]
    col_count = max(len(row) for row in values)
    padded_header = list(header) + [""] * (col_count - len(header))
    lines = [f"### {sheet_title}", _render_row(padded_header), "| " + " | ".join(["---"] * col_count) + " |"]
    for row in body:
        padded = list(row) + [""] * (col_count - len(row))
        lines.append(_render_row(padded))
    return "\n".join(lines)


def _pick_sheet(sheets: list[dict[str, Any]], sheet_id: str | None) -> dict[str, Any] | None:
    if not sheets:
        return None
    if sheet_id:
        for sheet in sheets:
            if str(sheet.get("sheet_id") or "") == sheet_id:
                return sheet
    return sheets[0]


def fetch_sheet_text(
    client: lark.Client,
    spreadsheet_token: str,
    *,
    sheet_id: str | None,
    start_row: int = 1,
    max_rows: int = MAX_ROWS_PER_SHEET,
) -> str:
    """Return a compact markdown snapshot of the target sheet.

    - Lists all sheets in the spreadsheet as a header summary.
    - Renders one sheet's cells: the ``sheet_id`` if given, else the first.
    - Row / column counts are clipped by ``start_row``, ``max_rows`` and
      ``MAX_COLS_PER_SHEET``.
    - Returns "" on failure so the caller can fall back to a friendlier reply.
    """
    listing = _request_json(
        client,
        method=HttpMethod.GET,
        uri=f"/open-apis/sheets/v3/spreadsheets/{quote(spreadsheet_token)}/sheets/query",
    )
    sheets = ((listing.get("data") or {}).get("sheets")) or []
    if not sheets:
        return ""

    target = _pick_sheet(sheets, sheet_id)
    if target is None:
        return ""

    grid = (target.get("grid_properties") or {})
    row_count = int(grid.get("row_count") or 0)
    col_count = int(grid.get("column_count") or 0)
    if row_count <= 0 or col_count <= 0:
        row_count, col_count = 1, 1

    safe_start_row = max(1, int(start_row or 1))
    if safe_start_row > row_count:
        safe_start_row = row_count
    row_cap = max(1, min(int(max_rows), row_count - safe_start_row + 1))
    end_row = safe_start_row + row_cap - 1
    col_cap = max(1, min(MAX_COLS_PER_SHEET, col_count))
    target_id = str(target.get("sheet_id") or "")
    a1 = f"{target_id}!A{safe_start_row}:{_col_letter(col_cap)}{end_row}"

    values_payload = _request_json(
        client,
        method=HttpMethod.GET,
        uri=(
            f"/open-apis/sheets/v2/spreadsheets/{quote(spreadsheet_token)}"
            f"/values_batch_get?ranges={quote(a1, safe='!:')}"
        ),
    )
    ranges = ((values_payload.get("data") or {}).get("valueRanges")) or []
    values: list[list[Any]] = []
    if ranges:
        values = list(ranges[0].get("values") or [])

    other = [
        f"- {s.get('title') or s.get('sheet_id')}"
        for s in sheets
        if str(s.get("sheet_id") or "") != target_id
    ]
    summary_lines = [
        f"飞书电子表格：共 {len(sheets)} 个 sheet；当前渲染：{target.get('title') or target_id}",
        f"当前渲染行：{safe_start_row}-{end_row}；总行数约 {row_count}，总列数约 {col_count}",
    ]
    if other:
        summary_lines.append("其他 sheet：")
        summary_lines.extend(other)

    body = _render_markdown(str(target.get("title") or target_id), values)
    return "\n".join(summary_lines + ["", body])
