"""Unit tests for read-only Drive file metadata fetcher."""
from __future__ import annotations

from types import SimpleNamespace

from lark_doc_whisper.lark.drive_fetcher import fetch_file_metadata_text


def test_fetch_file_metadata_text_returns_summary():
    calls = []

    def _batch_query(req):
        calls.append(req)
        meta = SimpleNamespace(
            doc_token="file_tok",
            doc_type="file",
            title="迁移方案.pdf",
            owner_id="ou_owner",
            latest_modify_user="ou_editor",
            latest_modify_time=123456,
            url="https://example.com/file/file_tok",
            sec_label_name="内部",
        )
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(metas=[meta]), code=0, msg="")

    client = SimpleNamespace(drive=SimpleNamespace(v1=SimpleNamespace(meta=SimpleNamespace(batch_query=_batch_query))))

    text = fetch_file_metadata_text(client, "file_tok", "file")
    assert "飞书云盘文件元信息" in text
    assert "迁移方案.pdf" in text
    assert "内部" in text
    assert calls[0].request_body.request_docs[0].doc_token == "file_tok"
    assert calls[0].request_body.request_docs[0].doc_type == "file"


def test_fetch_file_metadata_text_returns_empty_on_failure():
    def _batch_query(req):
        return SimpleNamespace(success=lambda: False, data=None, code=91403, msg="permission denied")

    client = SimpleNamespace(drive=SimpleNamespace(v1=SimpleNamespace(meta=SimpleNamespace(batch_query=_batch_query))))
    assert fetch_file_metadata_text(client, "file_tok", "file") == ""
