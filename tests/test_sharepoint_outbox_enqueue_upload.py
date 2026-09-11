"""
SharePoint upload paths enqueue via MsOutbox (not inline Graph upload).

Covers bill_credit._upload_attachments_to_module_folder and
invoice._upload_to_sharepoint: outbox enqueue replaces synchronous blob
download + driveitem_service.upload_file, and skipped_count vs synced_count
discriminates U-221 idempotency-guard skips from genuinely new enqueues.

Both counters count FILES, never line items: an attachment shared by several
line items is one upload and must count exactly once.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill_credit.business.complete_service import BillCreditCompleteService
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit_line_item.business.model import BillCreditLineItem
from entities.invoice.business.model import Invoice
from entities.invoice.business.service import InvoiceService
from integrations.ms.outbox.business.model import MsOutbox

_BC_MODULE = "entities.bill_credit.business.complete_service"
_INV_MODULE = "entities.invoice.business.service"

_BC_MS_STUBS = {
    "DriveItemProjectExcelConnector": MagicMock,
    "DriveItemProjectModuleConnector": MagicMock,
    "MsDriveItemService": MagicMock,
    "MsDriveRepository": MagicMock,
}


def _outbox_row(*, status="pending", row_id=1):
    return MsOutbox(
        id=row_id,
        public_id=f"outbox-{row_id}",
        row_version=f"rv-{row_id}",
        kind="upload_sharepoint_file",
        entity_type="Bill",
        entity_public_id="entity-1",
        tenant_id="tenant-1",
        request_id="req-1",
        payload="{}",
        status=status,
        attempts=0,
        ready_after=None,
        correlation_id=None,
    )


def _stub_bill_credit_upload_deps(service, *, enqueue_return, line_items=None):
    """Wire minimal mocks so _upload_attachments_to_module_folder reaches enqueue."""
    module = SimpleNamespace(id=1, name="Bill Credits")
    service.module_service.read_by_name = MagicMock(return_value=module)
    service.project_module_connector.get_folder_for_module = MagicMock(
        return_value={"ms_drive_id": 10, "item_id": "folder-item-1", "name": "Credits"}
    )
    service.drive_repo.read_by_id = MagicMock(
        return_value=SimpleNamespace(drive_id="drive-graph-id", public_id="drive-pub")
    )
    service.vendor_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=1, name="Vendor", abbreviation="VND")
    )
    service.project_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=1, name="Project", abbreviation="PRJ")
    )

    default_line_item = BillCreditLineItem(
        id=100,
        public_id="bcli-pub",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        bill_credit_id=1,
        sub_cost_code_id=None,
        project_id=1,
        description="Desc",
        quantity=None,
        unit_price=None,
        amount=Decimal("100.00"),
        is_billable=True,
        is_billed=False,
        billable_amount=None,
        is_draft=False,
    )
    attachment_link = SimpleNamespace(attachment_id=42)
    attachment = SimpleNamespace(
        id=42,
        blob_url="https://blob.example/att.pdf",
        content_type="application/pdf",
        file_extension="pdf",
        original_filename="att.pdf",
    )
    service.bill_credit_line_item_attachment_service.read_by_bill_credit_line_item_id = MagicMock(
        return_value=attachment_link
    )
    service.attachment_service.read_by_id = MagicMock(return_value=attachment)

    bill_credit = BillCredit(
        id=1,
        public_id="bc-pub",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        vendor_id=1,
        credit_date="2026-08-02",
        credit_number="VC-1",
        total_amount=Decimal("100.00"),
        memo=None,
        is_draft=False,
    )

    # U-437: an Exception instance means "raise it" (genuine failure); anything
    # else is a return value (a row = queued, None = policy refusal).
    enqueue_mock = (
        MagicMock(side_effect=enqueue_return)
        if isinstance(enqueue_return, Exception)
        else MagicMock(return_value=enqueue_return)
    )
    with patch.multiple(_BC_MODULE, **_BC_MS_STUBS), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue_mock
        result = service._upload_attachments_to_module_folder(
            bill_credit=bill_credit,
            line_items=line_items if line_items is not None else [default_line_item],
            project_id=1,
        )
    return result, enqueue_mock


@pytest.fixture
def bill_credit_complete_service():
    with patch.multiple(_BC_MODULE, **_BC_MS_STUBS):
        yield BillCreditCompleteService()


def test_bill_credit_enqueue_pending_increments_synced_count(bill_credit_complete_service):
    result, enqueue_mock = _stub_bill_credit_upload_deps(
        bill_credit_complete_service, enqueue_return=_outbox_row(status="pending")
    )

    assert result["synced_count"] == 1
    assert result["skipped_count"] == 0
    assert "Queued 1 file(s) for SharePoint upload" in result["message"]
    enqueue_mock.assert_called_once()
    bill_credit_complete_service.driveitem_service.upload_file.assert_not_called()


def test_bill_credit_enqueue_done_increments_skipped_count(bill_credit_complete_service):
    result, enqueue_mock = _stub_bill_credit_upload_deps(
        bill_credit_complete_service, enqueue_return=_outbox_row(status="done")
    )

    assert result["synced_count"] == 0
    assert result["skipped_count"] == 1
    assert "already uploaded (skipped)" in result["message"]
    enqueue_mock.assert_called_once()


def test_bill_credit_enqueue_refused_increments_neither_counter(bill_credit_complete_service):
    result, enqueue_mock = _stub_bill_credit_upload_deps(
        bill_credit_complete_service, enqueue_return=None
    )

    assert result["synced_count"] == 0
    assert result["skipped_count"] == 0
    assert len(result["errors"]) == 1
    assert "enqueue refused" in result["errors"][0]["error"]
    enqueue_mock.assert_called_once()


def _bill_credit_line_item(*, public_id):
    return BillCreditLineItem(
        id=100 if public_id == "bcli-1" else 101,
        public_id=public_id,
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        bill_credit_id=1,
        sub_cost_code_id=None,
        project_id=1,
        description="Desc",
        quantity=None,
        unit_price=None,
        amount=Decimal("100.00"),
        is_billable=True,
        is_billed=False,
        billable_amount=None,
        is_draft=False,
    )


def test_bill_credit_shared_attachment_counts_one_file(bill_credit_complete_service):
    """Two line items, one shared attachment, one outbox row -> counts total 1."""
    result, enqueue_mock = _stub_bill_credit_upload_deps(
        bill_credit_complete_service,
        enqueue_return=_outbox_row(status="pending"),
        line_items=[
            _bill_credit_line_item(public_id="bcli-1"),
            _bill_credit_line_item(public_id="bcli-2"),
        ],
    )

    assert result["synced_count"] == 1
    assert result["skipped_count"] == 0
    assert "Queued 1 file(s) for SharePoint upload" in result["message"]
    enqueue_mock.assert_called_once()


def test_bill_credit_shared_attachment_skip_counts_one_file(bill_credit_complete_service):
    """Same, when the outbox reports the file already uploaded."""
    result, enqueue_mock = _stub_bill_credit_upload_deps(
        bill_credit_complete_service,
        enqueue_return=_outbox_row(status="done"),
        line_items=[
            _bill_credit_line_item(public_id="bcli-1"),
            _bill_credit_line_item(public_id="bcli-2"),
        ],
    )

    assert result["synced_count"] == 0
    assert result["skipped_count"] == 1
    assert "1 already uploaded (skipped)" in result["message"]
    enqueue_mock.assert_called_once()


def _stub_invoice_upload_deps(service, *, enqueue_side_effect, include_packet=False, line_attachment_rows=None):
    invoice = Invoice(
        id=1,
        public_id="inv-pub",
        row_version="rv",
        created_datetime=None,
        modified_datetime=None,
        project_id=10,
        payment_term_id=None,
        invoice_date="2026-08-02",
        due_date="2026-08-02",
        invoice_number="INV-1",
        total_amount=Decimal("100.00"),
        memo=None,
        is_draft=False,
    )
    module = SimpleNamespace(id=5, name="Invoices")
    service.module_service.read_by_name = MagicMock(return_value=module)
    service.project_module_connector.get_folder_for_module = MagicMock(
        return_value={"ms_drive_id": 10, "item_id": "parent-folder"}
    )
    service.drive_repo.read_by_id = MagicMock(
        return_value=SimpleNamespace(drive_id="drive-graph-id", public_id="drive-pub")
    )
    driveitem = MagicMock()
    driveitem.read_or_create_folder = MagicMock(
        return_value={"status_code": 200, "item": {"item_id": "invoice-subfolder"}}
    )
    driveitem.upload_file = MagicMock()
    service._driveitem_service = driveitem
    service._collect_line_attachment_rows = MagicMock(
        return_value=line_attachment_rows
        if line_attachment_rows is not None
        else [
            {
                "attachment_id": 99,
                "blob_url": "https://blob.example/line.pdf",
                "content_type": "application/pdf",
                "file_extension": "pdf",
                "original_filename": "line.pdf",
                "vendor_name": "Vendor",
                "parent_number": "B-1",
                "description": "Line",
                "scc_number": "100",
                "price": Decimal("50.00"),
                "source_date": "2026-08-01",
            }
        ]
    )
    if include_packet:
        service.invoice_attachment_service.read_by_invoice_id = MagicMock(
            return_value=[SimpleNamespace(attachment_id=200)]
        )
        packet_attachment = SimpleNamespace(
            id=200,
            category="invoice_packet",
            blob_url="https://blob.example/packet.pdf",
        )
        attachment_service_cls = MagicMock()
        attachment_service_cls.return_value.read_by_id = MagicMock(return_value=packet_attachment)
    else:
        service.invoice_attachment_service.read_by_invoice_id = MagicMock(return_value=[])
        attachment_service_cls = MagicMock()

    enqueue_mock = MagicMock(side_effect=enqueue_side_effect)
    with patch("integrations.ms.outbox.business.service.MsOutboxService") as ms_outbox_cls, patch(
        "entities.attachment.business.service.AttachmentService", attachment_service_cls
    ):
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue_mock
        result = service._upload_to_sharepoint(invoice=invoice, line_items=[])
    return result, enqueue_mock, driveitem


def test_invoice_enqueue_pending_increments_synced_count():
    service = InvoiceService()
    result, enqueue_mock, driveitem = _stub_invoice_upload_deps(
        service, enqueue_side_effect=[_outbox_row(status="pending")]
    )

    assert result["synced_count"] == 1
    assert result["skipped_count"] == 0
    assert "Queued 1 file(s) for SharePoint upload" in result["message"]
    enqueue_mock.assert_called_once()
    driveitem.upload_file.assert_not_called()


def test_invoice_enqueue_done_increments_skipped_count():
    service = InvoiceService()
    result, enqueue_mock, _driveitem = _stub_invoice_upload_deps(
        service, enqueue_side_effect=[_outbox_row(status="done")]
    )

    assert result["synced_count"] == 0
    assert result["skipped_count"] == 1
    assert "already uploaded (skipped)" in result["message"]
    enqueue_mock.assert_called_once()


def test_invoice_enqueue_refused_increments_neither_counter():
    service = InvoiceService()
    result, enqueue_mock, _driveitem = _stub_invoice_upload_deps(
        service, enqueue_side_effect=[None]
    )

    assert result["synced_count"] == 0
    assert result["skipped_count"] == 0
    assert len(result["errors"]) == 1
    assert result["errors"][0]["attachment_id"] == 99
    enqueue_mock.assert_called_once()


def test_invoice_no_download_file_on_upload_path():
    """Regression: inline blob download must not run on the SharePoint upload path."""
    service = InvoiceService()
    _, enqueue_mock, driveitem = _stub_invoice_upload_deps(
        service, enqueue_side_effect=[_outbox_row(status="pending")]
    )

    enqueue_mock.assert_called_once()
    driveitem.upload_file.assert_not_called()


def test_invoice_shared_attachment_counts_one_file():
    """Two lines sharing one attachment, one outbox row -> counts total 1."""
    service = InvoiceService()
    shared_rows = [
        {
            "attachment_id": 99,
            "blob_url": "https://blob.example/line.pdf",
            "content_type": "application/pdf",
            "file_extension": "pdf",
            "original_filename": "line.pdf",
            "vendor_name": "Vendor",
            "parent_number": "B-1",
            "description": "Line A",
            "scc_number": "100",
            "price": Decimal("50.00"),
            "source_date": "2026-08-01",
        },
        {
            "attachment_id": 99,
            "blob_url": "https://blob.example/line.pdf",
            "content_type": "application/pdf",
            "file_extension": "pdf",
            "original_filename": "line.pdf",
            "vendor_name": "Vendor",
            "parent_number": "B-2",
            "description": "Line B",
            "scc_number": "200",
            "price": Decimal("75.00"),
            "source_date": "2026-08-02",
        },
    ]
    result, enqueue_mock, _driveitem = _stub_invoice_upload_deps(
        service,
        enqueue_side_effect=[_outbox_row(status="done")],
        line_attachment_rows=shared_rows,
    )

    assert result["synced_count"] == 0
    assert result["skipped_count"] == 1
    assert "1 already uploaded (skipped)" in result["message"]
    enqueue_mock.assert_called_once()


def test_invoice_packet_enqueue_pending_increments_synced_count():
    service = InvoiceService()
    result, enqueue_mock, driveitem = _stub_invoice_upload_deps(
        service,
        enqueue_side_effect=[_outbox_row(status="pending")],
        include_packet=True,
        line_attachment_rows=[],
    )

    assert result["synced_count"] == 1
    assert result["skipped_count"] == 0
    enqueue_mock.assert_called_once()
    driveitem.upload_file.assert_not_called()


def test_invoice_packet_enqueue_done_increments_skipped_count():
    service = InvoiceService()
    result, enqueue_mock, _driveitem = _stub_invoice_upload_deps(
        service,
        enqueue_side_effect=[_outbox_row(status="done")],
        include_packet=True,
        line_attachment_rows=[],
    )

    assert result["synced_count"] == 0
    assert result["skipped_count"] == 1
    assert "already uploaded (skipped)" in result["message"]
    enqueue_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Bill + Expense module-folder uploads: counts are per FILE, not per line item.
#
# Regression for the live 2026-09-08 observation on bill #26-0183 (two line
# items sharing attachment 29670): the dedup branch credited synced_count on
# the path that enqueues nothing, so completion reported "Queued 2 file(s)"
# against a single outbox row.
# ---------------------------------------------------------------------------

_BILL_MODULE = "entities.bill.business.service"
_EXPENSE_MODULE = "entities.expense.business.service"

def _stub_lazy_ms_collaborators(service, *, module_folder, drive):
    """BillService / ExpenseService build their MS collaborators through lazy
    properties, so patching the module namespace around __init__ never fires.
    Seed the backing attributes so no real connector is ever constructed."""
    connector = MagicMock()
    connector.get_folder_for_module = MagicMock(return_value=module_folder)
    service._project_module_connector = connector
    service._project_excel_connector = MagicMock()
    service._driveitem_service = MagicMock()
    drive_repo = MagicMock()
    drive_repo.read_by_id = MagicMock(return_value=drive)
    service._drive_repo = drive_repo
    return connector, drive_repo


def _shared_attachment_link():
    """Every line item resolves to the SAME attachment — the defect's shape."""
    return SimpleNamespace(attachment_id=29670)


