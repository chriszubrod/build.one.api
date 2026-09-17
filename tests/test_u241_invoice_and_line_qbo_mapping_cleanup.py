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
    helper below (U-362) one family later. It DOES still clear the mapping
    TABLE row first though (see
    test_bill_line_item_delete_clears_legacy_mapping_row_before_line below) —
    the table itself isn't dropped by this unit and still carries a live NO
    ACTION FK onto BillLineItem."""
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


def test_bill_line_item_delete_clears_legacy_mapping_row_before_line():
    """U-363 deploy-gap bridge: the OBJECT_ID-guarded clear must run BEFORE the
    line delete, or a still-mapped row's delete 547s against the live
    FK_BillLineItemBillLine_BillLineItem constraint.

    U-446c moved this from Python into `DeleteBillLineItemCascadeById`, so the
    invariant is now a property of the sproc — and a stronger one: the clear and
    the delete are in the SAME transaction, where before they were two, and a
    completion landing between them left the mapping cleared on a line that was
    then refused.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(
        REPO_ROOT / "entities/bill_line_item/sql/dbo.bill_line_item.sql",
        "DeleteBillLineItemCascadeById",
    )
    executable = "\n".join(l.split("--")[0] for l in body.splitlines())

    assert "OBJECT_ID('qbo.BillLineItemBillLine')" in executable, (
        "the guard is what makes an already-dropped table a plain no-op"
    )
    assert executable.index("qbo.[BillLineItemBillLine]") < executable.index(
        "DELETE FROM dbo.[BillLineItem]"
    ), "the mapping must be cleared before the line it points at"


def test_bill_line_item_delete_no_longer_swallows_a_mapping_clear_failure():
    """Deliberately INVERTED by U-446c, and worth stating plainly.

    The old shape cleared the mapping on its own connection and swallowed any
    failure, on the reasoning that "the FK is the real safety net — a genuinely
    still-mapped row 547s on the line delete instead". That reasoning conceded
    the point: swallowing bought nothing, because the very next statement failed
    anyway, just less legibly.

    Now it is one transaction, so a real failure rolls the whole delete back and
    nothing is half-done. The already-dropped case — the only one the swallow
    actually protected — is handled by the OBJECT_ID guard, which never raises.
    """
    import inspect

    from entities.bill_line_item.business.service import BillLineItemService

    src = inspect.getsource(BillLineItemService.delete_by_public_id)
    assert "get_connection" not in src, (
        "the cascade must not open its own connection any more — that was the "
        "separate transaction this unit removed"
    )
    assert "delete_cascade_by_id" in src


# --- InvoiceLineItem ---


def test_invoice_line_item_delete_no_longer_uses_the_shared_restore_helper():
    """U-362: qbo.InvoiceLineItemInvoiceLine's CONNECTOR is retired
    (dbo.InvoiceLineItem.QboId/RealmId, U-238b, is the sole identity store),
    so the delete path no longer routes through the shared clear-then-restore
    helper (delete_own_qbo_mapping_before_header) the way it did pre-U-362 —
    mirrors test_invoice_delete_no_longer_clears_any_qbo_mapping above (U-356)
    one family later. It DOES still clear the mapping TABLE row first though
    (see test_invoice_line_item_delete_clears_legacy_mapping_row_before_line
    below) — the table itself isn't dropped by this unit and still carries a
    live NO ACTION FK onto InvoiceLineItem."""
    line = SimpleNamespace(id=31, public_id="ili-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    svc = InvoiceLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item_attachment.business.service.InvoiceLineItemAttachmentService"
    ) as ilia_svc_cls, patch(
        "integrations.intuit.qbo.base.mapping_cleanup.delete_own_qbo_mapping_before_header"
    ) as legacy_helper, patch(
        "shared.database.get_connection"
    ):
        ilia_svc_cls.return_value.repo.read_by_invoice_line_item_id.return_value = []
        result = svc.delete_by_public_id("ili-pub")

    assert result is line
    legacy_helper.assert_not_called()


def test_invoice_line_item_delete_clears_legacy_mapping_row_before_line():
    """U-362 deploy-gap bridge: the OBJECT_ID-guarded raw-SQL clear must run
    BEFORE the line delete, or a still-mapped row's delete 547s against the
    live FK_InvoiceLineItemInvoiceLine_InvoiceLineItem constraint."""
    line = SimpleNamespace(id=31, public_id="ili-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *_: call_order.append("line") or line

    mock_cursor = Mock()
    mock_cursor.execute.side_effect = lambda *_: call_order.append("mapping")
    mock_conn = Mock()
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    svc = InvoiceLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item_attachment.business.service.InvoiceLineItemAttachmentService"
    ) as ilia_svc_cls, patch(
        "shared.database.get_connection", return_value=mock_conn,
    ):
        ilia_svc_cls.return_value.repo.read_by_invoice_line_item_id.return_value = []
        result = svc.delete_by_public_id("ili-pub")

    assert call_order == ["mapping", "line"]
    assert result is line
    sql_text = mock_cursor.execute.call_args.args[0]
    assert "OBJECT_ID" in sql_text
    assert "qbo.InvoiceLineItemInvoiceLine" in sql_text or "[InvoiceLineItemInvoiceLine]" in sql_text
    assert mock_cursor.execute.call_args.args[1] == (31,)


