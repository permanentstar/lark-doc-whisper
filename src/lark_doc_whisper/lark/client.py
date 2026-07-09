"""lark-oapi SDK Client factory and app-level helpers."""
from __future__ import annotations

import json
import time

import lark_oapi as lark
from lark_oapi.core.enum import AccessTokenType, HttpMethod

from ..config import load_env

_client: lark.Client | None = None


def get_client() -> lark.Client:
    global _client
    if _client is None:
        env = load_env()
        _client = (
            lark.Client.builder()
            .app_id(env["LARK_APP_ID"])
            .app_secret(env["LARK_APP_SECRET"])
            .log_level(lark.LogLevel.INFO)
            .build()
        )
    return _client


def resolve_bot_open_id(client: lark.Client, *, attempts: int = 3, retry_delay_sec: float = 0.5) -> str:
    """Resolve the current app bot's open_id at startup.

    The value is runtime metadata derived from LARK_APP_ID/LARK_APP_SECRET, so
    keeping it out of persistent config avoids drift between credentials and
    bot identity.
    """
    request = (
        lark.BaseRequest.builder()
        .http_method(HttpMethod.GET)
        .uri("/open-apis/bot/v3/info")
        .token_types({AccessTokenType.TENANT})
        .build()
    )

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            response = client.request(request)
            if not response.success():
                raise RuntimeError(
                    f"code={response.code} msg={response.msg or 'unknown'}"
                )
            content = response.raw.content if response.raw is not None else None
            if not content:
                raise RuntimeError("empty response body")
            payload = json.loads(content.decode("utf-8") if isinstance(content, bytes) else content)
            open_id = str(((payload.get("bot") or {}).get("open_id") or "")).strip()
            if not open_id:
                raise RuntimeError("missing bot.open_id")
            return open_id
        except Exception as exc:
            last_error = exc
            if attempt < max(1, attempts) - 1:
                time.sleep(retry_delay_sec)

    raise RuntimeError(f"failed to resolve bot open_id: {last_error}") from last_error
