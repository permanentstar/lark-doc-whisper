from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from lark_doc_whisper.agent.doc_context import current_doc_context_bag
from lark_doc_whisper.agent.url_fetch import (
    FetchedUrlContent,
    UrlAuthorizationRequest,
    UrlFetchContext,
    build_fetched_url_context,
    current_url_fetch_context,
    decode_authorization_state,
    fetch_url_content_tool,
    preflight_feishu_urls,
)
from lark_doc_whisper.config import UrlAuthorizationConfig, UrlFetchConfig
from lark_doc_whisper.security.policy import AllowedUrl
from lark_doc_whisper.state.user_doc_tokens import InMemoryUserDocTokenStore


def test_fetch_url_content_rejects_unapproved_url():
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=object(),
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url="https://example.com/allowed.py", kind="external_http"),),
        )
    )
    try:
        result = fetch_url_content_tool.invoke({"url": "https://evil.example/secret.txt", "reason": "compare"})
        assert "not allowed" in result
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_fetch_url_content_attaches_external_text(monkeypatch):
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=object(),
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url="https://example.com/demo.py", kind="external_http"),),
        )
    )
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch._fetch_external_text", lambda *_, **__: ("def demo():\n    return 1", ""))
    try:
        result = fetch_url_content_tool.invoke({"url": "https://example.com/demo.py", "reason": "compare"})
        bag = current_doc_context_bag.get()
        assert "url content attached" in result
        assert "def demo()" in bag["url_content"]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_fetch_url_content_rejects_github_url_even_when_approved(monkeypatch):
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=object(),
            cfg=UrlFetchConfig(),
            allowed_urls=(
                AllowedUrl(
                    url="https://github.com/permanentstar/lark-doc-whisper",
                    kind="external_http",
                ),
            ),
        )
    )

    def _unexpected_fetch(*args, **kwargs):
        raise AssertionError("GitHub URLs must not use generic HTTP fetch")

    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch._fetch_external_text", _unexpected_fetch)
    try:
        result = fetch_url_content_tool.invoke(
            {
                "url": "https://github.com/permanentstar/lark-doc-whisper",
                "reason": "read repo",
            }
        )
        assert "GitHub MCP" in result
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_build_fetched_url_context_applies_char_budget():
    context = build_fetched_url_context(
        (
            FetchedUrlContent(
                url="https://bytedance.sg.larkoffice.com/sheets/sheet_token",
                kind="feishu_sheets",
                text="x" * 500,
            ),
        ),
        max_chars=160,
    )

    assert len(context) <= 160
    assert context.startswith("<url-content")
    assert "...[truncated]" in context


def test_fetch_url_content_resolves_feishu_wiki_to_docx(monkeypatch):
    fake_client = SimpleNamespace(
        wiki=SimpleNamespace(
            v2=SimpleNamespace(
                space=SimpleNamespace(
                    get_node=lambda request: SimpleNamespace(
                        success=lambda: True,
                        data=SimpleNamespace(node=SimpleNamespace(obj_type="docx", obj_token="docx_token")),
                    )
                )
            )
        )
    )
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=fake_client,
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/wiki/wiki_token", kind="feishu_wiki"),),
        )
    )
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_doc_text", lambda *_, **__: "wiki doc text")
    try:
        result = fetch_url_content_tool.invoke({"url": "https://bytedance.sg.larkoffice.com/wiki/wiki_token", "reason": "read"})
        assert "url content attached" in result
        assert "wiki doc text" in current_doc_context_bag.get()["url_content"]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_fetch_url_content_resolves_feishu_wiki_to_sheet(monkeypatch):
    fake_client = SimpleNamespace(
        wiki=SimpleNamespace(
            v2=SimpleNamespace(
                space=SimpleNamespace(
                    get_node=lambda request: SimpleNamespace(
                        success=lambda: True,
                        data=SimpleNamespace(node=SimpleNamespace(obj_type="sheet", obj_token="sheet_token")),
                    )
                )
            )
        )
    )
    calls: list = []

    def _fake_sheet_fetch(client, spreadsheet_token, *, sheet_id, max_rows):
        calls.append({"token": spreadsheet_token, "sheet_id": sheet_id, "max_rows": max_rows})
        return "### Sheet\n| a |\n| --- |\n| 1 |"

    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=fake_client,
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/wiki/wiki_token", kind="feishu_wiki"),),
        )
    )
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_sheet_text", _fake_sheet_fetch)
    try:
        result = fetch_url_content_tool.invoke({"url": "https://bytedance.sg.larkoffice.com/wiki/wiki_token", "reason": "read"})
        assert "url content attached" in result
        assert "### Sheet" in current_doc_context_bag.get()["url_content"]
        assert calls == [{"token": "sheet_token", "sheet_id": None, "max_rows": 200}]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_fetch_url_content_resolves_feishu_wiki_to_bitable(monkeypatch):
    fake_client = SimpleNamespace(
        wiki=SimpleNamespace(
            v2=SimpleNamespace(
                space=SimpleNamespace(
                    get_node=lambda request: SimpleNamespace(
                        success=lambda: True,
                        data=SimpleNamespace(node=SimpleNamespace(obj_type="bitable", obj_token="app_token")),
                    )
                )
            )
        )
    )
    calls: list = []

    def _fake_bitable_fetch(client, app_token, *, table_id, max_rows):
        calls.append({"token": app_token, "table_id": table_id, "max_rows": max_rows})
        return "### Base\n| a |\n| --- |\n| 1 |"

    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=fake_client,
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/wiki/wiki_token", kind="feishu_wiki"),),
        )
    )
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_bitable_text", _fake_bitable_fetch)
    try:
        result = fetch_url_content_tool.invoke({"url": "https://bytedance.sg.larkoffice.com/wiki/wiki_token", "reason": "read"})
        assert "url content attached" in result
        assert "### Base" in current_doc_context_bag.get()["url_content"]
        assert calls == [{"token": "app_token", "table_id": None, "max_rows": 200}]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_preflight_feishu_urls_reports_permission_required(monkeypatch):
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_doc_text", lambda *_, **__: "")

    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(),
        allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/docx/doc_token", kind="feishu_docx"),),
    )

    assert result.allowed is False
    assert result.url == "https://bytedance.sg.larkoffice.com/docx/doc_token"
    assert result.reason == "permission_or_auth_required"
    assert "没有权限访问这个链接" in result.reply_text