def _storage_stub():
    """AzureBlobStorage double. The expense paths probe the blob (fail-fast)
    before enqueueing and unpack a (bytes, metadata) tuple; bill does not."""
    storage_cls = MagicMock()
    storage_cls.return_value.download_file.return_value = (b"pdf-bytes", {})
    return storage_cls


def _attachment():
    return SimpleNamespace(
        id=29670,
        blob_url="https://blob.example/shared.pdf",
        content_type="application/pdf",
        file_extension="pdf",
        original_filename="shared.pdf",
    )


def _line_item(prefix, line_id):
    """Minimal line item: the upload loops only read these six attributes."""
    return SimpleNamespace(
        id=line_id,
        public_id=f"{prefix}-{line_id}",
        project_id=1,
        sub_cost_code_id=None,
        description="Line",
        price=Decimal("50.00"),
    )


def _run_bill_module_folder_upload(*, enqueue_return, line_items):
    from entities.bill.business.service import BillService

    service = BillService(repo=MagicMock())
    _stub_lazy_ms_collaborators(
        service,
        module_folder={"ms_drive_id": 10, "item_id": "folder-item-1"},
        drive=SimpleNamespace(drive_id="drive-graph-id", public_id="drive-pub"),
    )
    service.module_service.read_by_name = MagicMock(return_value=SimpleNamespace(id=1, name="Bills"))
    service.vendor_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=1, name="Siteworks", abbreviation="SW")
    )
    service.project_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=1, name="Project", abbreviation="PRJ")
    )
    service.bill_line_item_attachment_service.read_by_bill_line_item_id = MagicMock(
        return_value=_shared_attachment_link()
    )
    service.attachment_service.read_by_id = MagicMock(return_value=_attachment())

    bill = SimpleNamespace(
        id=20370,
        public_id="bill-pub",
        bill_number="26-0183",
        bill_date="2026-09-01",
        total_amount=Decimal("100.00"),
        vendor_id=1,
    )

    # U-437: an Exception instance means "raise it" (genuine failure); anything
    # else is a return value (a row = queued, None = policy refusal).
    enqueue_mock = (
        MagicMock(side_effect=enqueue_return)
        if isinstance(enqueue_return, Exception)
        else MagicMock(return_value=enqueue_return)
    )
    with patch(f"{_BILL_MODULE}.AzureBlobStorage", _storage_stub()), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue_mock
        result = service._upload_attachments_to_module_folder(
            bill=bill,
            line_items=line_items,
            project_id=1,
            bill_line_items_count=len(line_items),
        )
    return result, enqueue_mock


