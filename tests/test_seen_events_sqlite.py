from __future__ import annotations

import threading
import time

from lark_doc_whisper.state import seen_events


def test_seen_event_visible_across_store_instances(tmp_path):
    db_path = tmp_path / "seen_events.db"

    store_a = seen_events.SqliteSeenEventsStore(db_path)
    store_b = seen_events.SqliteSeenEventsStore(db_path)

    assert store_b.is_seen("evt_1", ttl_sec=60) is False
    store_a.mark_seen("evt_1")
    assert store_b.is_seen("evt_1", ttl_sec=60) is True


def test_expired_event_reads_as_unseen_without_delete(tmp_path):
    db_path = tmp_path / "seen_events.db"
    store = seen_events.SqliteSeenEventsStore(db_path)

    store.mark_seen("evt_old")
    with store._connect() as conn:
        conn.execute("UPDATE seen_events SET seen_at = ?", (time.time() - 120,))

    # is_seen is now a pure read: expired -> False, but the row stays put.
    assert store.is_seen("evt_old", ttl_sec=60) is False
    with store._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM seen_events WHERE event_id = ?", ("evt_old",)).fetchone()[0]
    assert count == 1


def test_mark_seen_does_not_delete_expired_rows(tmp_path):
    db_path = tmp_path / "seen_events.db"
    store = seen_events.SqliteSeenEventsStore(db_path)

    store.mark_seen("evt_old")
    with store._connect() as conn:
        conn.execute("UPDATE seen_events SET seen_at = ?", (time.time() - 120,))

    store.mark_seen("evt_new")

    with store._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM seen_events").fetchone()[0]
    assert count == 2


def test_concurrent_mark_same_event_is_idempotent(tmp_path):
    db_path = tmp_path / "seen_events.db"
    stores = [seen_events.SqliteSeenEventsStore(db_path) for _ in range(8)]
    errors: list[BaseException] = []

    def mark(store: seen_events.SqliteSeenEventsStore) -> None:
        try:
            store.mark_seen("evt_concurrent")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=mark, args=(store,)) for store in stores]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3)

    assert errors == []
    with stores[0]._connect() as conn:
        rows = conn.execute("SELECT event_id FROM seen_events WHERE event_id = ?", ("evt_concurrent",)).fetchall()
    assert rows == [("evt_concurrent",)]
