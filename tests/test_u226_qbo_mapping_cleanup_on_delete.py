"""Pure-logic tests for U-226 — clear own qbo.* mapping on entity header delete."""
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


@patch("integrations.intuit.qbo.base.mapping_cleanup.qbo_app_lock", _granted_lock)
def test_bill_credit_delete_clears_qbo_mapping_before_header():
    bill_credit = SimpleNamespace(id=42, public_id="bc-pub")
    call_order = []

    mock_repo = Mock()
    mock_repo.delete_by_id.side_effect = lambda *_: call_order.append("header") or bill_credit

    mock_mapping_repo = Mock()
    fake_mapping = SimpleNamespace(qbo_vendor_credit_id=100)
    mock_mapping_repo.read_by_bill_credit_id.return_value = fake_mapping
    mock_mapping_repo.delete_by_qbo_vendor_credit_id.side_effect = (
        lambda *_: call_order.append("mapping")
    )

    svc = BillCreditService(repo=mock_repo)

    with patch.object(svc, "read_by_public_id", return_value=bill_credit), patch(
        "entities.bill_credit_line_item.business.service.BillCreditLineItemService"
    ) as li_svc_cls, patch(
        "integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo.VendorCreditBillCreditMappingRepository",
        return_value=mock_mapping_repo,
    ):
        li_svc_cls.return_value.read_by_bill_credit_id.return_value = []
        svc.delete_by_public_id("bc-pub")

    assert call_order == ["mapping", "header"]
    mock_mapping_repo.read_by_bill_credit_id.assert_called_once_with(42)
    mock_mapping_repo.delete_by_qbo_vendor_credit_id.assert_called_once_with(100)
    mock_mapping_repo.create.assert_not_called()
    mock_repo.delete_by_id.assert_called_once_with(42)


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
