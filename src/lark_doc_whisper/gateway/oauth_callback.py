"""Small OAuth callback server for user-scoped linked-doc reads."""
from __future__ import annotations

import html
import logging
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping
from urllib.parse import parse_qs, urlparse

from ..agent.url_fetch import decode_authorization_state
from ..state.user_doc_tokens import InMemoryUserDocTokenStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OAuthCallbackResponse:
    status: int
    body: str


class OAuthCallbackApp:
    def __init__(
        self,
        *,
        oauth_client,
        token_store: InMemoryUserDocTokenStore,
        state_secret: str,
    ) -> None:
        self._oauth_client = oauth_client
        self._token_store = token_store
        self._state_secret = state_secret

    def handle(self, params: Mapping[str, list[str]]) -> OAuthCallbackResponse:
        if params.get("error"):
            return OAuthCallbackResponse(400, "授权未完成，请回到评论区重新发起授权。")

        code = _first(params, "code")
        state_raw = _first(params, "state")
        if not code or not state_raw:
            return OAuthCallbackResponse(400, "授权回调缺少 code 或 state。")

        try:
            state = decode_authorization_state(state_raw, self._state_secret)
        except ValueError:
            return OAuthCallbackResponse(400, "授权状态无效，请回到评论区重新发起授权。")

        if state.get("action") != "feishu_link_doc_authorization":
            return OAuthCallbackResponse(400, "授权状态无效，请回到评论区重新发起授权。")

        expected_open_id = str(state.get("user_open_id") or "")
        link_url = str(state.get("link_url") or "")
        if not expected_open_id or not link_url:
            return OAuthCallbackResponse(400, "授权状态缺少必要上下文，请重新发起授权。")

        try:
            token = self._oauth_client.exchange_code(code)
            actual_open_id = self._oauth_client.get_user_open_id(token.access_token)
        except Exception:
            logger.warning("failed to complete oauth callback", exc_info=True)
            return OAuthCallbackResponse(502, "授权换取失败，请稍后回到评论区重试。")

        if actual_open_id != expected_open_id:
            return OAuthCallbackResponse(403, "授权用户不匹配，请使用发起评论的账号重新授权。")

        self._token_store.put(
            expected_open_id,
            link_url,
            token.access_token,
            expires_in=token.expires_in,
        )
        return OAuthCallbackResponse(200, "授权已完成，请回到原评论线程重新 @我。")


class OAuthCallbackService:
    def __init__(self, *, host: str, port: int, app: OAuthCallbackApp) -> None:
        self._host = host
        self._port = port
        self._app = app
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        app = self._app

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib API name
                parsed = urlparse(self.path)
                if parsed.path != "/oauth/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                response = app.handle(parse_qs(parsed.query))
                body = _html_page(response.body).encode("utf-8")
                self.send_response(response.status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802 - stdlib API name
                self.send_response(405)
                self.end_headers()

            def log_message(self, fmt: str, *args) -> None:
                parsed = urlparse(getattr(self, "path", ""))
                logger.info(
                    "oauth callback http: method=%s path=%s",
                    getattr(self, "command", ""),
                    parsed.path,
                )

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="whisper-oauth-callback",
            daemon=True,
        )
        self._thread.start()
        logger.info("oauth callback server listening on %s:%s", self._host, self._port)

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None


def _first(params: Mapping[str, list[str]], key: str) -> str:
    values = params.get(key) or []
    return str(values[0]) if values else ""


def _html_page(message: str) -> str:
    escaped = html.escape(message)
    return f"<!doctype html><meta charset=\"utf-8\"><title>授权结果</title><p>{escaped}</p>"