def test_bill_shared_attachment_counts_one_file():
    """Two line items, one shared attachment, one outbox row -> 'Queued 1 file(s)'."""
    result, enqueue_mock = _run_bill_module_folder_upload(
        enqueue_return=_outbox_row(status="pending"),
        line_items=[_line_item("bli", 1), _line_item("bli", 2)],
    )

    enqueue_mock.assert_called_once()
    assert result["synced_count"] == 1
    assert result["skipped_count"] == 0
    assert "Queued 1 file(s) for SharePoint upload" in result["message"]


def test_bill_single_line_still_counts_one_file():
    result, enqueue_mock = _run_bill_module_folder_upload(
        enqueue_return=_outbox_row(status="pending"),
        line_items=[_line_item("bli", 1)],
    )

    enqueue_mock.assert_called_once()
    assert result["synced_count"] == 1
    assert "Queued 1 file(s) for SharePoint upload" in result["message"]


def test_bill_shared_attachment_guard_skip_counts_one_file():
    """Outbox reports the file already uploaded: one skip, not one per line."""
    result, enqueue_mock = _run_bill_module_folder_upload(
        enqueue_return=_outbox_row(status="done"),
        line_items=[_line_item("bli", 1), _line_item("bli", 2)],
    )

    enqueue_mock.assert_called_once()
    assert result["synced_count"] == 0
    assert result["skipped_count"] == 1
    assert "Queued 0 file(s) for SharePoint upload, 1 already uploaded (skipped)" in result["message"]
    assert result["success"] is True


