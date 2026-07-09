from __future__ import annotations

import asyncio

from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent

from lark_doc_whisper.agent.github_mcp_policy import build_github_mcp_interceptor
from lark_doc_whisper.agent.url_fetch import UrlFetchContext, current_url_fetch_context
from lark_doc_whisper.config import UrlFetchConfig
from lark_doc_whisper.security.policy import AllowedUrl


def _text(result: CallToolResult) -> str:
    return "\n".join(item.text for item in result.content if isinstance(item, TextContent))


def _run(coro):
    return asyncio.run(coro)


def test_github_mcp_interceptor_allows_same_repo():
    calls = []
    interceptor = build_github_mcp_interceptor()
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

    async def handler(request: MCPToolCallRequest) -> CallToolResult:
        calls.append(request)
        return CallToolResult(content=[TextContent(type="text", text="README")])

    try:
        result = _run(
            interceptor(
                MCPToolCallRequest(
                    name="get_file_contents",
                    args={"owner": "permanentstar", "repo": "lark-doc-whisper", "path": "README.md"},
                    server_name="github",
                ),
                handler,
            )
        )
    finally:
        current_url_fetch_context.reset(ctx_token)

    assert _text(result) == "README"
    assert len(calls) == 1


def test_github_mcp_interceptor_rejects_cross_repo():
    calls = []
    interceptor = build_github_mcp_interceptor()
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

    async def handler(request: MCPToolCallRequest) -> CallToolResult:
        calls.append(request)
        return CallToolResult(content=[TextContent(type="text", text="SHOULD_NOT_RUN")])

    try:
        result = _run(
            interceptor(
                MCPToolCallRequest(
                    name="get_file_contents",
                    args={"owner": "other", "repo": "secret", "path": "README.md"},
                    server_name="github",
                ),
                handler,
            )
        )
    finally:
        current_url_fetch_context.reset(ctx_token)

    assert calls == []
    assert "not in allowed GitHub repositories" in _text(result)


def test_github_mcp_interceptor_rejects_write_tool_even_for_allowed_repo():
    calls = []
    interceptor = build_github_mcp_interceptor()
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

    async def handler(request: MCPToolCallRequest) -> CallToolResult:
        calls.append(request)
        return CallToolResult(content=[TextContent(type="text", text="SHOULD_NOT_RUN")])

    try:
        result = _run(
            interceptor(
                MCPToolCallRequest(
                    name="create_or_update_file",
                    args={"owner": "permanentstar", "repo": "lark-doc-whisper", "path": "README.md"},
                    server_name="github",
                ),
                handler,
            )
        )
    finally:
        current_url_fetch_context.reset(ctx_token)

    assert calls == []
    assert "read-only" in _text(result)


def test_github_mcp_interceptor_ignores_non_github_server():
    calls = []
    interceptor = build_github_mcp_interceptor()

    async def handler(request: MCPToolCallRequest) -> CallToolResult:
        calls.append(request)
        return CallToolResult(content=[TextContent(type="text", text="OK")])

    result = _run(
        interceptor(
            MCPToolCallRequest(
                name="get_file_contents",
                args={"owner": "other", "repo": "secret"},
                server_name="not_github",
            ),
            handler,
        )
    )

    assert _text(result) == "OK"
    assert len(calls) == 1