def test_preflight_feishu_urls_keeps_readable_sheet_content(monkeypatch):
    url = "https://bytedance.sg.larkoffice.com/sheets/sheet_token"

    def _fake_sheet_fetch(client, spreadsheet_token, *, sheet_id, max_rows):
        assert spreadsheet_token == "sheet_token"
        assert sheet_id is None
        assert max_rows == 200
        return "### Sheet: Q3\n| 业务子域 | 表数 |\n| --- | --- |\n| 数据仓库 | 23 |"

    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_sheet_text", _fake_sheet_fetch)

    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(),
        allowed_urls=(AllowedUrl(url=url, kind="feishu_sheets"),),
    )

    assert result.allowed is True
    assert len(result.fetched_contents) == 1
    assert result.fetched_contents[0].url == url
    assert result.fetched_contents[0].kind == "feishu_sheets"
    assert "数据仓库" in result.fetched_contents[0].text


def test_preflight_feishu_urls_returns_oauth_link_when_configured(monkeypatch):
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_doc_text", lambda *_, **__: "")

    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(
            authorization=UrlAuthorizationConfig(
                enabled=True,
                redirect_uri="https://assistant.example.com/lark/oauth/callback",
                scopes=("docx:document:readonly", "drive:drive:readonly"),
            )
        ),
        allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/docx/link_doc", kind="feishu_docx"),),
        app_id="cli_test",
        state_secret="state_secret",
        auth_request=UrlAuthorizationRequest(
            source_file_token="current_doc",
            source_file_type="docx",
            comment_id="comment_1",
            reply_id="reply_1",
            user_open_id="ou_user",
        ),
    )

    assert result.allowed is False
    assert result.reason == "permission_or_auth_required"
    assert result.authorization_url
    assert "授权链接里的文档" in result.reply_text
    assert result.authorization_url in result.reply_text

    parsed = urlparse(result.authorization_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.feishu.cn"
    assert parsed.path == "/open-apis/authen/v1/authorize"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["cli_test"]
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == ["https://assistant.example.com/lark/oauth/callback"]
    assert query["scope"] == ["docx:document:readonly drive:drive:readonly"]

    state = decode_authorization_state(query["state"][0], "state_secret")
    assert state["action"] == "feishu_link_doc_authorization"
    assert state["link_url"] == "https://bytedance.sg.larkoffice.com/docx/link_doc"
    assert state["link_kind"] == "feishu_docx"
    assert state["source_file_token"] == "current_doc"
    assert state["source_file_type"] == "docx"
    assert state["comment_id"] == "comment_1"
    assert state["reply_id"] == "reply_1"
    assert state["user_open_id"] == "ou_user"

    with pytest.raises(ValueError, match="invalid authorization state signature"):
        decode_authorization_state(query["state"][0], "wrong_secret")

    envelope = json.loads(base64.urlsafe_b64decode(query["state"][0] + "==="))
    envelope["payload"]["comment_id"] = "other_comment"
    tampered = base64.urlsafe_b64encode(
        json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    with pytest.raises(ValueError, match="invalid authorization state signature"):
        decode_authorization_state(tampered, "state_secret")


def test_preflight_feishu_urls_falls_back_when_oauth_config_incomplete(monkeypatch):
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_doc_text", lambda *_, **__: "")

    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(
            authorization=UrlAuthorizationConfig(
                enabled=True,
                redirect_uri="https://assistant.example.com/lark/oauth/callback",
                scopes=(),
            )
        ),
        allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/docx/link_doc", kind="feishu_docx"),),
        app_id="cli_test",
        state_secret="state_secret",
        auth_request=UrlAuthorizationRequest(
            source_file_token="current_doc",
            source_file_type="docx",
            comment_id="comment_1",
            reply_id="reply_1",
            user_open_id="ou_user",
        ),
    )

    assert result.allowed is False
    assert result.authorization_url == ""
    assert result.reply_text == "我暂时没有权限访问这个链接。请先完成授权或把文档权限共享给机器人，然后重新 @我。"


def test_preflight_feishu_urls_filters_offline_access_scope(monkeypatch):
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_doc_text", lambda *_, **__: "")

    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(
            authorization=UrlAuthorizationConfig(
                enabled=True,
                redirect_uri="https://assistant.example.com/lark/oauth/callback",
                scopes=("docx:document:readonly", "offline_access"),
            )
        ),
        allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/docx/link_doc", kind="feishu_docx"),),
        app_id="cli_test",
        state_secret="state_secret",
        auth_request=UrlAuthorizationRequest(
            source_file_token="current_doc",
            source_file_type="docx",
            comment_id="comment_1",
            reply_id="reply_1",
            user_open_id="ou_user",
        ),
    )

    query = parse_qs(urlparse(result.authorization_url).query)
    assert query["scope"] == ["docx:document:readonly"]


