from __future__ import annotations

import pytest

from lark_doc_whisper.gateway.singleton import AnotherInstanceRunning, SingleInstanceLock


def test_same_app_and_slot_second_lock_fails(tmp_path):
    first = SingleInstanceLock.for_app("cli_test", slot="0", locks_dir=tmp_path)
    second = SingleInstanceLock.for_app("cli_test", slot="0", locks_dir=tmp_path)

    with first:
        with pytest.raises(AnotherInstanceRunning):
            with second:
                pass


def test_different_slots_can_coexist(tmp_path):
    first = SingleInstanceLock.for_app("cli_test", slot="0", locks_dir=tmp_path)
    second = SingleInstanceLock.for_app("cli_test", slot="1", locks_dir=tmp_path)

    with first, second:
        assert first.path != second.path


def test_lock_released_after_context_exit(tmp_path):
    lock = SingleInstanceLock.for_app("cli_test", slot="0", locks_dir=tmp_path)

    with lock:
        pass

    with SingleInstanceLock.for_app("cli_test", slot="0", locks_dir=tmp_path):
        pass


def test_app_id_is_sanitized(tmp_path):
    lock = SingleInstanceLock.for_app("cli_../bad/app", slot="0", locks_dir=tmp_path)

    assert lock.path.parent == tmp_path
    assert lock.path.name == "gateway_cli_.._bad_app_slot_0.lock"
