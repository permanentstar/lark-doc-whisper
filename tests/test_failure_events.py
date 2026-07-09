from __future__ import annotations

from lark_doc_whisper.notify.notifier import NullNotifier, drain_failure_events
from lark_doc_whisper.state.failure_events import FailureEvent, SqliteFailureEventStore


def test_failure_event_store_round_trip(tmp_path):
    store = SqliteFailureEventStore(tmp_path / "failure_events.db")
    event = FailureEvent(
        event_id="evt_backend_timeout",
        file_token="doc_token",
        comment_id="comment_1",
        reply_id="reply_1",
        user_id="ou_user",
        session_id="doc__doc_token__user__ou_user",
        stage="backend_chat",
        error_type="TimeoutError",
        error_message="backend chat timed out",
        fallback_reply_text="目前在神游，稍后回来。",
        fallback_reply_succeeded=True,
        created_at=1000.0,
        notified_at=None,
    )

    store.add_event(event)
    pending = store.list_pending(limit=10)

    assert len(pending) == 1
    assert pending[0].event_id == "evt_backend_timeout"
    assert pending[0].fallback_reply_succeeded is True


def test_failure_event_store_marks_notified(tmp_path):
    store = SqliteFailureEventStore(tmp_path / "failure_events.db")
    store.add_event(
        FailureEvent(
            event_id="evt_post_reply_failed",
            file_token="doc_token",
            comment_id="comment_1",
            reply_id="reply_1",
            user_id="ou_user",
            session_id="doc__doc_token__user__ou_user",
            stage="post_reply",
            error_type="ReplyWriteError",
            error_message="post_reply returned no reply_id",
            fallback_reply_text="目前在神游，稍后回来。",
            fallback_reply_succeeded=False,
            created_at=1000.0,
            notified_at=None,
        )
    )

    store.mark_notified("evt_post_reply_failed", notified_at=2000.0)

    assert store.list_pending(limit=10) == []


def test_drain_failure_events_marks_rows_notified(tmp_path):
    class _Recorder(NullNotifier):
        def __init__(self) -> None:
            self.calls = []

        def notify(self, event):
            self.calls.append(event.event_id)

    store = SqliteFailureEventStore(tmp_path / "failure_events.db")
    store.add_event(
        FailureEvent(
            event_id="evt_notify_me",
            file_token="doc_token",
            comment_id="comment_1",
            reply_id="reply_1",
            user_id="ou_user",
            session_id="doc__doc_token__user__ou_user",
            stage="backend_chat",
            error_type="RuntimeError",
            error_message="boom",
            fallback_reply_text="目前在神游，稍后回来。",
            fallback_reply_succeeded=True,
            created_at=1000.0,
            notified_at=None,
        )
    )

    notifier = _Recorder()
    assert drain_failure_events(store, notifier, limit=10) == 1
    assert notifier.calls == ["evt_notify_me"]
    assert store.list_pending(limit=10) == []
