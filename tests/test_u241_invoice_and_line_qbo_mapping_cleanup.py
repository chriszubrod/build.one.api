"""Behavioral tests for U-241 QBO mapping cleanup on Invoice header + line-item deletes."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from conftest import mock_qbo_app_lock_granted
from entities.bill_line_item.business.service import BillLineItemService
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.invoice.business.service import InvoiceService
from entities.invoice_line_item.business.service import InvoiceLineItemService


@pytest.fixture(autouse=True)
def _mock_qbo_app_lock():
    """Mocks mapping_cleanup's real sp_getapplock lock so these delete tests never
    open a live pyodbc connection (U-295)."""
    with patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", mock_qbo_app_lock_granted):
        yield


# --- Invoice header ---


def test_invoice_delete_clears_qbo_mapping_before_header():
    invoice = SimpleNamespace(id=11, public_id="inv-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *_: call_order.append("header") or invoice

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=77, invoice_id=11, qbo_invoice_id=500)
    mock_mapping_repo.read_by_invoice_id.return_value = fake_mapping
    mock_mapping_repo.delete_by_id.side_effect = lambda *_: call_order.append("mapping")

    svc = InvoiceService(repo=mock_repo)
    svc.invoice_line_item_service.read_by_invoice_id = Mock(return_value=[])
    svc.invoice_attachment_service.read_by_invoice_id = Mock(return_value=[])

    with patch.object(svc, "read_by_public_id", return_value=invoice), patch(
        "integrations.intuit.qbo.invoice.connector.invoice.persistence.repo.InvoiceInvoiceRepository",
        return_value=mock_mapping_repo,
    ):
        result = svc.delete_by_public_id("inv-pub")

    assert call_order == ["mapping", "header"]
    assert result is invoice
    mock_mapping_repo.read_by_invoice_id.assert_called_once_with(11)
    mock_mapping_repo.delete_by_id.assert_called_once_with(77)
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(11)


def test_invoice_delete_header_failure_restores_qbo_mapping():
    invoice = SimpleNamespace(id=11, public_id="inv-pub")
    header_exc = RuntimeError("FK 547 on Invoice delete")

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = header_exc

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=77, invoice_id=11, qbo_invoice_id=500)
    mock_mapping_repo.read_by_invoice_id.return_value = fake_mapping

    svc = InvoiceService(repo=mock_repo)
    svc.invoice_line_item_service.read_by_invoice_id = Mock(return_value=[])
    svc.invoice_attachment_service.read_by_invoice_id = Mock(return_value=[])

    with patch.object(svc, "read_by_public_id", return_value=invoice), patch(
        "integrations.intuit.qbo.invoice.connector.invoice.persistence.repo.InvoiceInvoiceRepository",
        return_value=mock_mapping_repo,
    ):
        with pytest.raises(RuntimeError, match="FK 547 on Invoice delete") as exc_info:
            svc.delete_by_public_id("inv-pub")

    assert exc_info.value is header_exc
    mock_mapping_repo.delete_by_id.assert_called_once_with(77)
    mock_mapping_repo.create.assert_called_once_with(invoice_id=11, qbo_invoice_id=500)
    mock_repo.delete_by_id.assert_called_once_with(11)


def test_invoice_delete_no_mapping_skips_mapping_repo_mutations():
    invoice = SimpleNamespace(id=11, public_id="inv-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = invoice

    mock_mapping_repo = Mock()
    mock_mapping_repo.read_by_invoice_id.return_value = None

    svc = InvoiceService(repo=mock_repo)
    svc.invoice_line_item_service.read_by_invoice_id = Mock(return_value=[])
    svc.invoice_attachment_service.read_by_invoice_id = Mock(return_value=[])

    with patch.object(svc, "read_by_public_id", return_value=invoice), patch(
        "integrations.intuit.qbo.invoice.connector.invoice.persistence.repo.InvoiceInvoiceRepository",
        return_value=mock_mapping_repo,
    ):
        result = svc.delete_by_public_id("inv-pub")

    assert result is invoice
    mock_mapping_repo.delete_by_id.assert_not_called()
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(11)


def test_invoice_delete_header_and_mapping_restore_failure_records_reconciliation_issue():
    invoice = SimpleNamespace(id=11, public_id="inv-pub")
    header_exc = RuntimeError("FK 547 on Invoice delete")
    restore_exc = RuntimeError("mapping recreate failed")

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = header_exc

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=77, invoice_id=11, qbo_invoice_id=500)
    mock_mapping_repo.read_by_invoice_id.return_value = fake_mapping
    mock_mapping_repo.create.side_effect = restore_exc

    mock_staging = SimpleNamespace(realm_id="realm-1", qbo_id="qbo-inv-11")
    mock_staging_repo = Mock()
    mock_staging_repo.read_by_id.return_value = mock_staging

    svc = InvoiceService(repo=mock_repo)
    svc.invoice_line_item_service.read_by_invoice_id = Mock(return_value=[])
    svc.invoice_attachment_service.read_by_invoice_id = Mock(return_value=[])

    with patch.object(svc, "read_by_public_id", return_value=invoice), patch(
        "integrations.intuit.qbo.invoice.connector.invoice.persistence.repo.InvoiceInvoiceRepository",
        return_value=mock_mapping_repo,
    ), patch(
        "integrations.intuit.qbo.invoice.persistence.repo.QboInvoiceRepository",
        return_value=mock_staging_repo,
    ), patch(
        "integrations.intuit.qbo.base.delete_reconcile.record_partial_delete_issue"
    ) as record_issue:
        with pytest.raises(RuntimeError, match="FK 547 on Invoice delete") as exc_info:
            svc.delete_by_public_id("inv-pub")

    assert exc_info.value is header_exc
    mock_staging_repo.read_by_id.assert_called_once_with(500)
    record_issue.assert_called_once()
    assert record_issue.call_args.kwargs["entity_type"] == "Invoice"
    assert record_issue.call_args.kwargs["mapping_label"] == "InvoiceInvoice"
    assert record_issue.call_args.kwargs["mapped_label"] == "Invoice"
    assert record_issue.call_args.kwargs["realm_id"] == "realm-1"
    assert record_issue.call_args.kwargs["qbo_id"] == "qbo-inv-11"
    assert record_issue.call_args.kwargs["local_id"] == 11
    assert record_issue.call_args.kwargs["error"] is restore_exc


# --- BillLineItem ---


def test_bill_line_item_delete_clears_qbo_mapping_before_line():
    line = SimpleNamespace(id=21, public_id="bli-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *_: call_order.append("line") or line

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=88, bill_line_item_id=21, qbo_bill_line_id=600)
    mock_mapping_repo.read_by_bill_line_item_id.return_value = fake_mapping
    mock_mapping_repo.delete_by_id.side_effect = lambda *_: call_order.append("mapping")

    svc = BillLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository"
    ) as ili_repo_cls, patch(
        "entities.contract_labor.persistence.repo.ContractLaborRepository"
    ) as cl_repo_cls, patch(
        "integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo.BillLineItemBillLineRepository",
        return_value=mock_mapping_repo,
    ):
        ili_repo_cls.return_value.delete_by_bill_line_item_id = Mock()
        cl_repo_cls.return_value.read_by_bill_line_item_id.return_value = []
        result = svc.delete_by_public_id("bli-pub")

    assert call_order == ["mapping", "line"]
    assert result is line
    mock_mapping_repo.read_by_bill_line_item_id.assert_called_once_with(21)
    mock_mapping_repo.delete_by_id.assert_called_once_with(88)
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(21)


def test_bill_line_item_delete_line_failure_restores_qbo_mapping():
    line = SimpleNamespace(id=21, public_id="bli-pub")
    line_exc = RuntimeError("FK 547 on BillLineItem delete")

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = line_exc

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=88, bill_line_item_id=21, qbo_bill_line_id=600)
    mock_mapping_repo.read_by_bill_line_item_id.return_value = fake_mapping

    svc = BillLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository"
    ) as ili_repo_cls, patch(
        "entities.contract_labor.persistence.repo.ContractLaborRepository"
    ) as cl_repo_cls, patch(
        "integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo.BillLineItemBillLineRepository",
        return_value=mock_mapping_repo,
    ):
        ili_repo_cls.return_value.delete_by_bill_line_item_id = Mock()
        cl_repo_cls.return_value.read_by_bill_line_item_id.return_value = []
        with pytest.raises(RuntimeError, match="FK 547 on BillLineItem delete") as exc_info:
            svc.delete_by_public_id("bli-pub")

    assert exc_info.value is line_exc
    mock_mapping_repo.delete_by_id.assert_called_once_with(88)
    mock_mapping_repo.create.assert_called_once_with(
        bill_line_item_id=21, qbo_bill_line_id=600
    )
    mock_repo.delete_by_id.assert_called_once_with(21)


def test_bill_line_item_delete_no_mapping_skips_mapping_repo_mutations():
    line = SimpleNamespace(id=21, public_id="bli-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    mock_mapping_repo = Mock()
    mock_mapping_repo.read_by_bill_line_item_id.return_value = None

    svc = BillLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository"
    ) as ili_repo_cls, patch(
        "entities.contract_labor.persistence.repo.ContractLaborRepository"
    ) as cl_repo_cls, patch(
        "integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo.BillLineItemBillLineRepository",
        return_value=mock_mapping_repo,
    ):
        ili_repo_cls.return_value.delete_by_bill_line_item_id = Mock()
        cl_repo_cls.return_value.read_by_bill_line_item_id.return_value = []
        result = svc.delete_by_public_id("bli-pub")

    assert result is line
    mock_mapping_repo.delete_by_id.assert_not_called()
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(21)


# --- InvoiceLineItem ---


def test_invoice_line_item_delete_clears_qbo_mapping_before_line():
    line = SimpleNamespace(id=31, public_id="ili-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *_: call_order.append("line") or line

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=99, invoice_line_item_id=31, qbo_invoice_line_id=700)
    mock_mapping_repo.read_by_invoice_line_item_id.return_value = fake_mapping
    mock_mapping_repo.delete_by_id.side_effect = lambda *_: call_order.append("mapping")

    svc = InvoiceLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item_attachment.business.service.InvoiceLineItemAttachmentService"
    ) as ilia_svc_cls, patch(
        "integrations.intuit.qbo.invoice.connector.invoice_line_item.persistence.repo.InvoiceLineItemInvoiceLineRepository",
        return_value=mock_mapping_repo,
    ):
        ilia_svc_cls.return_value.repo.read_by_invoice_line_item_id.return_value = []
        result = svc.delete_by_public_id("ili-pub")

    assert call_order == ["mapping", "line"]
    assert result is line
    mock_mapping_repo.read_by_invoice_line_item_id.assert_called_once_with(31)
    mock_mapping_repo.delete_by_id.assert_called_once_with(99)
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(31)


def test_invoice_line_item_delete_line_failure_restores_qbo_mapping():
    line = SimpleNamespace(id=31, public_id="ili-pub")
    line_exc = RuntimeError("FK 547 on InvoiceLineItem delete")

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = line_exc

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=99, invoice_line_item_id=31, qbo_invoice_line_id=700)
    mock_mapping_repo.read_by_invoice_line_item_id.return_value = fake_mapping

    svc = InvoiceLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item_attachment.business.service.InvoiceLineItemAttachmentService"
    ) as ilia_svc_cls, patch(
        "integrations.intuit.qbo.invoice.connector.invoice_line_item.persistence.repo.InvoiceLineItemInvoiceLineRepository",
        return_value=mock_mapping_repo,
    ):
        ilia_svc_cls.return_value.repo.read_by_invoice_line_item_id.return_value = []
        with pytest.raises(RuntimeError, match="FK 547 on InvoiceLineItem delete") as exc_info:
            svc.delete_by_public_id("ili-pub")

    assert exc_info.value is line_exc
    mock_mapping_repo.delete_by_id.assert_called_once_with(99)
    mock_mapping_repo.create.assert_called_once_with(
        invoice_line_item_id=31, qbo_invoice_line_id=700
    )
    mock_repo.delete_by_id.assert_called_once_with(31)


def test_invoice_line_item_delete_no_mapping_skips_mapping_repo_mutations():
    line = SimpleNamespace(id=31, public_id="ili-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    mock_mapping_repo = Mock()
    mock_mapping_repo.read_by_invoice_line_item_id.return_value = None

    svc = InvoiceLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item_attachment.business.service.InvoiceLineItemAttachmentService"
    ) as ilia_svc_cls, patch(
        "integrations.intuit.qbo.invoice.connector.invoice_line_item.persistence.repo.InvoiceLineItemInvoiceLineRepository",
        return_value=mock_mapping_repo,
    ):
        ilia_svc_cls.return_value.repo.read_by_invoice_line_item_id.return_value = []
        result = svc.delete_by_public_id("ili-pub")

    assert result is line
    mock_mapping_repo.delete_by_id.assert_not_called()
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(31)


# --- ExpenseLineItem ---


def test_expense_line_item_delete_clears_qbo_mapping_before_line():
    line = SimpleNamespace(id=41, public_id="eli-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *_: call_order.append("line") or line

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=101, expense_line_item_id=41, qbo_purchase_line_id=800)
    mock_mapping_repo.read_by_expense_line_item_id.return_value = fake_mapping
    mock_mapping_repo.delete_by_id.side_effect = lambda *_: call_order.append("mapping")

    svc = ExpenseLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as elia_repo_cls, patch(
        "integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo.PurchaseLineExpenseLineItemRepository",
        return_value=mock_mapping_repo,
    ):
        elia_repo_cls.return_value.read_by_expense_line_item_id.return_value = None
        result = svc.delete_by_public_id("eli-pub")

    assert call_order == ["mapping", "line"]
    assert result is line
    mock_mapping_repo.read_by_expense_line_item_id.assert_called_once_with(41)
    mock_mapping_repo.delete_by_id.assert_called_once_with(101)
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(41)


def test_expense_line_item_delete_line_failure_restores_qbo_mapping():
    line = SimpleNamespace(id=41, public_id="eli-pub")
    line_exc = RuntimeError("FK 547 on ExpenseLineItem delete")

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = line_exc

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=101, expense_line_item_id=41, qbo_purchase_line_id=800)
    mock_mapping_repo.read_by_expense_line_item_id.return_value = fake_mapping

    svc = ExpenseLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as elia_repo_cls, patch(
        "integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo.PurchaseLineExpenseLineItemRepository",
        return_value=mock_mapping_repo,
    ):
        elia_repo_cls.return_value.read_by_expense_line_item_id.return_value = None
        with pytest.raises(RuntimeError, match="FK 547 on ExpenseLineItem delete") as exc_info:
            svc.delete_by_public_id("eli-pub")

    assert exc_info.value is line_exc
    mock_mapping_repo.delete_by_id.assert_called_once_with(101)
    mock_mapping_repo.create.assert_called_once_with(
        qbo_purchase_line_id=800, expense_line_item_id=41
    )
    mock_repo.delete_by_id.assert_called_once_with(41)


def test_expense_line_item_delete_no_mapping_skips_mapping_repo_mutations():
    line = SimpleNamespace(id=41, public_id="eli-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    mock_mapping_repo = Mock()
    mock_mapping_repo.read_by_expense_line_item_id.return_value = None

    svc = ExpenseLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as elia_repo_cls, patch(
        "integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo.PurchaseLineExpenseLineItemRepository",
        return_value=mock_mapping_repo,
    ):
        elia_repo_cls.return_value.read_by_expense_line_item_id.return_value = None
        result = svc.delete_by_public_id("eli-pub")

    assert result is line
    mock_mapping_repo.delete_by_id.assert_not_called()
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(41)
