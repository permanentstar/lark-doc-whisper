from __future__ import annotations

import pytest

from lark_doc_whisper.lark.oauth import LarkOAuthClient


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_lark_oauth_client_exchanges_code_without_refresh_scope(monkeypatch):
    calls = []

    def fake_post(url, *, json, timeout):
        calls.append((url, json, timeout))
        return _Response(
            {
                "code": 0,
                "access_token": "user-token",
                "expires_in": 7200,
            }
        )

    monkeypatch.setattr("lark_doc_whisper.lark.oauth.httpx.post", fake_post)
    client = LarkOAuthClient(
        app_id="cli_test",
        app_secret="secret",
        redirect_uri="http://host:8088/oauth/callback",
        timeout_sec=9,
    )

    token = client.exchange_code("auth-code")

    assert token.access_token == "user-token"
    assert token.expires_in == 7200
    assert calls == [
        (
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            {
                "grant_type": "authorization_code",
                "client_id": "cli_test",
                "client_secret": "secret",
                "code": "auth-code",
                "redirect_uri": "http://host:8088/oauth/callback",
            },
            9,
        )
    ]


def test_lark_oauth_client_reads_user_open_id(monkeypatch):
    calls = []

    def fake_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return _Response({"code": 0, "data": {"open_id": "ou_user"}})

    monkeypatch.setattr("lark_doc_whisper.lark.oauth.httpx.get", fake_get)
    client = LarkOAuthClient(
        app_id="cli_test",
        app_secret="secret",
        redirect_uri="http://host:8088/oauth/callback",
        timeout_sec=9,
    )

    assert client.get_user_open_id("user-token") == "ou_user"
    assert calls == [
        (
            "https://open.feishu.cn/open-apis/authen/v1/user_info",
            {"Authorization": "Bearer user-token"},
            9,
        )
    ]


def test_lark_oauth_client_rejects_nonzero_api_code(monkeypatch):
    monkeypatch.setattr(
        "lark_doc_whisper.lark.oauth.httpx.post",
        lambda *_, **__: _Response({"code": 999, "msg": "bad code"}),
    )
    client = LarkOAuthClient(
        app_id="cli_test",
        app_secret="secret",
        redirect_uri="http://host:8088/oauth/callback",
        timeout_sec=9,
    )

    with pytest.raises(RuntimeError, match="oauth token exchange failed"):
        client.exchange_code("auth-code")