def test_bill_enqueue_refused_counts_neither():
    """U-437 CHANGED THIS DELIBERATELY: a policy refusal is no longer an error.

    `enqueue_return=None` means one thing under U-437's contract — the
    ALLOW_MS_WRITES gate is off. That is an expected state in a deliberately
    gated environment, not a failure, and recording it as an error is what let
    "gated" and "genuinely failed" be indistinguishable. A genuine failure now
    RAISES MsOutboxEnqueueError (covered by the sibling test below).

    Everything else this test pinned is unchanged and still asserted: refusal
    does not memoize the attachment, so the second line retries, and neither
    counter moves.
    """
    result, enqueue_mock = _run_bill_module_folder_upload(
        enqueue_return=None,
        line_items=[_line_item("bli", 1), _line_item("bli", 2)],
    )

    # Refusal does not memoize the attachment, so the second line retries.
    assert enqueue_mock.call_count == 2
    assert result["synced_count"] == 0
    assert result["skipped_count"] == 0
    assert len(result["errors"]) == 0, "a policy refusal must not be recorded as an error"
    assert result["enqueue_failed"] is False, "a policy refusal must not fail the completion job"


def test_bill_genuine_enqueue_failure_flags_the_job():
    """The other half: a real failure must gate the completion job.

    MsOutboxEnqueueError means the durable handoff never queued and nothing will
    retry it — the opposite of a policy refusal, and the case U-434's marking
    silently retired.
    """
    from integrations.ms.outbox.business.service import MsOutboxEnqueueError

    result, enqueue_mock = _run_bill_module_folder_upload(
        enqueue_return=MsOutboxEnqueueError("no tenant_id in context"),
        line_items=[_line_item("bli", 1)],
    )

    assert result["enqueue_failed"] is True
    assert len(result["errors"]) == 1