def test_preflight_feishu_urls_filters_offline_access_scope_case_insensitively(monkeypatch):
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_doc_text", lambda *_, **__: "")

    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(
            authorization=UrlAuthorizationConfig(
                enabled=True,
                redirect_uri="https://assistant.example.com/lark/oauth/callback",
                scopes=(" docx:document:readonly ", " Offline_Access "),
            )
        ),
        allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/docx/link_doc", kind="feishu_docx"),),
        app_id="cli_test",
        state_secret="state_secret",
        auth_request=UrlAuthorizationRequest(
            source_file_token="current_doc",
            source_file_type="docx",
            comment_id="comment_1",
            reply_id="reply_1",
            user_open_id="ou_user",
        ),
    )

    query = parse_qs(urlparse(result.authorization_url).query)
    assert query["scope"] == ["docx:document:readonly"]


def test_preflight_feishu_urls_allows_when_user_doc_token_can_read(monkeypatch):
    url = "https://bytedance.sg.larkoffice.com/docx/link_doc"
    store = InMemoryUserDocTokenStore(now=lambda: 1000.0, expiry_skew_sec=300)
    store.put("ou_user", url, "user-token", expires_in=7200)
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_doc_text", lambda *_, **__: "")
    monkeypatch.setattr(
        "lark_doc_whisper.agent.url_fetch.fetch_doc_text_with_user_access_token",
        lambda *_, **__: "user scoped doc text",
    )

    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(),
        allowed_urls=(AllowedUrl(url=url, kind="feishu_docx"),),
        auth_request=UrlAuthorizationRequest(
            source_file_token="current_doc",
            source_file_type="docx",
            comment_id="comment_1",
            reply_id="reply_1",
            user_open_id="ou_user",
        ),
        user_doc_token_store=store,
    )

    assert result.allowed is True


def test_fetch_url_content_uses_user_doc_token_when_bot_lacks_permission(monkeypatch):
    url = "https://bytedance.sg.larkoffice.com/docx/link_doc"
    store = InMemoryUserDocTokenStore(now=lambda: 1000.0, expiry_skew_sec=300)
    store.put("ou_user", url, "user-token", expires_in=7200)
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=object(),
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url=url, kind="feishu_docx"),),
            user_open_id="ou_user",
            user_doc_token_store=store,
        )
    )
    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_doc_text", lambda *_, **__: "")
    monkeypatch.setattr(
        "lark_doc_whisper.agent.url_fetch.fetch_doc_text_with_user_access_token",
        lambda *_, **__: "user scoped doc text",
    )
    try:
        result = fetch_url_content_tool.invoke({"url": url, "reason": "read"})
        assert "url content attached" in result
        assert "user scoped doc text" in current_doc_context_bag.get()["url_content"]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


