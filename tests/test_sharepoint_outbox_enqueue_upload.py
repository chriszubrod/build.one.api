"""
SharePoint upload paths enqueue via MsOutbox (not inline Graph upload).

Covers bill_credit._upload_attachments_to_module_folder and
invoice._upload_to_sharepoint: outbox enqueue replaces synchronous blob
download + driveitem_service.upload_file, and skipped_count vs synced_count
discriminates U-221 idempotency-guard skips from genuinely new enqueues.
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

    enqueue_mock = MagicMock(return_value=enqueue_return)
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


def test_bill_credit_shared_attachment_dedup_credits_skipped_count(bill_credit_complete_service):
    """Two line items sharing one attachment: dedup branch must mirror first occurrence's skip."""
    result, enqueue_mock = _stub_bill_credit_upload_deps(
        bill_credit_complete_service,
        enqueue_return=_outbox_row(status="done"),
        line_items=[
            _bill_credit_line_item(public_id="bcli-1"),
            _bill_credit_line_item(public_id="bcli-2"),
        ],
    )

    assert result["skipped_count"] == 2
    assert result["synced_count"] == 0
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


def test_invoice_shared_attachment_dedup_credits_skipped_count():
    """Two line items sharing one attachment: dedup branch must mirror first occurrence's skip."""
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

    assert result["skipped_count"] == 2
    assert result["synced_count"] == 0
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
