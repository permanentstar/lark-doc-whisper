"""Runtime path resolution. All runtime/state files live under ``runtime/``."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

RUNTIME_DIR = ROOT / "runtime"
LOGS_DIR = RUNTIME_DIR / "logs"
STATE_DIR = RUNTIME_DIR / "state"
DOC_CACHE_DIR = STATE_DIR / "doc_cache"
SEEN_EVENTS_PATH = STATE_DIR / "seen_events.json"
DEERFLOW_WORKSPACE_DIR = RUNTIME_DIR / "deerflow_workspace"


def ensure_dirs() -> None:
    for d in (LOGS_DIR, STATE_DIR, DOC_CACHE_DIR, DEERFLOW_WORKSPACE_DIR):
        d.mkdir(parents=True, exist_ok=True)