@pytest.mark.parametrize(
    "kind,url,label",
    [
        ("feishu_slides", "https://bytedance.sg.larkoffice.com/slides/sl1", "飞书幻灯片"),
        ("feishu_docs", "https://bytedance.sg.larkoffice.com/docs/lg1", "旧版飞书文档"),
        ("feishu_mindnote", "https://bytedance.sg.larkoffice.com/mindnotes/mn1", "飞书思维笔记"),
    ],
)
def test_preflight_rejects_unsupported_feishu_kinds_with_clear_reply(kind, url, label):
    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(),
        allowed_urls=(AllowedUrl(url=url, kind=kind),),
    )

    assert result.allowed is False
    assert result.reason == f"unsupported_feishu_type:{kind}"
    assert label in result.reply_text
    assert "没有权限" not in result.reply_text


@pytest.mark.parametrize(
    "kind,url",
    [
        ("feishu_slides", "https://bytedance.sg.larkoffice.com/slides/sl1"),
        ("feishu_docs", "https://bytedance.sg.larkoffice.com/docs/lg1"),
        ("feishu_mindnote", "https://bytedance.sg.larkoffice.com/mindnotes/mn1"),
    ],
)
def test_fetch_url_content_tool_rejects_unsupported_feishu_kinds(kind, url):
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=object(),
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url=url, kind=kind),),
        )
    )
    try:
        result = fetch_url_content_tool.invoke({"url": url, "reason": "read"})
        assert f"unsupported_feishu_type:{kind}" in result
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_fetch_url_content_reads_feishu_sheets_via_sheet_fetcher(monkeypatch):
    url = "https://bytedance.sg.larkoffice.com/sheets/ss_tok?sheet=sh_b"
    calls: list = []

    def _fake_fetch(client, spreadsheet_token, *, sheet_id, max_rows):
        calls.append({"token": spreadsheet_token, "sheet_id": sheet_id, "max_rows": max_rows})
        return "### Beta\n| col1 | col2 |\n| --- | --- |\n| v1 | v2 |"

    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_sheet_text", _fake_fetch)

    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=object(),
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url=url, kind="feishu_sheets"),),
        )
    )
    try:
        result = fetch_url_content_tool.invoke({"url": url, "reason": "read"})
        assert "url content attached" in result
        assert "v1" in current_doc_context_bag.get()["url_content"]
        assert calls == [{"token": "ss_tok", "sheet_id": "sh_b", "max_rows": 200}]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_preflight_allows_feishu_sheets_when_fetch_succeeds(monkeypatch):
    monkeypatch.setattr(
        "lark_doc_whisper.agent.url_fetch.fetch_sheet_text",
        lambda *a, **kw: "### Alpha\n| a |\n| --- |\n| 1 |",
    )
    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(),
        allowed_urls=(
            AllowedUrl(url="https://bytedance.sg.larkoffice.com/sheets/ss_tok", kind="feishu_sheets"),
        ),
    )
    assert result.allowed is True


def test_preflight_reports_permission_when_sheet_read_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "lark_doc_whisper.agent.url_fetch.fetch_sheet_text",
        lambda *a, **kw: "",
    )
    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(),
        allowed_urls=(
            AllowedUrl(url="https://bytedance.sg.larkoffice.com/sheets/ss_tok", kind="feishu_sheets"),
        ),
    )
    assert result.allowed is False
    assert result.reason == "permission_or_auth_required"


def test_fetch_url_content_reads_feishu_bitable_via_bitable_fetcher(monkeypatch):
    url = "https://bytedance.sg.larkoffice.com/base/app_tok?table=tbl_b"
    calls: list = []

    def _fake_fetch(client, app_token, *, table_id, max_rows):
        calls.append({"token": app_token, "table_id": table_id, "max_rows": max_rows})
        return "### Beta\n| col1 | col2 |\n| --- | --- |\n| v1 | v2 |"

    monkeypatch.setattr("lark_doc_whisper.agent.url_fetch.fetch_bitable_text", _fake_fetch)

    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=object(),
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url=url, kind="feishu_bitable"),),
        )
    )
    try:
        result = fetch_url_content_tool.invoke({"url": url, "reason": "read"})
        assert "url content attached" in result
        assert "v1" in current_doc_context_bag.get()["url_content"]
        assert calls == [{"token": "app_tok", "table_id": "tbl_b", "max_rows": 200}]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_preflight_allows_feishu_bitable_when_fetch_succeeds(monkeypatch):
    monkeypatch.setattr(
        "lark_doc_whisper.agent.url_fetch.fetch_bitable_text",
        lambda *a, **kw: "### Alpha\n| a |\n| --- |\n| 1 |",
    )
    result = preflight_feishu_urls(
        client=object(),
        cfg=UrlFetchConfig(),
        allowed_urls=(
            AllowedUrl(url="https://bytedance.sg.larkoffice.com/bitable/app_tok", kind="feishu_bitable"),
        ),
    )
    assert result.allowed is True


