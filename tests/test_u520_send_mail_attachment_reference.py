"""U-520 — review-submit enqueue stores a blob *reference*; send_mail fetches at drain.

The submit button was paying for an Azure download + base64 + a multi-MB
NVARCHAR(MAX) write. Enqueue now stores {name, content_type, blob_url};
the worker resolves that into Graph's content_bytes at drain, and still
accepts the legacy embedded-bytes shape so the cancelled row with
embedded base64 (and any future accidental embed) keeps sending.
Fetched bytes stay in a local — they must not be written back onto
the payload that ``update_payload`` persists.
"""

import base64
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business.notification_service import ReviewNotificationService
from integrations.ms.base.errors import MsNotFoundError, MsServerError
from integrations.ms.outbox.business.model import MsOutbox
from integrations.ms.outbox.business.worker import MsOutboxWorker
from shared.storage import AzureBlobStorageError

_CREATE_DRAFT = "integrations.ms.mail.external.client.create_draft"

PDF_BYTES = b"%PDF-1.4 legacy-or-fetched"
PDF_B64 = base64.b64encode(PDF_BYTES).decode("ascii")
FETCHED_BYTES = b"%PDF-1.4 fetched-at-drain"
FETCHED_B64 = base64.b64encode(FETCHED_BYTES).decode("ascii")


def _pdf_attachment(*, filename, blob_url, public_id="att-pdf"):
    return SimpleNamespace(
        public_id=public_id,
        blob_url=blob_url,
        content_type="application/pdf",
        filename=filename,
    )


def _jpeg_attachment(*, filename="scan.jpg", blob_url="attachments/scan.jpg"):
    return SimpleNamespace(
        public_id="att-jpg",
        blob_url=blob_url,
        content_type="image/jpeg",
        filename=filename,
    )


def _line(public_id):
    return SimpleNamespace(public_id=public_id, id=1)


# ---------------------------------------------------------------------------
# Enqueue: Bill
# ---------------------------------------------------------------------------


