"""In-memory user-scoped document access tokens.

These tokens are intentionally not persisted. A gateway restart, expiry, or
read failure simply sends the user through OAuth again.
"""
from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class UserDocToken:
    access_token: str
    expires_at: float


class InMemoryUserDocTokenStore:
    def __init__(
        self,
        *,
        now: Callable[[], float] = time.time,
        expiry_skew_sec: int = 300,
    ) -> None:
        self._now = now
        self._expiry_skew_sec = max(0, expiry_skew_sec)
        self._tokens: dict[tuple[str, str], UserDocToken] = {}
        self._lock = threading.Lock()

    def put(
        self,
        user_open_id: str,
        link_url: str,
        access_token: str,
        *,
        expires_in: int,
    ) -> None:
        if not user_open_id or not link_url or not access_token or expires_in <= 0:
            return
        with self._lock:
            self._tokens[(user_open_id, self._normalize(link_url))] = UserDocToken(
                access_token=access_token,
                expires_at=self._now() + expires_in,
            )

    def get(self, user_open_id: str, link_url: str) -> str | None:
        key = (user_open_id, self._normalize(link_url))
        with self._lock:
            token = self._tokens.get(key)
            if token is None:
                return None
            if self._now() + self._expiry_skew_sec >= token.expires_at:
                self._tokens.pop(key, None)
                return None
            return token.access_token

    def delete(self, user_open_id: str, link_url: str) -> None:
        with self._lock:
            self._tokens.pop((user_open_id, self._normalize(link_url)), None)

    def prune_expired(self, *, now: float | None = None) -> int:
        cutoff = (self._now() if now is None else now) + self._expiry_skew_sec
        with self._lock:
            expired = [
                key for key, token in self._tokens.items()
                if cutoff >= token.expires_at
            ]
            for key in expired:
                self._tokens.pop(key, None)
        return len(expired)

    @staticmethod
    def _normalize(link_url: str) -> str:
        return link_url.strip().rstrip(").,]")
