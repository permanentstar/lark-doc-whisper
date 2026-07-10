"""Lark OAuth helpers for short-lived user access tokens."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    expires_in: int


class LarkOAuthClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        redirect_uri: str,
        timeout_sec: int = 8,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._redirect_uri = redirect_uri
        self._timeout_sec = timeout_sec

    def exchange_code(self, code: str) -> OAuthToken:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._app_id,
            "client_secret": self._app_secret,
            "code": code,
            "redirect_uri": self._redirect_uri,
        }
        resp = httpx.post(
            "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
            json=payload,
            timeout=self._timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        if int(data.get("code") or 0) != 0:
            raise RuntimeError("oauth token exchange failed")
        token_data = _token_payload(data)
        access_token = str(token_data.get("access_token") or "")
        expires_in = int(token_data.get("expires_in") or 0)
        if not access_token or expires_in <= 0:
            raise RuntimeError("oauth token response missing access_token or expires_in")
        return OAuthToken(access_token=access_token, expires_in=expires_in)

    def get_user_open_id(self, access_token: str) -> str:
        resp = httpx.get(
            "https://open.feishu.cn/open-apis/authen/v1/user_info",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=self._timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        if int(data.get("code") or 0) != 0:
            raise RuntimeError("user_info request failed")
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        open_id = str((payload or {}).get("open_id") or "")
        if not open_id:
            raise RuntimeError("user_info response missing open_id")
        return open_id


def _token_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    if isinstance(nested, dict) and nested.get("access_token"):
        return nested
    return data
