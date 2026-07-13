from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from lark_doc_whisper.plugins.audit_log import AuditLogPlugin


def _header() -> SimpleNamespace:
    return SimpleNamespace(
        event_id="evt_1",
        event_type="drive.comment.add_v1",
        tenant_key="tkey",
        app_id="cli_test",
        create_time="1700000000",
    )


def _meta() -> SimpleNamespace:
    return SimpleNamespace(
        event_id="evt_1",
        file_token="doc_token",
        file_type="docx",
        comment_id="c1",
        reply_id="r1",
        from_open_id="ou_user",
        to_open_id="ou_bot",
        is_mentioned=True,
    )


def _dispatch(plugin: AuditLogPlugin, header, meta) -> None:
    async def _run():
        plugin.on_mention_event(header, meta)
        # AuditLog writes via a fire-and-forget background thread; drain.
        for task in list(plugin.pending_tasks):
            await task

    asyncio.run(_run())


def test_audit_log_writes_jsonl_line_with_expected_fields(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    plugin = AuditLogPlugin(path=log_path)

    _dispatch(plugin, _header(), _meta())

    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_id"] == "evt_1"
    assert payload["event_type"] == "drive.comment.add_v1"
    assert payload["tenant_key"] == "tkey"
    assert payload["app_id"] == "cli_test"
    assert payload["create_time"] == "1700000000"
    assert payload["file_token"] == "doc_token"
    assert payload["file_type"] == "docx"
    assert payload["comment_id"] == "c1"
    assert payload["reply_id"] == "r1"
    assert payload["from_open_id"] == "ou_user"
    assert payload["to_open_id"] == "ou_bot"
    assert payload["is_mentioned"] is True
    assert "ts_iso" in payload and payload["ts_iso"]
    # Absolutely must not leak the query body.
    assert "user_query" not in payload
    assert "query" not in payload


def test_audit_log_does_nothing_on_failure(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    plugin = AuditLogPlugin(path=log_path)

    # on_failure is a no-op for this plugin.
    plugin.on_failure(event=object())

    assert not log_path.exists()


def test_audit_log_appends_line_per_event(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    plugin = AuditLogPlugin(path=log_path)

    _dispatch(plugin, _header(), _meta())
    header2 = _header()
    header2.event_id = "evt_2"
    meta2 = _meta()
    meta2.event_id = "evt_2"
    _dispatch(plugin, header2, meta2)

    lines = log_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_id"] == "evt_1"
    assert json.loads(lines[1])["event_id"] == "evt_2"


def test_audit_log_survives_partial_header(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    plugin = AuditLogPlugin(path=log_path)

    _dispatch(plugin, None, _meta())

    payload = json.loads(log_path.read_text().splitlines()[0])
    assert payload["event_type"] == ""
    assert payload["tenant_key"] == ""
    assert payload["file_token"] == "doc_token"
