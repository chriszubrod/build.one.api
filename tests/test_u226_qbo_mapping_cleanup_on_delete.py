"""Pure-logic tests for delete_own_qbo_mapping_before_header (U-226/U-243), plus
header-delete characterization: Bill/BillCredit/Expense/Invoice clear no qbo.*
mapping on their own delete (U-353..U-356; their deploy-gap bridges were deleted
in U-365)."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from entities.bill.business.service import BillService
from entities.bill_credit.business.service import BillCreditService
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
    shape as Bill/Expense before U-226 ever touched them (the U-353 deploy-gap
    bridge that briefly preceded it was deleted in U-365)."""
    bill_credit = SimpleNamespace(id=42, public_id="bc-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = bill_credit

    svc = BillCreditService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=bill_credit), patch(
        "entities.bill_credit_line_item.business.service.BillCreditLineItemService"
    ) as li_svc_cls, patch("shared.database.get_connection") as get_conn:
        li_svc_cls.return_value.read_by_bill_credit_id.return_value = []
        result = svc.delete_by_public_id("bc-pub")

    assert result is bill_credit
    mock_repo.delete_by_id.assert_called_once_with(42)
    get_conn.assert_not_called()


def test_expense_delete_no_longer_clears_any_qbo_mapping():
    """U-354: qbo.PurchaseExpense is retired — Expense's delete no longer calls
    delete_own_qbo_mapping_before_header at all (dbo.Expense.QboId/RealmId are
    plain columns that die with the row; there is no separate mapping row to
    clear first). A straight header delete, same shape as Bill/BillCredit
    before U-226 ever touched them (the U-354 deploy-gap bridge that briefly
    preceded it was deleted in U-365)."""
    expense = SimpleNamespace(id=99, public_id="exp-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = expense

    svc = ExpenseService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=expense), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService"
    ) as li_svc_cls, patch("shared.database.get_connection") as get_conn:
        li_svc_cls.return_value.read_by_expense_id.return_value = []
        result = svc.delete_by_public_id("exp-pub")

    assert result is expense
    mock_repo.delete_by_id.assert_called_once_with(99)
    get_conn.assert_not_called()


def test_bill_delete_no_longer_clears_any_qbo_mapping():
    """U-355: qbo.BillBill is retired — Bill's delete no longer calls
    delete_own_qbo_mapping_before_header at all (dbo.Bill.QboId/RealmId are
    plain columns that die with the row; there is no separate mapping row to
    clear first). A straight header delete, same shape as BillCredit/Expense
    before U-226 ever touched them (the U-355 deploy-gap bridge that briefly
    preceded it was deleted in U-365)."""
    bill = SimpleNamespace(id=7, public_id="bill-pub")

    mock_repo = Mock()
    mock_repo.delete_by_id.return_value = bill

    svc = BillService(repo=mock_repo)
    svc.bill_line_item_service.read_by_bill_id = Mock(return_value=[])

    with patch.object(svc, "read_by_public_id", return_value=bill), patch(
        "entities.review.persistence.repo.ReviewRepository"
    ) as review_repo_cls, patch(
        "integrations.ms.mail.message.connector.bill.persistence.repo.MsMessageBillRepository"
    ) as ms_msg_repo_cls, patch("shared.database.get_connection") as get_conn:
        review_repo_cls.return_value.delete_by_bill_id = Mock()
        ms_msg_repo_cls.return_value.read_by_bill_id.return_value = []
        result = svc.delete_by_public_id("bill-pub")

    assert result is bill
    mock_repo.delete_by_id.assert_called_once_with(7)
    get_conn.assert_not_called()
