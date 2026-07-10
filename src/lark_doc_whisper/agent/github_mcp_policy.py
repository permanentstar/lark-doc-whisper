"""Program-side policy for GitHub MCP tool calls."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent

from .github_urls import allowed_github_repo_keys, github_repo_key_from_mcp_args
from .url_fetch import current_url_fetch_context


_READ_ONLY_TOOL_PREFIXES = ("get_", "list_", "search_")
_READ_ONLY_TOOL_SUFFIXES = ("_read",)
_READ_ONLY_TOOL_NAMES = {
    "repo_info",
    "repository_info",
}


def _deny(message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"[github mcp unavailable: {message}]")]
    )


def _tool_name_without_server_prefix(name: str, server_name: str) -> str:
    prefix = f"{server_name}_"
    if name.startswith(prefix):
        return name[len(prefix):]
    return name


def _is_read_only_tool(name: str) -> bool:
    if name in _READ_ONLY_TOOL_NAMES:
        return True
    return name.startswith(_READ_ONLY_TOOL_PREFIXES) or name.endswith(_READ_ONLY_TOOL_SUFFIXES)


def build_github_mcp_interceptor():
    async def github_mcp_interceptor(
        request: MCPToolCallRequest,
        handler: Callable[[MCPToolCallRequest], Awaitable[Any]],
    ) -> Any:
        if request.server_name != "github":
            return await handler(request)

        tool_name = _tool_name_without_server_prefix(request.name, request.server_name)
        if not _is_read_only_tool(tool_name):
            return _deny(f"tool={tool_name}, reason=read-only GitHub MCP tools only")

        ctx = current_url_fetch_context.get()
        if ctx is None:
            return _deny("reason=no active url fetch context")

        allowed_repos = allowed_github_repo_keys(ctx.allowed_urls)
        requested_repo = github_repo_key_from_mcp_args(request.args)
        if not requested_repo or requested_repo not in allowed_repos:
            return _deny(
                f"repo={requested_repo or 'unknown'}, reason=not in allowed GitHub repositories"
            )

        return await handler(request)

    return github_mcp_interceptor
