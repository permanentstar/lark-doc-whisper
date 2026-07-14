"""Unit tests for feishu bitable read-only text fetcher."""
from __future__ import annotations

from types import SimpleNamespace

from lark_doc_whisper.lark.bitable_fetcher import fetch_bitable_text


def _resp(success, data=None):
    return SimpleNamespace(
        success=lambda: success,
        code=0 if success else 91403,
        msg="",
        data=data,
    )


def _mk_client(tables, fields, records):
    calls = []

    def _list_tables(req):
        calls.append(("list_tables", req))
        items = [SimpleNamespace(table_id=t["table_id"], name=t["name"]) for t in tables]
        return _resp(True, SimpleNamespace(items=items, has_more=False))

    def _list_fields(req):
        calls.append(("list_fields", req))
        items = [SimpleNamespace(field_name=f) for f in fields]
        return _resp(True, SimpleNamespace(items=items, has_more=False))

    def _list_records(req):
        calls.append(("list_records", req))
        items = [SimpleNamespace(fields=r) for r in records]
        return _resp(True, SimpleNamespace(items=items, has_more=False))

    client = SimpleNamespace(
        bitable=SimpleNamespace(
            v1=SimpleNamespace(
                app_table=SimpleNamespace(list=_list_tables),
                app_table_field=SimpleNamespace(list=_list_fields),
                app_table_record=SimpleNamespace(list=_list_records),
            )
        )
    )
    return client, calls


def test_fetch_bitable_text_returns_markdown_of_first_table_by_default():
    client, calls = _mk_client(
        tables=[
            {"table_id": "tbl_a", "name": "Alpha"},
            {"table_id": "tbl_b", "name": "Beta"},
        ],
        fields=["名称", "数量"],
        records=[
            {"名称": "苹果", "数量": 12},
            {"名称": "香蕉", "数量": 7},
        ],
    )

    text = fetch_bitable_text(client, "app_tok", table_id=None, max_rows=100)
    assert "Alpha" in text
    assert "Beta" in text  # summary lists both
    assert "| 名称 | 数量 |" in text
    assert "| 苹果 | 12 |" in text
    # only one search per call
    kinds = [c[0] for c in calls]
    assert kinds == ["list_tables", "list_fields", "list_records"]


def test_fetch_bitable_text_selects_table_by_id():
    client, calls = _mk_client(
        tables=[
            {"table_id": "tbl_a", "name": "Alpha"},
            {"table_id": "tbl_b", "name": "Beta"},
        ],
        fields=["c1", "c2"],
        records=[{"c1": "v1", "c2": "v2"}],
    )
    text = fetch_bitable_text(client, "app_tok", table_id="tbl_b", max_rows=100)
    assert "Beta" in text
    # search request must target tbl_b
    search_req = [c[1] for c in calls if c[0] == "list_records"][0]
    assert search_req.table_id == "tbl_b"


def test_fetch_bitable_text_returns_empty_on_permission_failure():
    def _boom(req):
        return _resp(False)

    client = SimpleNamespace(
        bitable=SimpleNamespace(
            v1=SimpleNamespace(
                app_table=SimpleNamespace(list=_boom),
                app_table_field=SimpleNamespace(list=_boom),
                app_table_record=SimpleNamespace(list=_boom),
            )
        )
    )
    assert fetch_bitable_text(client, "app_tok", table_id=None, max_rows=100) == ""


def test_fetch_bitable_text_caps_records_to_max_rows():
    client, calls = _mk_client(
        tables=[{"table_id": "tbl_a", "name": "Alpha"}],
        fields=["c"],
        records=[{"c": str(i)} for i in range(200)],
    )
    fetch_bitable_text(client, "app_tok", table_id=None, max_rows=3)
    search_req = [c[1] for c in calls if c[0] == "list_records"][0]
    assert search_req.page_size == 3
