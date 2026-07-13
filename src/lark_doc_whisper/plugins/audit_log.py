"""Append-only JSONL audit log for @-mention events.

Off by default; deployment operators enable it via ``plugins:`` in
``configs/app.yaml`` to observe traffic and diagnose live incidents.
Deliberately does not persist the user's query text — the mention envelope
alone is enough for ops without expanding the sensitive data blast radius.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..state.paths import LOGS_DIR
from .base import PluginBuildCtx

logger = logging.getLogger(__name__)

DEFAULT_PATH = LOGS_DIR / "audit.jsonl"


class AuditLogPlugin:
    name = "audit_log"

    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._pending: set[asyncio.Task] = set()

    @property
    def pending_tasks(self) -> set[asyncio.Task]:
        return self._pending

    def on_mention_event(self, header, meta) -> None:
        record = _build_record(header, meta)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _append_line(self._path, record)
            return
        task = loop.create_task(asyncio.to_thread(_append_line, self._path, record))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def on_failure(self, event) -> None:
        # Failure events are already persisted to failure_events.db; no-op here.
        return None


def _build_record(header, meta) -> dict[str, Any]:
    def _get(obj, name: str, default: Any = "") -> Any:
        if obj is None:
            return default
        return getattr(obj, name, default) or default

    return {
        "ts_iso": datetime.now(timezone.utc).isoformat(),
        "event_id": _get(header, "event_id", "") or _get(meta, "event_id", ""),
        "event_type": _get(header, "event_type", ""),
        "tenant_key": _get(header, "tenant_key", ""),
        "app_id": _get(header, "app_id", ""),
        "create_time": _get(header, "create_time", ""),
        "file_token": _get(meta, "file_token", ""),
        "file_type": _get(meta, "file_type", ""),
        "comment_id": _get(meta, "comment_id", ""),
        "reply_id": _get(meta, "reply_id", ""),
        "from_open_id": _get(meta, "from_open_id", ""),
        "to_open_id": _get(meta, "to_open_id", ""),
        "is_mentioned": bool(_get(meta, "is_mentioned", False)),
    }


def _append_line(path: Path, record: Mapping[str, Any]) -> None:
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def build_audit_log_plugin(ctx: PluginBuildCtx, options: Mapping[str, Any]) -> AuditLogPlugin:
    path = options.get("path")
    if path:
        return AuditLogPlugin(path=Path(str(path)))
    return AuditLogPlugin()
