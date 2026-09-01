"""Pure-logic tests for U-226 — clear own qbo.* mapping on entity header delete."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from entities.bill.business.service import BillService
from entities.bill_credit.business.service import (
    BillCreditService,
    _clear_legacy_vendorcredit_billcredit_mapping,
)
from entities.expense.business.service import ExpenseService
from integrations.intuit.qbo.base.mapping_cleanup import delete_own_qbo_mapping_before_header


@contextmanager
def _granted_lock(*_args, **_kwargs):
    yield True


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_delete_own_qbo_mapping_before_header_no_mapping_calls_delete_header():
    delete_header = Mock(return_value="header-result")
    recreate_mapping = Mock()
    delete_own_qbo_mapping_before_header(
        read_mapping=Mock(return_value=None),
        delete_mapping=Mock(),
        recreate_mapping=recreate_mapping,
        delete_header=delete_header,
        entity_label="Bill",
        entity_id=42,
    )
    delete_header.assert_called_once()
    recreate_mapping.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_delete_own_qbo_mapping_before_header_success_returns_delete_header_result():
    mapping = SimpleNamespace(id=99)
    delete_mapping = Mock()
    delete_header = Mock(return_value="header-result")
    recreate_mapping = Mock()
    on_restore_failed = Mock()
    result = delete_own_qbo_mapping_before_header(
        read_mapping=Mock(return_value=mapping),
        delete_mapping=delete_mapping,
        recreate_mapping=recreate_mapping,
        delete_header=delete_header,
        entity_label="Bill",
        entity_id=42,
        on_restore_failed=on_restore_failed,
    )
    delete_mapping.assert_called_once_with(mapping)
    delete_header.assert_called_once()
    recreate_mapping.assert_not_called()
    on_restore_failed.assert_not_called()
    assert result == "header-result"


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_delete_own_qbo_mapping_before_header_read_failure_raises_value_error():
    read_exc = RuntimeError("db blip")
    delete_header = Mock()
    with pytest.raises(ValueError, match="failed to read qbo mapping") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(side_effect=read_exc),
            delete_mapping=Mock(),
            recreate_mapping=Mock(),
            delete_header=delete_header,
            entity_label="BillCredit",
            entity_id=42,
        )
    assert exc_info.value.__cause__ is read_exc
    delete_header.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_delete_own_qbo_mapping_before_header_delete_failure_raises_value_error():
    mapping = SimpleNamespace(id=99)
    delete_exc = RuntimeError("FK 547")
    delete_header = Mock()
    with pytest.raises(ValueError, match="failed to delete qbo mapping") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(return_value=mapping),
            delete_mapping=Mock(side_effect=delete_exc),
            recreate_mapping=Mock(),
            delete_header=delete_header,
            entity_label="Expense",
            entity_id=42,
        )
    assert exc_info.value.__cause__ is delete_exc
    delete_header.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_delete_own_qbo_mapping_before_header_header_failure_restores_mapping():
    mapping = SimpleNamespace(id=99)
    header_exc = RuntimeError("header delete 547")
    delete_header = Mock(side_effect=header_exc)
    recreate_mapping = Mock()
    on_restore_failed = Mock()
    with pytest.raises(RuntimeError, match="header delete 547") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(return_value=mapping),
            delete_mapping=Mock(),
            recreate_mapping=recreate_mapping,
            delete_header=delete_header,
            entity_label="Bill",
            entity_id=42,
            on_restore_failed=on_restore_failed,
        )
    assert exc_info.value is header_exc
    recreate_mapping.assert_called_once_with(mapping)
    on_restore_failed.assert_not_called()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_delete_own_qbo_mapping_before_header_header_failure_restore_also_fails():
    mapping = SimpleNamespace(id=99)
    header_exc = RuntimeError("header delete 547")
    restore_exc = RuntimeError("restore failed")
    delete_header = Mock(side_effect=header_exc)
    recreate_mapping = Mock(side_effect=restore_exc)
    on_restore_failed = Mock()
    with pytest.raises(RuntimeError, match="header delete 547") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(return_value=mapping),
            delete_mapping=Mock(),
            recreate_mapping=recreate_mapping,
            delete_header=delete_header,
            entity_label="Bill",
            entity_id=42,
            on_restore_failed=on_restore_failed,
        )
    assert exc_info.value is header_exc
    recreate_mapping.assert_called_once_with(mapping)
    on_restore_failed.assert_called_once_with(mapping, restore_exc)


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_delete_own_qbo_mapping_before_header_restore_failed_callback_failure_still_raises_header():
    mapping = SimpleNamespace(id=99)
    header_exc = RuntimeError("header delete 547")
    restore_exc = RuntimeError("restore failed")
    callback_exc = RuntimeError("callback blew up")
    delete_header = Mock(side_effect=header_exc)
    recreate_mapping = Mock(side_effect=restore_exc)
    on_restore_failed = Mock(side_effect=callback_exc)
    with pytest.raises(RuntimeError, match="header delete 547") as exc_info:
        delete_own_qbo_mapping_before_header(
            read_mapping=Mock(return_value=mapping),
            delete_mapping=Mock(),
            recreate_mapping=recreate_mapping,
            delete_header=delete_header,
            entity_label="Bill",
            entity_id=42,
            on_restore_failed=on_restore_failed,
        )
    assert exc_info.value is header_exc
    on_restore_failed.assert_called_once_with(mapping, restore_exc)


def test_bill_credit_delete_no_longer_clears_any_qbo_mapping():
    """U-353: qbo.VendorCreditBillCredit is retired — BillCredit's delete no
    longer calls delete_own_qbo_mapping_before_header at all (dbo.BillCredit.
    QboId/RealmId are plain columns that die with the row; there is no
    separate mapping row to clear first). A straight header delete, same
    shape as Bill/Expense before U-226 ever touched them — plus the U-353
    deploy-gap bridge (see the dedicated tests below), which is a real DB call
    here since it isn't mocked; that's fine — the harness's no-live-DB guard
    fires and is caught by the bridge's own broad tolerance."""
    bill_credit = SimpleNamespace(id=42, public_id="bc-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = bill_credit

    svc = BillCreditService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=bill_credit), patch(
        "entities.bill_credit_line_item.business.service.BillCreditLineItemService"
    ) as li_svc_cls:
        li_svc_cls.return_value.read_by_bill_credit_id.return_value = []
        result = svc.delete_by_public_id("bc-pub")

    assert result is bill_credit
    mock_repo.delete_by_id.assert_called_once_with(42)


