import asyncio
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

import integrations.intuit.qbo.base.locking as locking
import shared.api.admin as admin
from shared.api.admin import sync_qbo_router


@contextmanager
def _granted_lock(resource_name, timeout_ms=0):
    yield True


@pytest.fixture
def mock_qbo_app_lock_granted(monkeypatch):
    """Prevent real sp_getapplock round-trips; always grant the lock.

    U-347: `shared/api/admin.py` no longer imports `qbo_app_lock` directly
    (it repoints onto `qbo_sync_lock`, which is the sole caller of
    `qbo_app_lock` left in the codebase) — patch it at the source in
    `locking.py`, same as test_u337/test_u340/test_u346.
    """
    monkeypatch.setattr(locking, "qbo_app_lock", _granted_lock)


def test_sync_qbo_non_2xx_status_raises_502(monkeypatch, mock_qbo_app_lock_granted):
    monkeypatch.setattr(
        admin,
        "_qbo_sync_fn",
        lambda entity: (
            lambda: {"result": {"success": False, "error": "boom"}, "status_code": 502}
        ),
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(sync_qbo_router(entity="bill", attachments=True))
    exc = exc_info.value
    assert exc.status_code == 502
    assert exc.detail["upstream_status"] == 502
    assert exc.detail["error"] == "boom"


def test_sync_qbo_2xx_status_returns_200_envelope(monkeypatch, mock_qbo_app_lock_granted):
    monkeypatch.setattr(
        admin,
        "_qbo_sync_fn",
        lambda entity: (
            lambda: {"result": {"success": True}, "status_code": 200}
        ),
    )
    env = asyncio.run(sync_qbo_router(entity="bill", attachments=True))
    assert env["status"] == "ok"
    assert env["job"] == "sync.qbo.bill"


def test_sync_qbo_skipped_when_lock_busy(monkeypatch):
    @contextmanager
    def _denied_lock_bill(resource_name, timeout_ms=0):
        assert resource_name == "qbo_sync:bill"
        assert timeout_ms == 0
        yield False

    monkeypatch.setattr(locking, "qbo_app_lock", _denied_lock_bill)

    sync_fn_called = False

    def _tracking_sync_fn():
        nonlocal sync_fn_called
        sync_fn_called = True
        return {"result": {"success": True}, "status_code": 200}

    monkeypatch.setattr(
        admin,
        "_qbo_sync_fn",
        lambda entity: _tracking_sync_fn,
    )

    env = asyncio.run(sync_qbo_router(entity="bill", attachments=True))
    assert env == {
        "status": "skipped",
        "job": "sync.qbo.bill",
        "reason": "lock_busy",
    }
    assert not sync_fn_called
