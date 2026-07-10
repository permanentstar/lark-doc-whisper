from __future__ import annotations

from pathlib import Path

import pytest


def _clear_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LARK_APP_ID", "LARK_APP_SECRET", "LLM_API_KEY", "WHISPER_REDIRECT_URI"):
        monkeypatch.delenv(key, raising=False)


def test_load_env_prefers_configs_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from lark_doc_whisper import config

    _clear_required_env(monkeypatch)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    configs_env = configs_dir / ".env"
    configs_env.write_text(
        "LARK_APP_ID=config-app\n"
        "LARK_APP_SECRET=config-secret\n"
        "LLM_API_KEY=config-llm\n"
    )
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    home_env = home_dir / ".env"
    home_env.write_text(
        "LARK_APP_ID=home-app\n"
        "LARK_APP_SECRET=home-secret\n"
        "LLM_API_KEY=home-llm\n"
    )
    monkeypatch.setattr(config, "APP_CONFIG_PATH", configs_dir / "app.yaml")
    monkeypatch.setattr(config, "ENV_CANDIDATES", (configs_env, home_env), raising=False)

    env = config.load_env(require_llm=True)

    assert env == {
        "LARK_APP_ID": "config-app",
        "LARK_APP_SECRET": "config-secret",
        "LLM_API_KEY": "config-llm",
    }


def test_load_env_falls_back_to_home_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from lark_doc_whisper import config

    _clear_required_env(monkeypatch)
    missing_configs_env = tmp_path / "configs" / ".env"
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    home_env = home_dir / ".env"
    home_env.write_text(
        "LARK_APP_ID=home-app\n"
        "LARK_APP_SECRET=home-secret\n"
    )
    monkeypatch.setattr(config, "ENV_CANDIDATES", (missing_configs_env, home_env), raising=False)

    env = config.load_env()

    assert env == {
        "LARK_APP_ID": "home-app",
        "LARK_APP_SECRET": "home-secret",
    }


def test_load_env_allows_shell_environment_without_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from lark_doc_whisper import config

    _clear_required_env(monkeypatch)
    monkeypatch.setenv("LARK_APP_ID", "shell-app")
    monkeypatch.setenv("LARK_APP_SECRET", "shell-secret")
    monkeypatch.setattr(
        config,
        "ENV_CANDIDATES",
        (tmp_path / "configs" / ".env", tmp_path / "home" / ".env"),
        raising=False,
    )

    env = config.load_env()

    assert env == {
        "LARK_APP_ID": "shell-app",
        "LARK_APP_SECRET": "shell-secret",
    }


def test_load_env_error_lists_new_candidate_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from lark_doc_whisper import config

    _clear_required_env(monkeypatch)
    configs_env = tmp_path / "configs" / ".env"
    home_env = tmp_path / "home" / ".env"
    monkeypatch.setattr(config, "ENV_CANDIDATES", (configs_env, home_env), raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        config.load_env()

    message = str(exc_info.value)
    assert "LARK_APP_ID not set" in message
    assert str(configs_env) in message
    assert str(home_env) in message


def test_load_app_config_reads_url_authorization_block(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from lark_doc_whisper import config

    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(
        """
file_type_default: docx
doc_cache_ttl_sec: 300
event_dedup_ttl_sec: 86400
user_memory_ttl_sec: 2592000
state_cleanup_interval_sec: 600
url_fetch:
  authorization:
    enabled: true
    authorize_base_url: https://accounts.feishu.cn/open-apis/authen/v1/authorize
    redirect_uri: https://assistant.example.com/lark/oauth/callback
    scopes:
      - docx:document:readonly
      - drive:drive:readonly
oauth_callback:
  enabled: true
  host: 0.0.0.0
  port: 8088
deerflow:
  checkpointer:
    type: memory
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "APP_CONFIG_PATH", app_yaml)

    cfg = config.load_app_config()

    assert cfg.url_fetch.authorization.enabled is True
    assert cfg.url_fetch.authorization.authorize_base_url == (
        "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    )
    assert cfg.url_fetch.authorization.redirect_uri == (
        "https://assistant.example.com/lark/oauth/callback"
    )
    assert cfg.url_fetch.authorization.scopes == (
        "docx:document:readonly",
        "drive:drive:readonly",
    )
    assert cfg.oauth_callback.enabled is True
    assert cfg.oauth_callback.host == "0.0.0.0"
    assert cfg.oauth_callback.port == 8088


def test_load_app_config_expands_env_references_from_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from lark_doc_whisper import config

    _clear_required_env(monkeypatch)
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    configs_env = configs_dir / ".env"
    configs_env.write_text(
        "LARK_APP_ID=config-app\n"
        "LARK_APP_SECRET=config-secret\n"
        "WHISPER_REDIRECT_URI=http://10.37.14.13:8088/oauth/callback\n",
        encoding="utf-8",
    )
    app_yaml = configs_dir / "app.yaml"
    app_yaml.write_text(
        """
file_type_default: docx
doc_cache_ttl_sec: 300
event_dedup_ttl_sec: 86400
user_memory_ttl_sec: 2592000
state_cleanup_interval_sec: 600
url_fetch:
  authorization:
    enabled: true
    redirect_uri: ${WHISPER_REDIRECT_URI}
    scopes:
      - docx:document:readonly
oauth_callback:
  enabled: true
  host: 0.0.0.0
  port: 8088
deerflow:
  checkpointer:
    type: memory
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "APP_CONFIG_PATH", app_yaml)
    monkeypatch.setattr(config, "ENV_CANDIDATES", (configs_env,), raising=False)

    cfg = config.load_app_config()

    assert cfg.url_fetch.authorization.redirect_uri == "http://10.37.14.13:8088/oauth/callback"


def test_load_app_config_fails_fast_when_env_reference_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    from lark_doc_whisper import config

    _clear_required_env(monkeypatch)
    app_yaml = tmp_path / "app.yaml"
    app_yaml.write_text(
        """
file_type_default: docx
doc_cache_ttl_sec: 300
event_dedup_ttl_sec: 86400
user_memory_ttl_sec: 2592000
state_cleanup_interval_sec: 600
url_fetch:
  authorization:
    enabled: true
    redirect_uri: ${WHISPER_REDIRECT_URI}
    scopes:
      - docx:document:readonly
deerflow:
  checkpointer:
    type: memory
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "APP_CONFIG_PATH", app_yaml)
    monkeypatch.setattr(config, "ENV_CANDIDATES", (), raising=False)

    with pytest.raises(RuntimeError, match="WHISPER_REDIRECT_URI"):
        config.load_app_config()
