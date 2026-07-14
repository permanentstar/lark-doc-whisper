"""Unit tests for feishu sheets read-only text fetcher.

The fetcher talks to legacy v2 spreadsheets API via lark.BaseRequest so we
can reuse the tenant-token flow already used elsewhere.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from lark_doc_whisper.lark.sheets_fetcher import fetch_sheet_text


def _make_client(recorded, responses):
    """A fake lark client whose .request returns queued responses.

    Each response is a dict: {code, data} — encoded as JSON for the raw body.
    """
    queue = list(responses)

    def _request(request):
        recorded.append(request)
        payload = queue.pop(0)
        return SimpleNamespace(
            success=lambda: int(payload.get("code", 0)) == 0,
            code=int(payload.get("code", 0)),
            msg=str(payload.get("msg") or ""),
            raw=SimpleNamespace(content=json.dumps(payload).encode("utf-8")),
        )

    return SimpleNamespace(request=_request)


def test_fetch_sheet_text_returns_markdown_of_first_sheet_by_default():
    recorded: list = []
    client = _make_client(
        recorded,
        [
            {
                "code": 0,
                "data": {
                    "sheets": [
                        {
                            "sheet_id": "sh_a",
                            "title": "Alpha",
                            "grid_properties": {"row_count": 3, "column_count": 2},
                        },
                        {
                            "sheet_id": "sh_b",
                            "title": "Beta",
                            "grid_properties": {"row_count": 10, "column_count": 5},
                        },
                    ]
                },
            },
            {
                "code": 0,
                "data": {
                    "valueRanges": [
                        {
                            "range": "sh_a!A1:B3",
                            "values": [
                                ["名称", "数量"],
                                ["苹果", 12],
                                ["香蕉", 7],
                            ],
                        }
                    ]
                },
            },
        ],
    )

    text = fetch_sheet_text(client, "ss_token", sheet_id=None, max_rows=100)

    assert "Alpha" in text
    assert "| 名称 | 数量 |" in text
    assert "| 苹果 | 12 |" in text
    assert "| 香蕉 | 7 |" in text
    # Non-selected sheet only listed as summary, not rendered
    assert "Beta" in text
    # We asked v3 query first then v2 values_batch_get
    assert recorded[0].uri.endswith("/sheets/v3/spreadsheets/ss_token/sheets/query")
    assert "values_batch_get" in recorded[1].uri
    assert "sh_a!A1:B3" in recorded[1].uri


def test_fetch_sheet_text_selects_sheet_by_id_when_given():
    recorded: list = []
    client = _make_client(
        recorded,
        [
            {
                "code": 0,
                "data": {
                    "sheets": [
                        {
                            "sheet_id": "sh_a",
                            "title": "Alpha",
                            "grid_properties": {"row_count": 3, "column_count": 2},
                        },
                        {
                            "sheet_id": "sh_b",
                            "title": "Beta",
                            "grid_properties": {"row_count": 2, "column_count": 2},
                        },
                    ]
                },
            },
            {
                "code": 0,
                "data": {
                    "valueRanges": [
                        {
                            "range": "sh_b!A1:B2",
                            "values": [["col1", "col2"], ["v1", "v2"]],
                        }
                    ]
                },
            },
        ],
    )

    text = fetch_sheet_text(client, "ss_token", sheet_id="sh_b", max_rows=50)
    assert "Beta" in text
    assert "sh_b!A1:B2" in recorded[1].uri
    assert "| col1 | col2 |" in text


def test_fetch_sheet_text_trims_trailing_empty_columns():
    recorded: list = []
    client = _make_client(
        recorded,
        [
            {
                "code": 0,
                "data": {
                    "sheets": [
                        {
                            "sheet_id": "sh_a",
                            "title": "Alpha",
                            "grid_properties": {"row_count": 2, "column_count": 6},
                        }
                    ]
                },
            },
            {
                "code": 0,
                "data": {
                    "valueRanges": [
                        {
                            "range": "sh_a!A1:F2",
                            "values": [
                                ["名称", "数量", "", None, "", ""],
                                ["苹果", 12, "", None, "", ""],
                            ],
                        }
                    ]
                },
            },
        ],
    )

    text = fetch_sheet_text(client, "ss_token", sheet_id=None, max_rows=10)

    assert "| 名称 | 数量 |" in text
    assert "| 苹果 | 12 |" in text
    assert "| 名称 | 数量 |  |" not in text


def test_fetch_sheet_text_returns_empty_when_query_fails():
    recorded: list = []
    client = _make_client(
        recorded,
        [{"code": 91403, "msg": "permission denied"}],
    )

    text = fetch_sheet_text(client, "ss_token", sheet_id=None, max_rows=100)
    assert text == ""


def test_fetch_sheet_text_caps_rows_to_max_rows():
    recorded: list = []
    client = _make_client(
        recorded,
        [
            {
                "code": 0,
                "data": {
                    "sheets": [
                        {
                            "sheet_id": "sh_a",
                            "title": "Alpha",
                            "grid_properties": {"row_count": 1000, "column_count": 2},
                        }
                    ]
                },
            },
            {
                "code": 0,
                "data": {"valueRanges": [{"range": "sh_a!A1:B5", "values": [["h1", "h2"]]}]},
            },
        ],
    )

    fetch_sheet_text(client, "ss_token", sheet_id=None, max_rows=5)
    # requested range must respect max_rows cap
    assert "sh_a!A1:B5" in recorded[1].uri


def test_fetch_sheet_text_can_read_incremental_row_window():
    recorded: list = []
    client = _make_client(
        recorded,
        [
            {
                "code": 0,
                "data": {
                    "sheets": [
                        {
                            "sheet_id": "sh_a",
                            "title": "Alpha",
                            "grid_properties": {"row_count": 1000, "column_count": 3},
                        }
                    ]
                },
            },
            {
                "code": 0,
                "data": {
                    "valueRanges": [
                        {
                            "range": "sh_a!A101:C120",
                            "values": [["r101", "v1", "x"], ["r102", "v2", "y"]],
                        }
                    ]
                },
            },
        ],
    )

    text = fetch_sheet_text(client, "ss_token", sheet_id=None, start_row=101, max_rows=20)

    assert "当前渲染行：101-120" in text
    assert "| r101 | v1 | x |" in text
    assert "sh_a!A101:C120" in recorded[1].uri
