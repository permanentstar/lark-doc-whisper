"""HarnessBackend protocol — abstract contract for an agent runtime.

P1 only has one implementation (DeerFlowBackend), but this protocol exists so
the comment_handler can stay decoupled from any specific framework.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol

from .doc_context import DocPromptContext


class HarnessBackend(Protocol):
    def chat(
        self,
        thread_id: str,
        user_query: str,
        *,
        doc_context: Optional[str | DocPromptContext] = None,
        doc_context_provider: Optional[Any] = None,
        url_fetch_context: Optional[Any] = None,
    ) -> str:
        """Return the final assistant message text. Blocking."""
        ...
