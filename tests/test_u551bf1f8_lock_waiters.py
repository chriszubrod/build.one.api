"""U-551b-f1-f8: one in-process waiter per suggestion lock resource; the rest fail fast (busy)."""
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from entities.coding_suggestion.business import service as svc

GUID = "3F2504E0-4F89-11D3-9A0C-0305E82C3301"
OTHER = "11111111-2222-3333-4444-555555555555"


class FakeLock:
    def __init__(self, acquire=True, raises=None):
        self.entries = []
        self.release = threading.Event()
        self.acquire = acquire
        self.raises = raises
        self._g = threading.Lock()

    @contextmanager
    def __call__(self, resource, timeout_ms=0):
        with self._g:
            self.entries.append(resource)
        self.release.wait(2)  # simulates blocking inside sp_getapplock
        if self.raises:
            raise self.raises
        yield self.acquire


def _until(pred, limit=2.0):
    end = time.monotonic() + limit
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def _waiter(results, public_id, key):
    def run():
        try:
            with svc.coding_suggestion_lock(public_id, timeout_ms=10_000) as acquired:
                results[key] = acquired
        except Exception as e:  # noqa: BLE001
            results[key] = e
    t = threading.Thread(target=run)
    t.start()
    return t


@pytest.fixture(autouse=True)
def _clean_registry():
    svc._LOCK_WAITERS.clear()
    yield
    svc._LOCK_WAITERS.clear()


def test_second_waiter_same_resource_fails_fast_without_entering_lock():
    # Kills: no waiter registry (second call would also enter qbo_app_lock); non-canonical key.
    fake = FakeLock()
    results = {}
    with patch.object(svc, "qbo_app_lock", fake):
        t = _waiter(results, GUID, "a")
        assert _until(lambda: len(fake.entries) == 1)
        with svc.coding_suggestion_lock(GUID.lower(), timeout_ms=10_000) as acquired:
            assert acquired is False
        assert len(fake.entries) == 1
        fake.release.set()
        t.join(2)
    assert results["a"] is True


def test_different_resource_still_enters():
    # Kills: a global (not per-resource) waiter limit.
    fake = FakeLock()
    results = {}
    with patch.object(svc, "qbo_app_lock", fake):
        t = _waiter(results, GUID, "a")
        assert _until(lambda: len(fake.entries) == 1)
        t2 = _waiter(results, OTHER, "b")
        assert _until(lambda: len(fake.entries) == 2)
        fake.release.set()
        t.join(2)
        t2.join(2)
    assert results == {"a": True, "b": True}


def test_registration_cleared_after_exit_so_next_call_enters():
    # Kills: registration never discarded at all (the 2nd call would fail fast).
    fake = FakeLock()
    fake.release.set()
    with patch.object(svc, "qbo_app_lock", fake):
        with svc.coding_suggestion_lock(GUID, timeout_ms=1) as a:
            assert a is True
        assert not svc._LOCK_WAITERS
        with svc.coding_suggestion_lock(GUID, timeout_ms=1) as a:
            assert a is True
    assert len(fake.entries) == 2


def test_timeout_and_error_paths_clear_registration():
    # Kills: registration leaked when qbo_app_lock reports timeout (False) or raises.
    fake = FakeLock(acquire=False)
    fake.release.set()
    with patch.object(svc, "qbo_app_lock", fake):
        with svc.coding_suggestion_lock(GUID, timeout_ms=1) as a:
            assert a is False
        assert not svc._LOCK_WAITERS
    boom = FakeLock(raises=RuntimeError("db"))
    boom.release.set()
    with patch.object(svc, "qbo_app_lock", boom):
        with pytest.raises(RuntimeError):
            with svc.coding_suggestion_lock(GUID, timeout_ms=1):
                pass
        assert not svc._LOCK_WAITERS
        with pytest.raises(RuntimeError):
            with svc.coding_suggestion_lock(GUID, timeout_ms=1):
                pass
    assert len(boom.entries) == 2


def test_holder_does_not_block_next_waiter_from_queueing():
    # Kills: registration held through the held region (confirm would fail fast against a drain).
    seen = []

    @contextmanager
    def instant(resource, timeout_ms=0):
        seen.append(resource)
        yield True

    with patch.object(svc, "qbo_app_lock", instant):
        with svc.coding_suggestion_lock(GUID, timeout_ms=1) as held:
            assert held is True
            with svc.coding_suggestion_lock(GUID, timeout_ms=1) as second:
                assert second is True
    assert len(seen) == 2


def test_holder_exit_does_not_evict_a_later_waiters_registration():
    # Kills: unconditional discard in finally (holder exit would evict T2's live entry, letting T3 enter).
    entries = []
    t2_in_wait = threading.Event()
    t2_release = threading.Event()
    t1_release = threading.Event()

    @contextmanager
    def fake(resource, timeout_ms=0):
        entries.append(resource)
        if len(entries) == 2:
            t2_in_wait.set()
            t2_release.wait(2)
        yield True

    results = {}

    def t1():
        with svc.coding_suggestion_lock(GUID, timeout_ms=1):
            t1_release.wait(2)
        results["t1"] = True

    with patch.object(svc, "qbo_app_lock", fake):
        a = threading.Thread(target=t1)
        a.start()
        assert _until(lambda: len(entries) == 1 and not svc._LOCK_WAITERS)
        b = _waiter(results, GUID, "t2")
        assert t2_in_wait.wait(2)
        t1_release.set()
        a.join(2)
        assert results.get("t1") is True
        assert svc._LOCK_WAITERS == {svc.coding_suggestion_lock_resource(GUID)}
        with svc.coding_suggestion_lock(GUID, timeout_ms=1) as acquired:
            assert acquired is False
        assert len(entries) == 2
        t2_release.set()
        b.join(2)
    assert results["t2"] is True
    assert not svc._LOCK_WAITERS


def test_holder_exit_after_timeout_does_not_evict_a_later_waiters_registration():
    # Kills: the same unconditional-finally discard via the timeout (False) path.
    entries = []
    t2_in_wait = threading.Event()
    t2_release = threading.Event()
    t1_release = threading.Event()

    @contextmanager
    def fake(resource, timeout_ms=0):
        entries.append(resource)
        if len(entries) == 1:
            yield False
            return
        t2_in_wait.set()
        t2_release.wait(2)
        yield True

    results = {}

    def t1():
        with svc.coding_suggestion_lock(GUID, timeout_ms=1) as acquired:
            results["t1_acquired"] = acquired
            t1_release.wait(2)

    with patch.object(svc, "qbo_app_lock", fake):
        a = threading.Thread(target=t1)
        a.start()
        assert _until(lambda: "t1_acquired" in results)
        b = _waiter(results, GUID, "t2")
        assert t2_in_wait.wait(2)
        t1_release.set()
        a.join(2)
        assert svc._LOCK_WAITERS == {svc.coding_suggestion_lock_resource(GUID)}
        with svc.coding_suggestion_lock(GUID, timeout_ms=1) as acquired:
            assert acquired is False
        assert len(entries) == 2
        t2_release.set()
        b.join(2)
    assert results["t1_acquired"] is False
    assert results["t2"] is True
