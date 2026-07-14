from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_deerflow_memory_is_explicitly_disabled():
    config = yaml.safe_load((ROOT / "configs" / "deerflow.yaml").read_text())

    assert config["memory"]["enabled"] is False


def test_deerflow_loader_sees_memory_disabled(monkeypatch):
    from deerflow.config.app_config import reload_app_config

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    app_config = reload_app_config(str(ROOT / "configs" / "deerflow.yaml"))

    assert app_config.memory.enabled is False


def test_app_checkpointer_still_uses_sqlite_for_thread_memory():
    config = yaml.safe_load((ROOT / "configs" / "app.yaml").read_text())

    assert config["deerflow"]["checkpointer"]["type"] == "sqlite"
    assert config["deerflow"]["checkpointer"]["connection_string"].endswith("checkpoints.db")


def test_app_config_has_concurrency_defaults():
    from lark_doc_whisper.config import load_app_config

    app_config = load_app_config()

    assert not hasattr(app_config, "bot_open_id")
    assert app_config.event_queue_size == 200
    assert app_config.event_worker_count == 8
    assert app_config.max_backend_in_flight == 8
    assert app_config.backend_timeout_sec == 300
    assert app_config.episode_summary_timeout_sec == 60


def test_app_config_has_failure_and_url_fetch_defaults():
    from lark_doc_whisper.config import load_app_config

    app_config = load_app_config()

    assert app_config.failure_handling.polite_reply_text == "目前在神游，稍后回来。"
    assert app_config.failure_handling.notifier_enabled is False
    assert app_config.url_fetch.enabled is True
    assert app_config.url_fetch.timeout_sec == 8
    assert app_config.url_fetch.max_redirects == 3
    assert app_config.comment_context.default_thread_history_replies == 8
    assert app_config.comment_context.default_thread_history_chars == 3_000
    assert app_config.comment_context.max_thread_history_replies == 30
    assert app_config.comment_context.max_thread_history_chars == 8_000
    assert app_config.comment_context.max_url_preload_chars == 4_000
    assert app_config.comment_context.max_url_preload_chars < app_config.comment_context.max_context_chars_total


def test_deerflow_config_path_is_repo_convention_not_app_setting():
    from lark_doc_whisper.config import DEERFLOW_CONFIG_PATH, ROOT, load_app_config

    app_config = load_app_config()

    assert DEERFLOW_CONFIG_PATH == ROOT / "configs" / "deerflow.yaml"
    assert not hasattr(app_config, "deerflow_config_path")


def test_deerflow_exposes_doc_context_tool():
    config = yaml.safe_load((ROOT / "configs" / "deerflow.yaml").read_text())

    tool_names = {tool["name"]: tool for tool in config["tools"]}

    assert tool_names["get_doc_context"]["use"] == "lark_doc_whisper.agent.doc_context:get_doc_context_tool"
    assert tool_names["search_user_recent_history"]["use"] == "lark_doc_whisper.agent.user_history:search_user_recent_history_tool"


def test_deerflow_exposes_url_fetch_tool_and_hides_read_file():
    config = yaml.safe_load((ROOT / "configs" / "deerflow.yaml").read_text())
    tool_names = {tool["name"]: tool for tool in config["tools"]}

    assert "fetch_url_content" in tool_names
    assert tool_names["fetch_url_content"]["use"] == "lark_doc_whisper.agent.url_fetch:fetch_url_content_tool"
    assert "read_file" not in tool_names


def test_deerflow_loader_resolves_doc_context_tool(monkeypatch):
    from deerflow.config.app_config import reload_app_config
    from deerflow.tools import get_available_tools

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    app_config = reload_app_config(str(ROOT / "configs" / "deerflow.yaml"))

    tool_names = {tool.name for tool in get_available_tools(app_config=app_config)}

    assert "get_doc_context" in tool_names
    assert "search_user_recent_history" in tool_names
    assert "fetch_url_content" in tool_names
    assert "read_file" not in tool_names
