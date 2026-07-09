"""Tools for user-scoped recent Q/A memory."""
from __future__ import annotations

from contextvars import ContextVar

from langchain_core.tools import tool

from .doc_context import current_doc_context
from ..state.user_memory import SqliteUserMemoryStore, default_store

current_user_memory_store: ContextVar[SqliteUserMemoryStore | None] = ContextVar(
    "lark_doc_whisper_user_memory_store",
    default=None,
)


def _get_store() -> SqliteUserMemoryStore:
    return current_user_memory_store.get() or default_store


@tool("search_user_recent_history")
def search_user_recent_history_tool(query: str, limit: int = 5) -> str:
    """Search this user's recent Q/A episode summaries across documents."""
    ctx = current_doc_context.get()
    if ctx is None or not ctx.user_id:
        return "[user recent history unavailable: no active user]"

    bounded_limit = max(1, min(int(limit or 5), 10))
    results = _get_store().search(
        ctx.user_id,
        query,
        limit=bounded_limit,
        ttl_sec=ctx.user_memory_ttl_sec,
    )
    if not results:
        return "[user recent history: no relevant episodes found]"

    lines = ["<user-recent-history>"]
    for idx, episode in enumerate(results, start=1):
        keywords = ", ".join(episode.keywords)
        lines.extend(
            [
                f"<episode index=\"{idx}\" doc_token=\"{episode.doc_token}\" comment_id=\"{episode.comment_id}\">",
                f"<summary>{episode.summary}</summary>",
                f"<keywords>{keywords}</keywords>",
                "</episode>",
            ]
        )
    lines.append("</user-recent-history>")
    return "\n".join(lines)