def _run_expense_module_folder_upload(*, enqueue_return, line_items):
    from entities.expense.business.service import ExpenseService

    service = ExpenseService()
    _stub_lazy_ms_collaborators(
        service,
        module_folder={"ms_drive_id": 10, "item_id": "folder-item-1"},
        drive=SimpleNamespace(drive_id="drive-graph-id", public_id="drive-pub"),
    )
    service.module_service.read_by_name = MagicMock(return_value=SimpleNamespace(id=2, name="Expenses"))
    service.vendor_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=1, name="Vendor", abbreviation="VND")
    )
    service.project_service.read_by_id = MagicMock(
        return_value=SimpleNamespace(id=1, name="Project", abbreviation="PRJ")
    )
    service.expense_line_item_attachment_service.read_by_expense_line_item_id = MagicMock(
        return_value=_shared_attachment_link()
    )
    service.attachment_service.read_by_id = MagicMock(return_value=_attachment())

    expense = SimpleNamespace(
        id=1,
        public_id="exp-pub",
        reference_number="EXP-1",
        expense_date="2026-09-01",
        total_amount=Decimal("100.00"),
        vendor_id=1,
        is_credit=False,
    )

    # U-437: an Exception instance means "raise it" (genuine failure); anything
    # else is a return value (a row = queued, None = policy refusal).
    enqueue_mock = (
        MagicMock(side_effect=enqueue_return)
        if isinstance(enqueue_return, Exception)
        else MagicMock(return_value=enqueue_return)
    )
    with patch(f"{_EXPENSE_MODULE}.AzureBlobStorage", _storage_stub()), patch(
        "integrations.ms.outbox.business.service.MsOutboxService"
    ) as ms_outbox_cls:
        ms_outbox_cls.return_value.enqueue_sharepoint_upload = enqueue_mock
        result = service._upload_attachments_to_module_folder(
            expense=expense,
            line_items=line_items,
            project_id=1,
            expense_line_items_count=len(line_items),
        )
    return result, enqueue_mock


