"""Configuration & environment loading.

- Secrets come from `configs/.env`, then `~/.env`: LARK_APP_ID /
  LARK_APP_SECRET / LLM_API_KEY
- App config comes from `configs/app.yaml`

`LLM_API_KEY` is the API key for the OpenAI-compatible model endpoint
configured in `configs/deerflow.yaml` (any provider works: Ark/Doubao,
OpenAI, DeepSeek, a local vLLM server, etc.).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .plugins.base import PluginSpec

ROOT = Path(__file__).resolve().parents[2]
APP_CONFIG_PATH = ROOT / "configs" / "app.yaml"
DEERFLOW_CONFIG_PATH = ROOT / "configs" / "deerflow.yaml"
ENV_CANDIDATES = (
    APP_CONFIG_PATH.parent / ".env",
    Path.home() / ".env",
)
ENV_REF_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


@dataclass(frozen=True)
class CommentContextConfig:
    default_nearby_before: int = 1
    default_nearby_after: int = 1
    max_nearby_before: int = 3
    max_nearby_after: int = 3
    default_thread_history_replies: int = 8
    default_thread_history_chars: int = 3_000
    max_thread_history_replies: int = 30
    max_thread_history_chars: int = 8_000
    max_fetch_rounds: int = 4
    max_context_chars: int = 12_000
    max_context_chars_total: int = 24_000
    enable_section: bool = True
    enable_document_summary: bool = True
    document_summary_chars: int = 4_000


@dataclass(frozen=True)
class FailureHandlingConfig:
    polite_reply_text: str = "目前在神游，稍后回来。"
    notifier_enabled: bool = False


@dataclass(frozen=True)
class UrlAuthorizationConfig:
    enabled: bool = False
    authorize_base_url: str = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
    redirect_uri: str = ""
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True)
class UrlFetchConfig:
    enabled: bool = True
    timeout_sec: int = 8
    max_redirects: int = 3
    max_response_bytes: int = 204_800
    allow_private_ip: bool = False
    allowed_content_types: tuple[str, ...] = (
        "text/plain",
        "text/html",
        "application/json",
        "application/xml",
        "text/xml",
    )
    authorization: UrlAuthorizationConfig = UrlAuthorizationConfig()


@dataclass(frozen=True)
class OAuthCallbackConfig:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8088


@dataclass(frozen=True)
class AppConfig:
    """应用运行期主配置。

    主要字段分组：
    - 文件默认值：`file_type_default`
    - 缓存与幂等：`doc_cache_ttl_sec`、`event_dedup_ttl_sec`、`user_memory_ttl_sec`
    - 状态清理：`state_cleanup_interval_sec`
    - DeerFlow 后端：`deerflow_checkpointer_cfg`
    - 功能子配置：`comment_context`、`failure_handling`、`url_fetch`
    - 运行期并发与超时：`event_queue_size`、`event_worker_count`、
      `max_backend_in_flight`、`backend_timeout_sec`、
      `episode_summary_timeout_sec`（LLM 提炼记忆的短超时）
    """
    file_type_default: str
    doc_cache_ttl_sec: int
    event_dedup_ttl_sec: int
    user_memory_ttl_sec: int
    state_cleanup_interval_sec: int
    deerflow_checkpointer_cfg: dict[str, Any]
    comment_context: CommentContextConfig = CommentContextConfig()
    failure_handling: FailureHandlingConfig = FailureHandlingConfig()
    url_fetch: UrlFetchConfig = UrlFetchConfig()
    oauth_callback: OAuthCallbackConfig = OAuthCallbackConfig()
    event_queue_size: int = 200
    event_worker_count: int = 8
    max_backend_in_flight: int = 8
    backend_timeout_sec: int = 300
    episode_summary_timeout_sec: int = 60
    plugins: tuple[PluginSpec, ...] = ()


def _load_env(require_llm: bool) -> dict[str, str]:
    for dotenv_path in ENV_CANDIDATES:
        if dotenv_path.is_file():
            load_dotenv(dotenv_path)
            break
    checked_paths = ", ".join(str(path) for path in ENV_CANDIDATES)
    required = ["LARK_APP_ID", "LARK_APP_SECRET"]
    if require_llm:
        required.append("LLM_API_KEY")
    out: dict[str, str] = {}
    for key in required:
        v = os.environ.get(key, "").strip()
        if not v or v == "__fill_me__":
            raise RuntimeError(f"{key} not set (checked {checked_paths})")
        out[key] = v
    return out


def load_env(*, require_llm: bool = False) -> dict[str, str]:
    """Return required env vars; raise if any missing.

    Set ``require_llm=True`` when launching the deerflow backend; lark-only
    flows (read/post comments) do not need the model endpoint's LLM_API_KEY.
    """
    return _load_env(require_llm=require_llm)


def _prime_env_from_dotenv() -> None:
    for dotenv_path in ENV_CANDIDATES:
        if dotenv_path.is_file():
            load_dotenv(dotenv_path)
            break


def _expand_env_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand_env_refs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_refs(item) for item in value]
    if not isinstance(value, str):
        return value

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        env_value = os.environ.get(key, "").strip()
        if not env_value or env_value == "__fill_me__":
            raise RuntimeError(f"{key} not set for config expansion")
        return env_value

    return ENV_REF_RE.sub(repl, value)


def load_app_config() -> AppConfig:
    if not APP_CONFIG_PATH.exists():
        raise RuntimeError(f"missing app config: {APP_CONFIG_PATH}")
    _prime_env_from_dotenv()
    with open(APP_CONFIG_PATH) as f:
        data: dict[str, Any] = _expand_env_refs(yaml.safe_load(f) or {})
    df = data.get("deerflow") or {}
    concurrency = data.get("concurrency") or {}
    cc = data.get("comment_context") or {}
    fh = data.get("failure_handling") or {}
    uf = data.get("url_fetch") or {}
    ufa = uf.get("authorization") or {}
    oc = data.get("oauth_callback") or {}
    raw_cp_cfg = dict(df.get("checkpointer") or {"type": "memory"})
    # Resolve relative connection_string against repo root so it doesn't
    # depend on the cwd where the process is launched from.
    cs = raw_cp_cfg.get("connection_string")
    if cs and not Path(cs).is_absolute():
        raw_cp_cfg["connection_string"] = str(ROOT / cs)
    return AppConfig(
        file_type_default=str(data.get("file_type_default", "docx")),
        doc_cache_ttl_sec=int(data.get("doc_cache_ttl_sec", 300)),
        event_dedup_ttl_sec=int(data.get("event_dedup_ttl_sec", 86400)),
        user_memory_ttl_sec=int(data.get("user_memory_ttl_sec", 2592000)),
        state_cleanup_interval_sec=int(data.get("state_cleanup_interval_sec", 600)),
        episode_summary_timeout_sec=int(data.get("episode_summary_timeout_sec", 60)),
        deerflow_checkpointer_cfg=raw_cp_cfg,
        comment_context=CommentContextConfig(
            default_nearby_before=int(cc.get("default_nearby_before", 1)),
            default_nearby_after=int(cc.get("default_nearby_after", 1)),
            max_nearby_before=int(cc.get("max_nearby_before", 3)),
            max_nearby_after=int(cc.get("max_nearby_after", 3)),
            default_thread_history_replies=int(cc.get("default_thread_history_replies", 8)),
            default_thread_history_chars=int(cc.get("default_thread_history_chars", 3_000)),
            max_thread_history_replies=int(cc.get("max_thread_history_replies", 30)),
            max_thread_history_chars=int(cc.get("max_thread_history_chars", 8_000)),
            max_fetch_rounds=int(cc.get("max_fetch_rounds", 4)),
            max_context_chars=int(cc.get("max_context_chars", 12_000)),
            max_context_chars_total=int(cc.get("max_context_chars_total", 24_000)),
            enable_section=bool(cc.get("enable_section", True)),
            enable_document_summary=bool(cc.get("enable_document_summary", True)),
            document_summary_chars=int(cc.get("document_summary_chars", 4_000)),
        ),
        failure_handling=FailureHandlingConfig(
            polite_reply_text=str(fh.get("polite_reply_text", "目前在神游，稍后回来。")),
            notifier_enabled=bool(fh.get("notifier_enabled", False)),
        ),
        url_fetch=UrlFetchConfig(
            enabled=bool(uf.get("enabled", True)),
            timeout_sec=int(uf.get("timeout_sec", 8)),
            max_redirects=int(uf.get("max_redirects", 3)),
            max_response_bytes=int(uf.get("max_response_bytes", 204_800)),
            allow_private_ip=bool(uf.get("allow_private_ip", False)),
            allowed_content_types=tuple(
                str(item) for item in (
                    uf.get("allowed_content_types") or [
                        "text/plain",
                        "text/html",
                        "application/json",
                        "application/xml",
                        "text/xml",
                    ]
                )
            ),
            authorization=UrlAuthorizationConfig(
                enabled=bool(ufa.get("enabled", False)),
                authorize_base_url=str(
                    ufa.get(
                        "authorize_base_url",
                        "https://accounts.feishu.cn/open-apis/authen/v1/authorize",
                    )
                ),
                redirect_uri=str(ufa.get("redirect_uri", "")),
                scopes=tuple(str(item) for item in (ufa.get("scopes") or [])),
            ),
        ),
        oauth_callback=OAuthCallbackConfig(
            enabled=bool(oc.get("enabled", False)),
            host=str(oc.get("host", "0.0.0.0")),
            port=int(oc.get("port", 8088)),
        ),
        event_queue_size=int(concurrency.get("event_queue_size", 200)),
        event_worker_count=int(concurrency.get("event_worker_count", 8)),
        max_backend_in_flight=int(concurrency.get("max_backend_in_flight", 8)),
        backend_timeout_sec=int(concurrency.get("backend_timeout_sec", 300)),
        plugins=_parse_plugin_specs(data.get("plugins")),
    )


def _parse_plugin_specs(raw: Any) -> tuple[PluginSpec, ...]:
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise RuntimeError(f"plugins must be a list, got {type(raw).__name__}")
    specs: list[PluginSpec] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise RuntimeError(f"plugin entry must be a mapping, got {entry!r}")
        name = str(entry.get("name", "")).strip()
        if not name:
            raise RuntimeError(f"plugin entry missing name: {entry!r}")
        options = entry.get("options") or {}
        if not isinstance(options, dict):
            raise RuntimeError(f"plugin options must be a mapping for {name!r}")
        specs.append(PluginSpec(name=name, options=options))
    return tuple(specs)
