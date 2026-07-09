"""DeerFlowBackend: wraps deerflow.client.DeerFlowClient with our thread_id."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from .doc_context import (
    DocPromptContext,
    DocContextProvider,
    DocumentContextMiddleware,
    current_doc_context,
    current_doc_context_bag,
    current_doc_context_provider,
)
from .url_fetch import UrlFetchContext, current_url_fetch_context
from ..state.paths import DEERFLOW_WORKSPACE_DIR

logger = logging.getLogger(__name__)


class DeerFlowBackend:
    def __init__(
        self,
        config_path: Path,
        checkpointer_cfg: dict[str, Any],
        *,
        model_name: Optional[str] = None,
    ):
        # Tell deerflow where its workspace lives so artifacts/uploads stay
        # inside our runtime/ directory rather than polluting the project root.
        DEERFLOW_WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("DEER_FLOW_DATA_DIR", str(DEERFLOW_WORKSPACE_DIR))
        # Pin deerflow's config to ours; otherwise it walks up to the cwd and
        # may pick up an unrelated config.yaml.
        os.environ["DEER_FLOW_CONFIG_PATH"] = str(config_path)

        # Build our OWN sqlite checkpointer rather than using deerflow's
        # singleton — deerflow's provider closes the underlying connection
        # whenever app config is reloaded, which leaves the cached saver
        # pointing at a closed db (sqlite3.ProgrammingError).
        self._checkpointer = self._build_checkpointer(checkpointer_cfg)
        logger.info("checkpointer ready: %s", type(self._checkpointer).__name__)

        from deerflow.client import DeerFlowClient

        self._client = DeerFlowClient(
            config_path=str(config_path),
            checkpointer=self._checkpointer,
            model_name=model_name,
            thinking_enabled=True,
            subagent_enabled=False,
            middlewares=[DocumentContextMiddleware()],
        )
        logger.info(
            "DeerFlowBackend ready: config=%s checkpointer=%s",
            config_path, checkpointer_cfg,
        )

    @staticmethod
    def _build_checkpointer(cfg: dict[str, Any]):
        kind = cfg.get("type", "memory")
        if kind == "memory":
            from langgraph.checkpoint.memory import InMemorySaver
            return InMemorySaver()
        if kind == "sqlite":
            import sqlite3
            from langgraph.checkpoint.sqlite import SqliteSaver

            conn_str = cfg.get("connection_string") or "store.db"
            Path(conn_str).parent.mkdir(parents=True, exist_ok=True)
            # Build the sqlite connection ourselves so its lifetime is tied
            # to this process — not to any context manager that might close
            # it out from under us. SqliteSaver is documented to be safe with
            # check_same_thread=False as it serializes via an internal lock.
            conn = sqlite3.connect(conn_str, check_same_thread=False)
            saver = SqliteSaver(conn)
            saver.setup()
            return saver
        raise ValueError(f"unsupported checkpointer type: {kind!r}")

    def chat(
        self,
        thread_id: str,
        user_query: str,
        *,
        doc_context: Optional[str | DocPromptContext] = None,
        doc_context_provider: Optional[DocContextProvider] = None,
        url_fetch_context: Optional[UrlFetchContext] = None,
    ) -> str:
        if isinstance(doc_context, str) and doc_context:
            ctx = DocPromptContext(
                file_token=thread_id,
                comment_id="",
                contexts={"document": doc_context},
            )
        elif isinstance(doc_context, DocPromptContext):
            ctx = doc_context
        else:
            ctx = None

        if ctx is None and doc_context_provider is not None:
            raise RuntimeError("doc_context_provider requires doc_context")

        if ctx is None:
            url_token = current_url_fetch_context.set(url_fetch_context)
            try:
                return self._client.chat(user_query, thread_id=thread_id)
            finally:
                current_url_fetch_context.reset(url_token)

        token = current_doc_context.set(ctx)
        bag_token = current_doc_context_bag.set({})
        provider_token = current_doc_context_provider.set(doc_context_provider)
        url_token = current_url_fetch_context.set(url_fetch_context)
        try:
            return self._client.chat(user_query, thread_id=thread_id)
        finally:
            current_url_fetch_context.reset(url_token)
            current_doc_context_provider.reset(provider_token)
            current_doc_context_bag.reset(bag_token)
            current_doc_context.reset(token)
