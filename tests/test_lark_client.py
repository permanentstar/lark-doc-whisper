from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from lark_oapi.core.enum import AccessTokenType, HttpMethod

from lark_doc_whisper.lark.client import resolve_bot_open_id


class _Response:
    def __init__(self, payload: dict, *, ok: bool = True):
        self.code = payload.get("code")
        self.msg = payload.get("msg")
        self.raw = SimpleNamespace(content=json.dumps(payload).encode("utf-8"))
        self._ok = ok

    def success(self):
        return self._ok


class _Client:
    def __init__(self, response: _Response):
        self.response = response
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return self.response


def test_resolve_bot_open_id_fetches_bot_info_with_tenant_token():
    client = _Client(
        _Response(
            {
                "code": 0,
                "msg": "ok",
                "bot": {"open_id": "ou_bot", "app_name": "AssistBot"},
            }
        )
    )

    open_id = resolve_bot_open_id(client)

    assert open_id == "ou_bot"
    request = client.requests[0]
    assert request.http_method == HttpMethod.GET
    assert request.uri == "/open-apis/bot/v3/info"
    assert request.token_types == {AccessTokenType.TENANT}


def test_resolve_bot_open_id_fails_fast_on_api_error():
    client = _Client(_Response({"code": 999, "msg": "invalid app"}, ok=False))

    with pytest.raises(RuntimeError, match="failed to resolve bot open_id"):
        resolve_bot_open_id(client, attempts=1)


def test_resolve_bot_open_id_fails_fast_when_response_omits_open_id():
    client = _Client(_Response({"code": 0, "msg": "ok", "bot": {}}))

    with pytest.raises(RuntimeError, match="missing bot.open_id"):
        resolve_bot_open_id(client, attempts=1)
