from __future__ import annotations

import json
from pathlib import Path


def test_default_extensions_config_registers_github_repos_readonly_mcp():
    config = json.loads(Path("extensions_config.json").read_text(encoding="utf-8"))

    github = config["mcpServers"]["github"]
    assert github["enabled"] is True
    assert github["type"] == "http"
    assert github["url"] == "https://api.githubcopilot.com/mcp/x/repos/readonly"
    assert github["headers"]["Authorization"] == "$GITHUB_MCP_AUTHORIZATION"
    assert github["headers"]["X-MCP-Toolsets"] == "repos"
    assert github["headers"]["X-MCP-Readonly"] == "true"
    assert config["mcpInterceptors"] == [
        "lark_doc_whisper.agent.github_mcp_policy:build_github_mcp_interceptor"
    ]
