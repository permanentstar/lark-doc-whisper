from __future__ import annotations

import http.client
import logging
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

from lark_doc_whisper.agent.url_fetch import (
    UrlAuthorizationRequest,
    _encode_authorization_state,
)
from lark_doc_whisper.gateway.oauth_callback import OAuthCallbackApp, OAuthCallbackService
from lark_doc_whisper.state.user_doc_tokens import InMemoryUserDocTokenStore


class _OAuthClient:
    def __init__(self, *, open_id: str = "ou_user") -> None:
        self.open_id = open_id
        self.codes = []
        self.user_info_tokens = []

    def exchange_code(self, code: str):
        self.codes.append(code)
        return SimpleNamespace(access_token="user-token", expires_in=7200)

    def get_user_open_id(self, access_token: str) -> str:
        self.user_info_tokens.append(access_token)
        return self.open_id


def _state() -> str:
    return _encode_authorization_state(
        link_url="https://bytedance.sg.larkoffice.com/docx/link_doc",
        link_kind="feishu_docx",
        auth_request=UrlAuthorizationRequest(
            source_file_token="current_doc",
            source_file_type="docx",
            comment_id="comment_1",
            reply_id="reply_1",
            user_open_id="ou_user",
        ),
        state_secret="state-secret",
    )


def test_oauth_callback_stores_short_lived_user_doc_token():
    store = InMemoryUserDocTokenStore(now=lambda: 1000.0, expiry_skew_sec=300)
    oauth_client = _OAuthClient(open_id="ou_user")
    app = OAuthCallbackApp(
        oauth_client=oauth_client,
        token_store=store,
        state_secret="state-secret",
    )

    response = app.handle({"code": ["auth-code"], "state": [_state()]})

    assert response.status == 200
    assert "授权已完成" in response.body
    assert oauth_client.codes == ["auth-code"]
    assert oauth_client.user_info_tokens == ["user-token"]
    assert store.get("ou_user", "https://bytedance.sg.larkoffice.com/docx/link_doc") == "user-token"


def test_oauth_callback_rejects_user_mismatch_without_storing_token():
    store = InMemoryUserDocTokenStore(now=lambda: 1000.0, expiry_skew_sec=300)
    app = OAuthCallbackApp(
        oauth_client=_OAuthClient(open_id="ou_other"),
        token_store=store,
        state_secret="state-secret",
    )

    response = app.handle({"code": ["auth-code"], "state": [_state()]})

    assert response.status == 403
    assert "授权用户不匹配" in response.body
    assert store.get("ou_user", "https://bytedance.sg.larkoffice.com/docx/link_doc") is None


def test_oauth_callback_rejects_invalid_state():
    store = InMemoryUserDocTokenStore(now=lambda: 1000.0, expiry_skew_sec=300)
    app = OAuthCallbackApp(
        oauth_client=_OAuthClient(open_id="ou_user"),
        token_store=store,
        state_secret="state-secret",
    )

    response = app.handle({"code": ["auth-code"], "state": ["tampered"]})

    assert response.status == 400
    assert "授权状态无效" in response.body


def test_oauth_callback_service_serves_callback_path_without_logging_query(caplog):
    store = InMemoryUserDocTokenStore(now=lambda: 1000.0, expiry_skew_sec=300)
    app = OAuthCallbackApp(
        oauth_client=_OAuthClient(open_id="ou_user"),
        token_store=store,
        state_secret="state-secret",
    )
    service = OAuthCallbackService(host="127.0.0.1", port=0, app=app)
    service.start()
    try:
        port = service._server.server_address[1]  # test-only access to the chosen ephemeral port
        query = urlencode({"code": "auth-code", "state": _state()})
        with caplog.at_level(logging.INFO, logger="lark_doc_whisper.gateway.oauth_callback"):
            with urlopen(f"http://127.0.0.1:{port}/oauth/callback?{query}", timeout=5) as resp:
                body = resp.read().decode("utf-8")
        assert "授权已完成" in body
        assert store.get("ou_user", "https://bytedance.sg.larkoffice.com/docx/link_doc") == "user-token"
        log_text = "\n".join(record.getMessage() for record in caplog.records)
        assert "auth-code" not in log_text
        assert "state=" not in log_text
    finally:
        service.stop()


def test_oauth_callback_service_rejects_other_path_and_non_get():
    app = OAuthCallbackApp(
        oauth_client=_OAuthClient(open_id="ou_user"),
        token_store=InMemoryUserDocTokenStore(now=lambda: 1000.0, expiry_skew_sec=300),
        state_secret="state-secret",
    )
    service = OAuthCallbackService(host="127.0.0.1", port=0, app=app)
    service.start()
    try:
        port = service._server.server_address[1]
        try:
            urlopen(f"http://127.0.0.1:{port}/not-callback", timeout=5)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("POST", "/oauth/callback?code=auth-code&state=secret-state")
        resp = conn.getresponse()
        assert resp.status == 405
        conn.close()
    finally:
        service.stop()