def test_invoice_line_item_delete_mapping_clear_failure_is_swallowed_line_delete_still_runs():
    """Best-effort: a failure clearing the (possibly already-dropped) mapping
    row must never block the line delete itself — the FK is the real safety
    net (a genuinely still-mapped row 547s on the line delete instead)."""
    line = SimpleNamespace(id=31, public_id="ili-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    svc = InvoiceLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.invoice_line_item_attachment.business.service.InvoiceLineItemAttachmentService"
    ) as ilia_svc_cls, patch(
        "shared.database.get_connection",
        side_effect=RuntimeError("connection reset"),
    ):
        ilia_svc_cls.return_value.repo.read_by_invoice_line_item_id.return_value = []
        result = svc.delete_by_public_id("ili-pub")

    assert result is line
    mock_repo.delete_by_id.assert_called_once_with(31)
    mock_repo.delete_by_id.assert_called_once_with(31)


# --- ExpenseLineItem ---


def test_expense_line_item_delete_no_longer_uses_the_shared_restore_helper():
    """U-364: qbo.PurchaseLineExpenseLineItem's CONNECTOR is retired
    (dbo.ExpenseLineItem.QboId/RealmId, U-238b, is the sole identity store),
    so the delete path no longer routes through the shared clear-then-restore
    helper (delete_own_qbo_mapping_before_header) the way it did pre-U-364 —
    mirrors test_bill_line_item_delete_no_longer_uses_the_shared_restore_
    helper above (U-363) one family later. It DOES still clear the mapping
    TABLE row first though (see
    test_expense_line_item_delete_clears_legacy_mapping_row_before_line
    below) — the table itself isn't dropped by this unit and still carries a
    live NO ACTION FK onto ExpenseLineItem."""
    line = SimpleNamespace(id=41, public_id="eli-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    svc = ExpenseLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as elia_repo_cls, patch(
        "integrations.intuit.qbo.base.mapping_cleanup.delete_own_qbo_mapping_before_header"
    ) as legacy_helper, patch(
        "shared.database.get_connection"
    ):
        elia_repo_cls.return_value.read_by_expense_line_item_id.return_value = None
        result = svc.delete_by_public_id("eli-pub")

    assert result is line
    legacy_helper.assert_not_called()


def test_expense_line_item_delete_clears_legacy_mapping_row_before_line():
    """U-364 deploy-gap bridge: the OBJECT_ID-guarded raw-SQL clear must run
    BEFORE the line delete, or a still-mapped row's delete 547s against the
    live FK_PurchaseLineExpenseLineItem_ExpenseLineItem constraint."""
    line = SimpleNamespace(id=41, public_id="eli-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *a, **k: call_order.append("line") or line

    mock_cursor = Mock()
    mock_cursor.execute.side_effect = lambda *_: call_order.append("mapping")
    mock_conn = Mock()
    mock_conn.__enter__ = Mock(return_value=mock_conn)
    mock_conn.__exit__ = Mock(return_value=False)
    mock_conn.cursor.return_value = mock_cursor

    svc = ExpenseLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as elia_repo_cls, patch(
        "shared.database.get_connection", return_value=mock_conn,
    ):
        elia_repo_cls.return_value.read_by_expense_line_item_id.return_value = None
        result = svc.delete_by_public_id("eli-pub")

    assert call_order == ["mapping", "line"]
    assert result is line
    sql_text = mock_cursor.execute.call_args.args[0]
    assert "OBJECT_ID" in sql_text
    assert "qbo.PurchaseLineExpenseLineItem" in sql_text or "[PurchaseLineExpenseLineItem]" in sql_text
    assert mock_cursor.execute.call_args.args[1] == (41,)


def test_expense_line_item_delete_mapping_clear_failure_is_swallowed_line_delete_still_runs():
    """Best-effort: a failure clearing the (possibly already-dropped) mapping
    row must never block the line delete itself — the FK is the real safety
    net (a genuinely still-mapped row 547s on the line delete instead)."""
    line = SimpleNamespace(id=41, public_id="eli-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = line

    svc = ExpenseLineItemService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=line), patch(
        "entities.expense_line_item_attachment.persistence.repo.ExpenseLineItemAttachmentRepository"
    ) as elia_repo_cls, patch(
        "shared.database.get_connection",
        side_effect=RuntimeError("connection reset"),
    ):
        elia_repo_cls.return_value.read_by_expense_line_item_id.return_value = None
        result = svc.delete_by_public_id("eli-pub")

    assert result is line
    mock_repo.delete_by_id.assert_called_once_with(41, allow_terminal_parent=False)
