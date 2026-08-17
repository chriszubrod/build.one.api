import asyncio
import time
from pathlib import Path

import pytest
from fastapi import BackgroundTasks

import entities.attachment.api.router as attachment_router
from entities.attachment.api.router import (
    upload_attachment_router,
    upload_bill_line_item_attachment_router,
)
from entities.attachment.business.model import Attachment
from shared.authz.context import current_user_id

STARTUP_SH = Path(__file__).resolve().parents[1] / "startup.sh"

CURRENT_USER = {"id": 1, "tenant_id": 1}


class FakeUploadFile:
    def __init__(
        self,
        filename: str = "test.pdf",
        content_type: str = "application/pdf",
        content: bytes = b"fake-pdf-bytes",
    ):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _fake_attachment() -> Attachment:
    return Attachment(
        id=1,
        public_id="00000000-0000-0000-0000-000000000001",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        filename="test.pdf",
        original_filename="test.pdf",
        file_extension="pdf",
        content_type="application/pdf",
        file_size=14,
        file_hash="fakehash",
        blob_url="https://fake.blob/test.pdf",
        description=None,
        category=None,
        tags=None,
        is_archived=False,
        status=None,
        download_count=0,
        last_downloaded_datetime=None,
        expiration_date=None,
        storage_tier="Hot",
    )


def _install_upload_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow_compact(data: bytes) -> bytes:
        time.sleep(0.2)
        return data

    monkeypatch.setattr(attachment_router, "compact_pdf", slow_compact)
    monkeypatch.setattr(attachment_router.service, "validate_file_size", lambda size: None)
    monkeypatch.setattr(attachment_router.service, "calculate_hash", lambda data: "fakehash")
    monkeypatch.setattr(attachment_router.service, "read_by_hash", lambda h: None)
    monkeypatch.setattr(attachment_router.service, "extract_extension", lambda fn: "pdf")
    monkeypatch.setattr(
        attachment_router.service,
        "build_blob_name",
        lambda public_id, ext: f"{public_id}.{ext}",
    )

    class FakeAzureBlobStorage:
        def upload_file(self, blob_name, file_content, content_type):
            return f"https://fake.blob/{blob_name}"

    monkeypatch.setattr(attachment_router, "AzureBlobStorage", FakeAzureBlobStorage)
    monkeypatch.setattr(attachment_router.service, "create", lambda **kwargs: _fake_attachment())


async def _run_upload_with_concurrent_ticker(upload_coro):
    upload_task = asyncio.ensure_future(upload_coro)
    ticks_before_done = 0
    while not upload_task.done():
        await asyncio.sleep(0.01)
        ticks_before_done += 1
        if ticks_before_done > 100:  # safety bound (~1s) so a genuine hang doesn't hang the test
            break
    result = await upload_task
    return result, ticks_before_done


def test_upload_attachment_router_offloads_blocking_work(monkeypatch):
    _install_upload_fakes(monkeypatch)

    result, ticks_before_done = asyncio.run(
        _run_upload_with_concurrent_ticker(
            upload_attachment_router(
                BackgroundTasks(),
                FakeUploadFile(),
                None,
                None,
                None,
                None,
                None,
                CURRENT_USER,
            )
        )
    )
    assert ticks_before_done >= 5
    assert result["data"]["public_id"] == "00000000-0000-0000-0000-000000000001"


def test_upload_bill_line_item_attachment_router_offloads_blocking_work(monkeypatch):
    _install_upload_fakes(monkeypatch)

    result, ticks_before_done = asyncio.run(
        _run_upload_with_concurrent_ticker(
            upload_bill_line_item_attachment_router(
                BackgroundTasks(),
                FakeUploadFile(),
                None,
                CURRENT_USER,
            )
        )
    )
    assert ticks_before_done >= 5
    assert result["data"]["public_id"] == "00000000-0000-0000-0000-000000000001"


def test_upload_attachment_router_propagates_current_user_id_to_worker_thread(monkeypatch):
    captured_user_id = None

    def capture_create(**kwargs):
        nonlocal captured_user_id
        captured_user_id = current_user_id.get()
        return _fake_attachment()

    _install_upload_fakes(monkeypatch)
    monkeypatch.setattr(attachment_router.service, "create", capture_create)

    token = current_user_id.set(42)
    try:
        asyncio.run(
            upload_attachment_router(
                BackgroundTasks(),
                FakeUploadFile(),
                None,
                None,
                None,
                None,
                None,
                CURRENT_USER,
            )
        )
    finally:
        current_user_id.reset(token)

    assert captured_user_id == 42


def test_startup_sh_gunicorn_timeout_and_worker_count():
    text = STARTUP_SH.read_text(encoding="utf-8")
    assert "-w 2" in text
    assert "--timeout" in text
    assert "180" in text
