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


def test_invoice_delete_no_longer_clears_any_qbo_mapping():
    """U-356: qbo.InvoiceInvoice is retired — Invoice's delete no longer calls
    delete_own_qbo_mapping_before_header at all (dbo.Invoice.QboId/RealmId are
    plain columns that die with the row; there is no separate mapping row to
    clear-then-restore). A straight header delete — the U-356 deploy-gap bridge
    that briefly preceded it was deleted in U-365 once the table was dropped."""
    invoice = SimpleNamespace(id=11, public_id="inv-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = invoice

    svc = InvoiceService(repo=mock_repo)
    svc.invoice_line_item_service.read_by_invoice_id = Mock(return_value=[])
    svc.invoice_attachment_service.read_by_invoice_id = Mock(return_value=[])

    with patch.object(svc, "read_by_public_id", return_value=invoice), patch(
        "integrations.intuit.qbo.base.mapping_cleanup.delete_own_qbo_mapping_before_header"
    ) as legacy_helper, patch("shared.database.get_connection") as get_conn:
        result = svc.delete_by_public_id("inv-pub")

    assert result is invoice
    legacy_helper.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(11)
    get_conn.assert_not_called()


# --- BillLineItem ---


def test_bill_line_item_delete_no_longer_uses_the_shared_restore_helper():
    """U-363: qbo.BillLineItemBillLine's CONNECTOR is retired
    (dbo.BillLineItem.QboId/RealmId, U-238b, is the sole identity store), so
    the delete path no longer routes through the shared clear-then-restore
    helper (delete_own_qbo_mapping_before_header) the way it did pre-U-363 —
    mirrors test_invoice_line_item_delete_no_longer_uses_the_shared_restore_
    helper below (U-362) one family later."""
    line = SimpleNamespace(id=21, public_id="bli-pub")

    mock_repo = Mock()
    # U-446c: the dependent cleanup moved into DeleteBillLineItemCascadeById, so
    # the service makes ONE repo call instead of four separate ones.
    mock_repo.delete_cascade_by_id.return_value = line

    svc = BillLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "integrations.intuit.qbo.base.mapping_cleanup.delete_own_qbo_mapping_before_header"
    ) as legacy_helper:
        result = svc.delete_by_public_id("bli-pub")

    assert result is line
    legacy_helper.assert_not_called()
    mock_repo.delete_cascade_by_id.assert_called_once_with(
        21, allow_terminal_parent=False
    )


# --- InvoiceLineItem ---


def test_invoice_line_item_delete_no_longer_uses_the_shared_restore_helper():
    """U-362: qbo.InvoiceLineItemInvoiceLine's CONNECTOR is retired
    (dbo.InvoiceLineItem.QboId/RealmId, U-238b, is the sole identity store),
    so the delete path no longer routes through the shared clear-then-restore
    helper (delete_own_qbo_mapping_before_header) the way it did pre-U-362 —
    mirrors test_invoice_delete_no_longer_clears_any_qbo_mapping above (U-356)
    one family later."""
    line = SimpleNamespace(id=31, public_id="ili-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    svc = InvoiceLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item_attachment.business.service.InvoiceLineItemAttachmentService"
    ) as ilia_svc_cls, patch(
        "integrations.intuit.qbo.base.mapping_cleanup.delete_own_qbo_mapping_before_header"
    ) as legacy_helper:
        ilia_svc_cls.return_value.repo.read_by_invoice_line_item_id.return_value = []
        result = svc.delete_by_public_id("ili-pub")

    assert result is line
    legacy_helper.assert_not_called()


# --- ExpenseLineItem ---


def test_expense_line_item_delete_no_longer_uses_the_shared_restore_helper():
    """U-364: qbo.PurchaseLineExpenseLineItem's CONNECTOR is retired
    (dbo.ExpenseLineItem.QboId/RealmId, U-238b, is the sole identity store),
    so the delete path no longer routes through the shared clear-then-restore
    helper (delete_own_qbo_mapping_before_header) the way it did pre-U-364 —
    mirrors test_bill_line_item_delete_no_longer_uses_the_shared_restore_
    helper above (U-363) one family later."""
    line = SimpleNamespace(id=41, public_id="eli-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    svc = ExpenseLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as elia_repo_cls, patch(
        "integrations.intuit.qbo.base.mapping_cleanup.delete_own_qbo_mapping_before_header"
    ) as legacy_helper:
        elia_repo_cls.return_value.read_by_expense_line_item_id.return_value = None
        result = svc.delete_by_public_id("eli-pub")

    assert result is line
    legacy_helper.assert_not_called()
