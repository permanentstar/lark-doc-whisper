from __future__ import annotations

from types import SimpleNamespace

from lark_doc_whisper.agent.doc_context import current_doc_context_bag
from lark_doc_whisper.agent.url_fetch import (
    UrlFetchContext,
    current_url_fetch_context,
    fetch_url_content_tool,
    preflight_feishu_urls,
)
from lark_doc_whisper.config import UrlFetchConfig
from lark_doc_whisper.security.policy import AllowedUrl


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