# --- U-353 deploy-gap bridge: _clear_legacy_vendorcredit_billcredit_mapping ---
#
# Builders never apply prod DDL — the DROP TABLE for qbo.VendorCreditBillCredit
# lands AFTER this unit's code deploys, not atomically with it (see repo CLAUDE.md
# + the call site's own comment). Until that DROP is applied, a historical
# BillCredit synced before this unit may still have a legacy mapping row pointing
# at it via a NO_ACTION FK — deleting the header without clearing it first 547s.

_MODULE = "entities.bill_credit.business.service"
# get_connection is imported INSIDE _clear_legacy_vendorcredit_billcredit_mapping
# (not at module top), so it must be patched at its own definition — patching
# f"{_MODULE}.get_connection" would find no such module attribute to replace.
_DATABASE_MODULE = "shared.database"


def test_legacy_mapping_bridge_issues_object_id_guarded_delete():
    """The IF OBJECT_ID(...) IS NOT NULL guard makes both the table-still-live
    AND the table-already-dropped case a single statement evaluated by SQL
    Server itself — no Python-side table-existence branching needed (and none
    left to test): the guard is SQL Server's job, not this function's."""
    cursor = Mock()
    conn = Mock()
    conn.cursor.return_value = cursor

    with patch(f"{_DATABASE_MODULE}.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = conn
        _clear_legacy_vendorcredit_billcredit_mapping(42)

    cursor.execute.assert_called_once_with(
        "IF OBJECT_ID('qbo.VendorCreditBillCredit', 'U') IS NOT NULL "
        "DELETE FROM [qbo].[VendorCreditBillCredit] WHERE [BillCreditId] = ?",
        (42,),
    )


def test_legacy_mapping_bridge_logs_but_swallows_unexpected_errors():
    """A genuine unexpected DB error (network blip, etc.) is logged, not raised —
    best-effort only. The real safety net is the FK itself: if a mapping row
    really does still exist and this bridge failed to clear it, the subsequent
    header DELETE 547s anyway (fail-safe, not fail-silent-corruption)."""
    with patch(f"{_DATABASE_MODULE}.get_connection") as mock_get_conn, patch(
        f"{_MODULE}.logger"
    ) as mock_logger:
        mock_get_conn.side_effect = RuntimeError("connection reset")
        _clear_legacy_vendorcredit_billcredit_mapping(42)  # must not raise

    mock_logger.warning.assert_called_once()


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_bill_delete_clears_qbo_mapping_before_header():
    bill = SimpleNamespace(id=7, public_id="bill-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *_: call_order.append("header") or bill

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=55, qbo_bill_id=200)
    mock_mapping_repo.read_by_bill_id.return_value = fake_mapping
    mock_mapping_repo.delete_by_id.side_effect = lambda *_: call_order.append("mapping")

    svc = BillService(repo=mock_repo)
    svc.bill_line_item_service.read_by_bill_id = Mock(return_value=[])

    with patch.object(svc, "read_by_public_id", return_value=bill), patch(
        "entities.review.persistence.repo.ReviewRepository"
    ) as review_repo_cls, patch(
        "integrations.ms.mail.message.connector.bill.persistence.repo.MsMessageBillRepository"
    ) as ms_msg_repo_cls, patch(
        "integrations.intuit.qbo.bill.connector.bill.persistence.repo.BillBillRepository",
        return_value=mock_mapping_repo,
    ):
        review_repo_cls.return_value.delete_by_bill_id = Mock()
        ms_msg_repo_cls.return_value.read_by_bill_id.return_value = []
        svc.delete_by_public_id("bill-pub")

    assert call_order == ["mapping", "header"]
    mock_mapping_repo.read_by_bill_id.assert_called_once_with(7)
    mock_mapping_repo.delete_by_id.assert_called_once_with(55)
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(7)


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_expense_delete_clears_qbo_mapping_before_header():
    expense = SimpleNamespace(id=99, public_id="exp-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *_: call_order.append("header") or expense

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=12, qbo_purchase_id=300)
    mock_mapping_repo.read_by_expense_id.return_value = fake_mapping
    mock_mapping_repo.delete_by_id.side_effect = lambda *_: call_order.append("mapping")

    svc = ExpenseService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=expense), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as li_svc_cls, patch(
        "integrations.intuit.qbo.purchase.connector.expense.persistence.repo.PurchaseExpenseRepository",
        return_value=mock_mapping_repo,
    ):
        li_svc_cls.return_value.read_by_expense_id.return_value = []
        svc.delete_by_public_id("exp-pub")

    assert call_order == ["mapping", "header"]
    mock_mapping_repo.read_by_expense_id.assert_called_once_with(99)
    mock_mapping_repo.delete_by_id.assert_called_once_with(12)
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(99)


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_expense_delete_header_failure_restores_qbo_mapping():
    expense = SimpleNamespace(id=99, public_id="exp-pub")
    header_exc = RuntimeError("FK 547 on Expense delete")

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = header_exc

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=12, qbo_purchase_id=300, expense_id=99)
    mock_mapping_repo.read_by_expense_id.return_value = fake_mapping

    svc = ExpenseService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=expense), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as li_svc_cls, patch(
        "integrations.intuit.qbo.purchase.connector.expense.persistence.repo.PurchaseExpenseRepository",
        return_value=mock_mapping_repo,
    ):
        li_svc_cls.return_value.read_by_expense_id.return_value = []
        with pytest.raises(RuntimeError, match="FK 547 on Expense delete") as exc_info:
            svc.delete_by_public_id("exp-pub")

    assert exc_info.value is header_exc
    mock_mapping_repo.delete_by_id.assert_called_once_with(12)
    mock_mapping_repo.create.assert_called_once_with(
        qbo_purchase_id=300, expense_id=99
    )
    mock_repo.delete_by_id.assert_called_once_with(99)


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_expense_delete_header_and_mapping_restore_failure_records_reconciliation_issue():
    expense = SimpleNamespace(id=99, public_id="exp-pub")
    header_exc = RuntimeError("FK 547 on Expense delete")
    restore_exc = RuntimeError("mapping recreate failed")

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = header_exc

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(id=12, qbo_purchase_id=300, expense_id=99)
    mock_mapping_repo.read_by_expense_id.return_value = fake_mapping
    mock_mapping_repo.create.side_effect = restore_exc

    mock_staging = SimpleNamespace(realm_id="realm-1", qbo_id="qbo-99")
    mock_staging_repo = Mock()
    mock_staging_repo.read_by_id.return_value = mock_staging

    svc = ExpenseService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=expense), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as li_svc_cls, patch(
        "integrations.intuit.qbo.purchase.connector.expense.persistence.repo.PurchaseExpenseRepository",
        return_value=mock_mapping_repo,
    ), patch(
        "integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository",
        return_value=mock_staging_repo,
    ), patch(
        "integrations.intuit.qbo.base.delete_reconcile.record_partial_delete_issue"
    ) as record_issue:
        li_svc_cls.return_value.read_by_expense_id.return_value = []
        with pytest.raises(RuntimeError, match="FK 547 on Expense delete") as exc_info:
            svc.delete_by_public_id("exp-pub")

    assert exc_info.value is header_exc
    mock_staging_repo.read_by_id.assert_called_once_with(300)
    record_issue.assert_called_once()
    assert record_issue.call_args.kwargs["entity_type"] == "Expense"
    assert record_issue.call_args.kwargs["mapping_label"] == "PurchaseExpense"
    assert record_issue.call_args.kwargs["mapped_label"] == "Expense"
    assert record_issue.call_args.kwargs["realm_id"] == "realm-1"
    assert record_issue.call_args.kwargs["qbo_id"] == "qbo-99"
    assert record_issue.call_args.kwargs["local_id"] == 99
    assert record_issue.call_args.kwargs["error"] is restore_exc