def test_expense_shared_attachment_counts_one_file():
    result, enqueue_mock = _run_expense_module_folder_upload(
        enqueue_return=_outbox_row(status="pending"),
        line_items=[_line_item("eli", 1), _line_item("eli", 2)],
    )

    enqueue_mock.assert_called_once()
    assert result["synced_count"] == 1
    assert result["skipped_count"] == 0
    assert "Queued 1 file(s) for SharePoint upload" in result["message"]


def test_expense_shared_attachment_guard_skip_counts_one_file():
    result, enqueue_mock = _run_expense_module_folder_upload(
        enqueue_return=_outbox_row(status="done"),
        line_items=[_line_item("eli", 1), _line_item("eli", 2)],
    )

    enqueue_mock.assert_called_once()
    assert result["synced_count"] == 0
    assert result["skipped_count"] == 1
    assert "1 already uploaded (skipped)" in result["message"]


# ---------------------------------------------------------------------------
# U-437 — the enqueue signalling contract itself
# ---------------------------------------------------------------------------
# Added because mutation testing caught the gap: reverting the wrappers to
# `return None` on no_tenant_id left the suite green, since the failure test
# injects the exception via side_effect and never exercises the wrapper's own
# path. Without this, the disambiguation U-437 depends on could silently rot.


def test_typed_wrappers_RAISE_on_missing_tenant_rather_than_returning_none():
    """None is reserved for the ALLOW_MS_WRITES policy refusal.

    A missing tenant is a genuine failure — nothing queues and nothing retries —
    so it must be distinguishable. Returning None for both is what let "gated"
    and "genuinely failed" wear the same clothes, which is the conflation that
    made U-434's marking unable to tell them apart.
    """
    from unittest.mock import patch as _patch
    from integrations.ms.outbox.business.service import (
        MsOutboxEnqueueError,
        MsOutboxService,
    )

    svc = MsOutboxService()
    with _patch(
        "integrations.ms.outbox.business.service._writes_allowed", return_value=True
    ), _patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id", return_value=None
    ):
        for call in (
            lambda: svc.enqueue_sharepoint_upload(
                entity_type="Bill", entity_public_id="p", drive_id="d",
                parent_item_id="i", filename="f.pdf", content_type="application/pdf",
                blob_path="b", attachment_id=1,
            ),
            lambda: svc.enqueue_excel_insert(
                entity_type="Bill", entity_public_id="p", drive_id="d", item_id="i",
                worksheet_name="w", row_index=1, values=[[]],
            ),
            lambda: svc.enqueue_excel_append(
                entity_type="Bill", entity_public_id="p", drive_id="d", item_id="i",
                worksheet_name="w", values=[[]],
            ),
        ):
            try:
                call()
            except MsOutboxEnqueueError:
                continue
            raise AssertionError(
                "a missing tenant returned instead of raising — 'gated' and "
                "'genuinely failed' are indistinguishable again"
            )


def test_the_policy_gate_still_returns_none_not_an_exception():
    """The other half of the contract: a refusal must stay a quiet None."""
    from unittest.mock import patch as _patch
    from integrations.ms.outbox.business.service import MsOutboxService

    svc = MsOutboxService()
    with _patch(
        "integrations.ms.outbox.business.service._writes_allowed", return_value=False
    ), _patch(
        "integrations.ms.outbox.business.service._resolve_tenant_id", return_value="t-1"
    ):
        assert svc.enqueue(
            kind="insert_excel_row", entity_type="Bill",
            entity_public_id="p", tenant_id="t-1", payload={},
        ) is None