@pytest.mark.parametrize(
    "kind,url,patch_target,expected_call",
    [
        (
            "feishu_file",
            "https://bytedance.sg.larkoffice.com/file/file_tok",
            "lark_doc_whisper.agent.url_fetch.fetch_file_metadata_text",
            {"token": "file_tok"},
        ),
        (
            "feishu_whiteboard",
            "https://bytedance.sg.larkoffice.com/board/board_tok",
            "lark_doc_whisper.agent.url_fetch.fetch_whiteboard_text",
            {"token": "board_tok"},
        ),
    ],
)
def test_fetch_url_content_reads_other_supported_feishu_kinds(monkeypatch, kind, url, patch_target, expected_call):
    calls: list = []

    def _fake_fetch(client, token, *args, **kwargs):
        calls.append({"token": token})
        return "readable content"

    monkeypatch.setattr(patch_target, _fake_fetch)
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=object(),
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url=url, kind=kind),),
        )
    )
    try:
        result = fetch_url_content_tool.invoke({"url": url, "reason": "read"})
        assert "url content attached" in result
        assert "readable content" in current_doc_context_bag.get()["url_content"]
        assert calls == [expected_call]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


@pytest.mark.parametrize(
    "obj_type,patch_target",
    [
        ("file", "lark_doc_whisper.agent.url_fetch.fetch_file_metadata_text"),
        ("whiteboard", "lark_doc_whisper.agent.url_fetch.fetch_whiteboard_text"),
    ],
)
def test_fetch_url_content_resolves_feishu_wiki_to_other_supported_types(monkeypatch, obj_type, patch_target):
    fake_client = SimpleNamespace(
        wiki=SimpleNamespace(
            v2=SimpleNamespace(
                space=SimpleNamespace(
                    get_node=lambda request: SimpleNamespace(
                        success=lambda: True,
                        data=SimpleNamespace(node=SimpleNamespace(obj_type=obj_type, obj_token="obj_token")),
                    )
                )
            )
        )
    )
    calls: list = []

    def _fake_fetch(client, token, *args, **kwargs):
        calls.append({"token": token})
        return f"{obj_type} content"

    monkeypatch.setattr(patch_target, _fake_fetch)
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=fake_client,
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/wiki/wiki_token", kind="feishu_wiki"),),
        )
    )
    try:
        result = fetch_url_content_tool.invoke({"url": "https://bytedance.sg.larkoffice.com/wiki/wiki_token", "reason": "read"})
        assert "url content attached" in result
        assert f"{obj_type} content" in current_doc_context_bag.get()["url_content"]
        assert calls == [{"token": "obj_token"}]
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)


def test_fetch_url_content_rejects_feishu_wiki_backed_by_slides():
    fake_client = SimpleNamespace(
        wiki=SimpleNamespace(
            v2=SimpleNamespace(
                space=SimpleNamespace(
                    get_node=lambda request: SimpleNamespace(
                        success=lambda: True,
                        data=SimpleNamespace(node=SimpleNamespace(obj_type="slides", obj_token="obj_token")),
                    )
                )
            )
        )
    )
    bag_token = current_doc_context_bag.set({})
    ctx_token = current_url_fetch_context.set(
        UrlFetchContext(
            client=fake_client,
            cfg=UrlFetchConfig(),
            allowed_urls=(AllowedUrl(url="https://bytedance.sg.larkoffice.com/wiki/wiki_token", kind="feishu_wiki"),),
        )
    )
    try:
        result = fetch_url_content_tool.invoke({"url": "https://bytedance.sg.larkoffice.com/wiki/wiki_token", "reason": "read"})
        assert "unsupported_feishu_type:feishu_slides" in result
    finally:
        current_url_fetch_context.reset(ctx_token)
        current_doc_context_bag.reset(bag_token)
