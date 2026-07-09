from __future__ import annotations

from pathlib import Path

import pytest


def _clear_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("LARK_APP_ID", "LARK_APP_SECRET", "LLM_API_KEY"):
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