def test_bill_attachment_payload_is_reference_and_does_not_download():
    """The performance claim: enqueue must not touch Azure Blob Storage."""
    bill = SimpleNamespace(public_id="bill-pub")
    line_items = [_line("li-1")]
    bla_service = MagicMock()
    bla_service.read_by_bill_line_item_id.return_value = SimpleNamespace(
        attachment_id=9
    )
    attachment_service = MagicMock()
    attachment_service.read_by_id.return_value = _pdf_attachment(
        filename="vendor-invoice.pdf",
        blob_url="attachments/vendor-invoice.pdf",
    )
    storage = MagicMock()

    payload = ReviewNotificationService._build_attachment_payload(
        bill=bill,
        line_items=line_items,
        bla_service=bla_service,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload == {
        "name": "vendor-invoice.pdf",
        "content_type": "application/pdf",
        "blob_url": "attachments/vendor-invoice.pdf",
    }
    assert "content_bytes" not in payload
    storage.download_file.assert_not_called()


def test_bill_non_pdf_attachment_is_skipped():
    bill = SimpleNamespace(public_id="bill-pub")
    line_items = [_line("li-1")]
    bla_service = MagicMock()
    bla_service.read_by_bill_line_item_id.return_value = SimpleNamespace(
        attachment_id=9
    )
    attachment_service = MagicMock()
    attachment_service.read_by_id.return_value = _jpeg_attachment()
    storage = MagicMock()

    payload = ReviewNotificationService._build_attachment_payload(
        bill=bill,
        line_items=line_items,
        bla_service=bla_service,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload is None
    storage.download_file.assert_not_called()


def test_bill_first_bla_with_pdf_wins():
    """Walk DB order; skip missing/non-PDF; first PDF wins; later lines unread."""
    bill = SimpleNamespace(public_id="bill-pub")
    line_items = [_line("li-1"), _line("li-2"), _line("li-3"), _line("li-4")]
    pdf_winner = _pdf_attachment(
        filename="first.pdf",
        blob_url="attachments/first.pdf",
        public_id="att-first",
    )
    pdf_later = _pdf_attachment(
        filename="later.pdf",
        blob_url="attachments/later.pdf",
        public_id="att-later",
    )

    def _bla_for(public_id):
        return {
            "li-1": None,
            "li-2": SimpleNamespace(attachment_id=2),
            "li-3": SimpleNamespace(attachment_id=3),
            "li-4": SimpleNamespace(attachment_id=4),
        }[public_id]

    bla_service = MagicMock()
    bla_service.read_by_bill_line_item_id.side_effect = _bla_for
    attachment_service = MagicMock()
    attachment_service.read_by_id.side_effect = lambda aid: {
        2: _jpeg_attachment(),
        3: pdf_winner,
        4: pdf_later,
    }[aid]
    storage = MagicMock()

    payload = ReviewNotificationService._build_attachment_payload(
        bill=bill,
        line_items=line_items,
        bla_service=bla_service,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload["blob_url"] == "attachments/first.pdf"
    assert payload["name"] == "first.pdf"
    assert "content_bytes" not in payload
    storage.download_file.assert_not_called()
    assert attachment_service.read_by_id.call_count == 2  # jpeg + winner; not later
    bla_ids = [c.args[0] for c in bla_service.read_by_bill_line_item_id.call_args_list]
    assert bla_ids == ["li-1", "li-2", "li-3"]


def test_bill_pdf_without_blob_url_is_skipped_and_later_pdf_wins():
    """M15: the blob_url guard is what keeps a None-url reference out of the outbox."""
    bill = SimpleNamespace(public_id="bill-pub")
    line_items = [_line("li-1"), _line("li-2")]
    missing_url = _pdf_attachment(
        filename="no-url.pdf",
        blob_url=None,
        public_id="att-no-url",
    )
    winner = _pdf_attachment(
        filename="has-url.pdf",
        blob_url="attachments/has-url.pdf",
        public_id="att-has-url",
    )

    bla_service = MagicMock()
    bla_service.read_by_bill_line_item_id.side_effect = lambda pid: {
        "li-1": SimpleNamespace(attachment_id=1),
        "li-2": SimpleNamespace(attachment_id=2),
    }[pid]
    attachment_service = MagicMock()
    attachment_service.read_by_id.side_effect = lambda aid: {
        1: missing_url,
        2: winner,
    }[aid]
    storage = MagicMock()

    payload = ReviewNotificationService._build_attachment_payload(
        bill=bill,
        line_items=line_items,
        bla_service=bla_service,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload["blob_url"] == "attachments/has-url.pdf"
    assert payload["name"] == "has-url.pdf"
    storage.download_file.assert_not_called()


# ---------------------------------------------------------------------------
# Enqueue: Expense (same treatment; m5 dies here if Bill-only)
# ---------------------------------------------------------------------------


def test_expense_attachment_payload_is_reference_and_does_not_download():
    expense = SimpleNamespace(public_id="exp-pub")
    line_items = [_line("eli-1")]
    elia_service = MagicMock()
    elia_service.read_by_expense_line_item_id.return_value = SimpleNamespace(
        attachment_id=9
    )
    attachment_service = MagicMock()
    attachment_service.read_by_id.return_value = _pdf_attachment(
        filename="receipt.pdf",
        blob_url="attachments/receipt.pdf",
    )
    storage = MagicMock()

    payload = ReviewNotificationService._build_expense_attachment_payload(
        expense=expense,
        line_items=line_items,
        elia_service=elia_service,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload == {
        "name": "receipt.pdf",
        "content_type": "application/pdf",
        "blob_url": "attachments/receipt.pdf",
    }
    assert "content_bytes" not in payload
    storage.download_file.assert_not_called()


def test_expense_non_pdf_attachment_is_skipped():
    expense = SimpleNamespace(public_id="exp-pub")
    line_items = [_line("eli-1")]
    elia_service = MagicMock()
    elia_service.read_by_expense_line_item_id.return_value = SimpleNamespace(
        attachment_id=9
    )
    attachment_service = MagicMock()
    attachment_service.read_by_id.return_value = _jpeg_attachment()
    storage = MagicMock()

    payload = ReviewNotificationService._build_expense_attachment_payload(
        expense=expense,
        line_items=line_items,
        elia_service=elia_service,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload is None
    storage.download_file.assert_not_called()


def test_expense_first_elia_with_pdf_wins():
    """Walk DB order; skip missing/non-PDF; first PDF wins; later lines unread."""
    expense = SimpleNamespace(public_id="exp-pub")
    line_items = [_line("eli-1"), _line("eli-2"), _line("eli-3"), _line("eli-4")]
    pdf_winner = _pdf_attachment(
        filename="exp-first.pdf",
        blob_url="attachments/exp-first.pdf",
        public_id="att-exp-first",
    )
    pdf_later = _pdf_attachment(
        filename="exp-later.pdf",
        blob_url="attachments/exp-later.pdf",
        public_id="att-exp-later",
    )

    def _elia_for(*, expense_line_item_public_id):
        return {
            "eli-1": None,
            "eli-2": SimpleNamespace(attachment_id=2),
            "eli-3": SimpleNamespace(attachment_id=3),
            "eli-4": SimpleNamespace(attachment_id=4),
        }[expense_line_item_public_id]

    elia_service = MagicMock()
    elia_service.read_by_expense_line_item_id.side_effect = _elia_for
    attachment_service = MagicMock()
    attachment_service.read_by_id.side_effect = lambda aid: {
        2: _jpeg_attachment(),
        3: pdf_winner,
        4: pdf_later,
    }[aid]
    storage = MagicMock()

    payload = ReviewNotificationService._build_expense_attachment_payload(
        expense=expense,
        line_items=line_items,
        elia_service=elia_service,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload["blob_url"] == "attachments/exp-first.pdf"
    assert payload["name"] == "exp-first.pdf"
    assert "content_bytes" not in payload
    storage.download_file.assert_not_called()
    assert attachment_service.read_by_id.call_count == 2  # jpeg + winner; not later
    elia_ids = [
        c.kwargs["expense_line_item_public_id"]
        for c in elia_service.read_by_expense_line_item_id.call_args_list
    ]
    assert elia_ids == ["eli-1", "eli-2", "eli-3"]


def test_expense_pdf_without_blob_url_is_skipped_and_later_pdf_wins():
    expense = SimpleNamespace(public_id="exp-pub")
    line_items = [_line("eli-1"), _line("eli-2")]
    missing_url = _pdf_attachment(
        filename="exp-no-url.pdf",
        blob_url=None,
        public_id="att-exp-no-url",
    )
    winner = _pdf_attachment(
        filename="exp-has-url.pdf",
        blob_url="attachments/exp-has-url.pdf",
        public_id="att-exp-has-url",
    )

    elia_service = MagicMock()
    elia_service.read_by_expense_line_item_id.side_effect = (
        lambda *, expense_line_item_public_id: {
            "eli-1": SimpleNamespace(attachment_id=1),
            "eli-2": SimpleNamespace(attachment_id=2),
        }[expense_line_item_public_id]
    )
    attachment_service = MagicMock()
    attachment_service.read_by_id.side_effect = lambda aid: {
        1: missing_url,
        2: winner,
    }[aid]
    storage = MagicMock()

    payload = ReviewNotificationService._build_expense_attachment_payload(
        expense=expense,
        line_items=line_items,
        elia_service=elia_service,
        attachment_service=attachment_service,
        storage=storage,
    )

    assert payload["blob_url"] == "attachments/exp-has-url.pdf"
    assert payload["name"] == "exp-has-url.pdf"
    storage.download_file.assert_not_called()


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def _mail_row(**overrides):
    defaults = dict(
        id=1,
        public_id="ob-1",
        row_version="rv-1",
        kind="send_mail",
        entity_type="Bill",
        entity_public_id="bill-1",
        tenant_id="tenant-1",
        request_id="req-1",
        payload="{}",
        status="in_progress",
        attempts=0,
        ready_after=None,
        correlation_id=None,
    )
    defaults.update(overrides)
    return MsOutbox(**defaults)


def _send_payload(attachment):
    return {
        "to_addresses": [{"email": "pm@example.com", "name": "Pat"}],
        "cc_addresses": [],
        "bcc_addresses": [],
        "subject": "review",
        "body": "<p>body</p>",
        "body_type": "HTML",
        "attachment": attachment,
        "mode": "draft",
    }


def _ok_draft():
    return {"status_code": 201, "draft": {"message_id": "mid-1"}}


def test_legacy_content_bytes_payload_sends_without_fetch():
    """Legacy embedded-bytes shape sends as-is; do not re-fetch."""
    worker = MsOutboxWorker(repo=MagicMock())
    attachment = {
        "name": "bill.pdf",
        "content_type": "application/pdf",
        "content_bytes": PDF_B64,
    }

    with patch.object(MsOutboxWorker, "_fetch_blob") as fetch, patch(
        _CREATE_DRAFT, return_value=_ok_draft()
    ) as draft:
        worker._handle_send_mail(_mail_row(), _send_payload(attachment))

    fetch.assert_not_called()
    sent = draft.call_args.kwargs["attachments"][0]
    assert sent["content_bytes"] == PDF_B64
    assert sent["name"] == "bill.pdf"


def test_reference_payload_fetches_blob_and_sends_bytes():
    worker = MsOutboxWorker(repo=MagicMock())
    attachment = {
        "name": "bill.pdf",
        "content_type": "application/pdf",
        "blob_url": "attachments/bill.pdf",
    }

    with patch.object(
        MsOutboxWorker, "_fetch_blob", return_value=FETCHED_BYTES
    ) as fetch, patch(_CREATE_DRAFT, return_value=_ok_draft()) as draft:
        worker._handle_send_mail(_mail_row(), _send_payload(attachment))

    fetch.assert_called_once_with("attachments/bill.pdf")
    sent = draft.call_args.kwargs["attachments"][0]
    assert sent["content_bytes"] == FETCHED_B64
    assert sent["name"] == "bill.pdf"
    assert sent["content_type"] == "application/pdf"


def test_success_persists_reference_payload_without_content_bytes():
    """Drain must not write fetched bytes back into ms.Outbox.Payload (M16)."""
    repo = MagicMock()
    repo.update_payload.return_value = SimpleNamespace(row_version="rv-2")
    worker = MsOutboxWorker(repo=repo)
    big = b"%PDF-1.4 " + b"A" * 50_000
    attachment = {
        "name": "bill.pdf",
        "content_type": "application/pdf",
        "blob_url": "attachments/bill.pdf",
    }

    with patch.object(MsOutboxWorker, "_fetch_blob", return_value=big), patch(
        _CREATE_DRAFT, return_value=_ok_draft()
    ):
        worker._handle_send_mail(_mail_row(), _send_payload(attachment))

    repo.update_payload.assert_called_once()
    persisted_json = repo.update_payload.call_args.kwargs["payload"]
    persisted = json.loads(persisted_json)
    att = persisted["attachment"]
    assert "content_bytes" not in att
    assert att["blob_url"] == "attachments/bill.pdf"
    assert persisted["graph_message_id"] == "mid-1"
    assert len(persisted_json) < 4096


def test_legacy_content_bytes_wins_over_blob_url():
    """Key presence: content_bytes in the dict → use as-is even if blob_url is also set."""
    worker = MsOutboxWorker(repo=MagicMock())
    attachment = {
        "name": "bill.pdf",
        "content_type": "application/pdf",
        "content_bytes": PDF_B64,
        "blob_url": "attachments/must-not-fetch.pdf",
    }

    with patch.object(
        MsOutboxWorker, "_fetch_blob", return_value=FETCHED_BYTES
    ) as fetch, patch(_CREATE_DRAFT, return_value=_ok_draft()) as draft:
        worker._handle_send_mail(_mail_row(), _send_payload(attachment))

    fetch.assert_not_called()
    sent = draft.call_args.kwargs["attachments"][0]
    assert sent["content_bytes"] == PDF_B64


def test_empty_content_bytes_is_legacy_and_does_not_fetch():
    """Key presence, not truthiness: b64encode(b'') == '' must still take the legacy path."""
    worker = MsOutboxWorker(repo=MagicMock())
    attachment = {
        "name": "empty.pdf",
        "content_type": "application/pdf",
        "content_bytes": "",
        "blob_url": "attachments/must-not-fetch.pdf",
    }

    with patch.object(
        MsOutboxWorker, "_fetch_blob", return_value=FETCHED_BYTES
    ) as fetch, patch(_CREATE_DRAFT, return_value=_ok_draft()) as draft:
        worker._handle_send_mail(_mail_row(), _send_payload(attachment))

    fetch.assert_not_called()
    sent = draft.call_args.kwargs["attachments"][0]
    assert sent["content_bytes"] == ""


def test_reference_fetch_failure_raises_retryable_and_does_not_send():
    worker = MsOutboxWorker(repo=MagicMock())
    attachment = {
        "name": "bill.pdf",
        "content_type": "application/pdf",
        "blob_url": "attachments/bill.pdf",
    }

    with patch.object(
        MsOutboxWorker, "_fetch_blob", side_effect=RuntimeError("blob timeout")
    ), patch(_CREATE_DRAFT, return_value=_ok_draft()) as draft:
        with pytest.raises(MsServerError) as exc_info:
            worker._handle_send_mail(_mail_row(), _send_payload(attachment))

    assert exc_info.value.is_retryable is True
    assert exc_info.value.http_status == 503
    draft.assert_not_called()


def test_reference_fetch_failure_attempt_1_schedules_retry_not_dead_letter():
    """A first-blip Azure failure must not dead-letter and must not send without the PDF."""
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _mail_row(
        payload=json.dumps(
            _send_payload(
                {
                    "name": "bill.pdf",
                    "content_type": "application/pdf",
                    "blob_url": "attachments/bill.pdf",
                }
            )
        ),
        attempts=0,
    )

    with patch.object(
        MsOutboxWorker, "_fetch_blob", side_effect=RuntimeError("blob timeout")
    ), patch(_CREATE_DRAFT) as draft:
        worker._process_inner(row)

    draft.assert_not_called()
    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
    repo.mark_done.assert_not_called()


def test_reference_fetch_503_schedules_retry_not_dead_letter():
    """Non-404 AzureBlobStorageError keeps the retry path (classifier else-branch)."""
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _mail_row(
        payload=json.dumps(
            _send_payload(
                {
                    "name": "bill.pdf",
                    "content_type": "application/pdf",
                    "blob_url": "attachments/bill.pdf",
                }
            )
        ),
        attempts=0,
    )

    with patch.object(
        MsOutboxWorker,
        "_fetch_blob",
        side_effect=AzureBlobStorageError("Failed to download blob: 503"),
    ), patch(_CREATE_DRAFT) as draft, patch.object(
        MsOutboxWorker, "_escalate_dead_letter"
    ):
        worker._process_inner(row)

    draft.assert_not_called()
    repo.mark_failed.assert_called_once()
    repo.mark_dead_letter.assert_not_called()
    repo.mark_done.assert_not_called()


def test_reference_fetch_404_raises_non_retryable_and_does_not_send():
    worker = MsOutboxWorker(repo=MagicMock())
    attachment = {
        "name": "bill.pdf",
        "content_type": "application/pdf",
        "blob_url": "attachments/bill.pdf",
    }

    with patch.object(
        MsOutboxWorker,
        "_fetch_blob",
        side_effect=AzureBlobStorageError("Failed to download blob: 404"),
    ), patch(_CREATE_DRAFT, return_value=_ok_draft()) as draft:
        with pytest.raises(MsNotFoundError) as exc_info:
            worker._handle_send_mail(_mail_row(), _send_payload(attachment))

    assert exc_info.value.is_retryable is False
    assert exc_info.value.http_status == 404
    draft.assert_not_called()


def test_reference_fetch_404_dead_letters_on_attempt_1():
    """A deleted blob is permanent: dead-letter + escalate on attempt 1, do not retry."""
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _mail_row(
        payload=json.dumps(
            _send_payload(
                {
                    "name": "bill.pdf",
                    "content_type": "application/pdf",
                    "blob_url": "attachments/bill.pdf",
                }
            )
        ),
        attempts=0,
    )

    with patch.object(
        MsOutboxWorker,
        "_fetch_blob",
        side_effect=AzureBlobStorageError("Failed to download blob: 404"),
    ), patch(_CREATE_DRAFT) as draft, patch.object(
        MsOutboxWorker, "_escalate_dead_letter"
    ) as escalate:
        worker._process_inner(row)

    draft.assert_not_called()
    repo.mark_dead_letter.assert_called_once()
    last_error = repo.mark_dead_letter.call_args.kwargs["last_error"]
    assert "MsNotFoundError" in last_error
    repo.mark_failed.assert_not_called()
    repo.mark_done.assert_not_called()
    escalate.assert_called_once()


def test_attachment_missing_content_bytes_and_blob_url_dead_letters_on_attempt_1():
    """ValueError from the resolver is unexpected → dead-letter on attempt 1, no fetch."""
    repo = MagicMock()
    worker = MsOutboxWorker(repo=repo)
    row = _mail_row(
        payload=json.dumps(
            _send_payload(
                {
                    "name": "bill.pdf",
                    "content_type": "application/pdf",
                }
            )
        ),
        attempts=0,
    )

    with patch.object(MsOutboxWorker, "_fetch_blob") as fetch, patch(
        _CREATE_DRAFT
    ) as draft, patch.object(MsOutboxWorker, "_escalate_dead_letter"):
        worker._process_inner(row)

    fetch.assert_not_called()
    draft.assert_not_called()
    repo.mark_dead_letter.assert_called_once()
    last_error = repo.mark_dead_letter.call_args.kwargs["last_error"]
    assert "ValueError" in last_error
    repo.mark_failed.assert_not_called()
    repo.mark_done.assert_not_called()
