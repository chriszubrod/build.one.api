import asyncio

import pytest
from fastapi import HTTPException

import shared.api.admin as admin
from shared.api.admin import sync_qbo_router


def test_sync_qbo_non_2xx_status_raises_502(monkeypatch):
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


def test_sync_qbo_2xx_status_returns_200_envelope(monkeypatch):
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
