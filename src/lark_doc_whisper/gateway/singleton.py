"""Slot-scoped single-instance lock for the gateway."""
from __future__ import annotations

import fcntl
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..state.paths import RUNTIME_DIR

_SAFE_ID_CHARS = re.compile(r"[^a-zA-Z0-9._-]")


class AnotherInstanceRunning(RuntimeError):
    pass


@dataclass
class SingleInstanceLock:
    path: Path
    _fd: int | None = None

    @classmethod
    def for_app(
        cls,
        app_id: str,
        *,
        slot: str,
        locks_dir: Path | None = None,
    ) -> "SingleInstanceLock":
        if not app_id:
            raise ValueError("app_id must not be empty")
        safe_app = _SAFE_ID_CHARS.sub("_", app_id)
        safe_slot = _SAFE_ID_CHARS.sub("_", slot or "0")
        base_dir = locks_dir or (RUNTIME_DIR / "locks")
        return cls(base_dir / f"gateway_{safe_app}_slot_{safe_slot}.lock")

    def __enter__(self) -> "SingleInstanceLock":
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise AnotherInstanceRunning(f"another gateway instance holds {self.path}") from exc

        os.ftruncate(fd, 0)
        payload = f"{os.getpid()}\n{datetime.now(timezone.utc).isoformat()}\n"
        os.write(fd, payload.encode("utf-8"))
        self._fd = fd
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def close(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None
