"""Maintainer notification interface for failure events.

The default notifier is intentionally a no-op; production delivery channels can
be added behind this interface without coupling them to the comment reply path.
"""
from __future__ import annotations

from typing import Protocol

from ..state.failure_events import FailureEvent, SqliteFailureEventStore


class Notifier(Protocol):
    def notify(self, event: FailureEvent) -> None:
        ...


class NullNotifier:
    def notify(self, event: FailureEvent) -> None:
        return None


default_notifier = NullNotifier()


def drain_failure_events(
    store: SqliteFailureEventStore,
    notifier: Notifier,
    *,
    limit: int = 20,
) -> int:
    sent = 0
    for event in store.list_pending(limit=limit):
        notifier.notify(event)
        store.mark_notified(event.event_id)
        sent += 1
    return sent
