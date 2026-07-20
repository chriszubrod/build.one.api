"""Pure-logic unit tests for expense coding confirm → enqueue → outbox handler (Phase C2)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
    PurchaseChangedInQboError,
    PurchaseRecodeMappingError,
)
from entities.expense_coding_item.business.service import ExpenseCodingItemService


PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
REALM_ID = "realm-test"


def _make_item(*, status="enqueued", sync_token_at_suggest="5"):
    return SimpleNamespace(
        public_id=PUBLIC_ID,
        status=status,
        qbo_purchase_qbo_id="purchase-123",
        qbo_line_id="1",
        confirmed_sub_cost_code_id=101,
        confirmed_project_id=202,
        confirmed_description="recode desc",
        sync_token_at_suggest=sync_token_at_suggest,
        qbo_purchase_id=999,
        realm_id=REALM_ID,
    )


def _make_outbox_row():
    return QboOutbox(
        id=1,
        public_id="outbox-1",
        row_version="abc",
        kind="recode_purchase_line",
        entity_type="ExpenseCodingItem",
        entity_public_id=PUBLIC_ID,
        realm_id=REALM_ID,
        request_id="req-1",
        status="processing",
        attempts=0,
    )


# Gate-off confirm behavior (writes_disabled, nothing recorded) is covered in
# tests/test_expense_coding_item.py — the canonical home for the confirm() gate suite.


@patch("integrations.intuit.qbo.base.client._writes_allowed", return_value=True)
@patch("integrations.intuit.qbo.base.client._recode_writes_allowed", return_value=True)
def test_confirm_mapping_missing_no_confirmation_or_enqueue(
    _mock_recode_writes_allowed,
    _mock_writes_allowed,
):
    svc = ExpenseCodingItemService()
    svc.read_by_public_id = MagicMock(return_value=_make_item(status="suggested"))
    svc.record_confirmation = MagicMock()

    with patch(
        "integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo.ItemSubCostCodeRepository"
    ) as mock_item_repo_cls:
        mock_item_repo_cls.return_value.read_by_sub_cost_code_id.return_value = None

        result = svc.confirm(
            public_id=PUBLIC_ID,
            project_id=202,
            sub_cost_code_id=101,
            description="desc",
            was_overridden=False,
            user_id=17,
        )

    assert result == {
        "status": "mapping_missing",
        "reason": "SubCostCode has no QBO Item mapping",
    }
    svc.record_confirmation.assert_not_called()


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handle_recode_written_calls_mark_written(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_item(status="enqueued")

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_line.return_value = {
        "status": "written",
        "sync_token": "6",
    }

    QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    connector.recode_purchase_line.assert_called_once()
    svc.mark_written.assert_called_once_with(PUBLIC_ID, sync_token="6")


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handle_recode_processes_confirmed_state_crash_window(mock_svc_cls, mock_connector_cls):
    """An outbox row whose item is still 'confirmed' (enqueue succeeded but mark_enqueued
    did not) MUST still be written — not silently skipped (lost-write window)."""
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_item(status="confirmed")

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_line.return_value = {"status": "written", "sync_token": "6"}

    QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    connector.recode_purchase_line.assert_called_once()
    svc.mark_written.assert_called_once_with(PUBLIC_ID, sync_token="6")


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handle_recode_purchase_changed_marks_changed_in_qbo(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_item(status="enqueued")

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_line.side_effect = PurchaseChangedInQboError(
        qbo_purchase_qbo_id="purchase-123",
        expected_sync_token="5",
        actual_sync_token="9",
    )

    QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    svc.mark_changed_in_qbo.assert_called_once_with(PUBLIC_ID)
    svc.mark_written.assert_not_called()
    svc.mark_error.assert_not_called()


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handle_recode_sync_token_mismatch_marks_changed_in_qbo(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_item(status="enqueued")

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_line.side_effect = QboSyncTokenMismatchError("stale")

    QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    svc.mark_changed_in_qbo.assert_called_once_with(PUBLIC_ID)


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handle_recode_mapping_error_marks_error(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_item(status="enqueued")

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_line.side_effect = PurchaseRecodeMappingError(sub_cost_code_id=101)

    QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    svc.mark_error.assert_called_once()
    assert PUBLIC_ID in svc.mark_error.call_args[0]
    svc.mark_changed_in_qbo.assert_not_called()


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handle_recode_line_not_found_marks_changed_in_qbo(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_item(status="enqueued")

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_line.return_value = {"status": "line_not_found"}

    QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    svc.mark_changed_in_qbo.assert_called_once_with(PUBLIC_ID)
    connector.recode_purchase_line.assert_called_once()


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.expense_coding_item.business.service.ExpenseCodingItemService")
def test_handle_recode_skips_when_not_enqueued(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_item(status="written")

    connector = MagicMock()
    mock_connector_cls.return_value = connector

    QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

    connector.recode_purchase_line.assert_not_called()
    svc.mark_written.assert_not_called()
    svc.mark_changed_in_qbo.assert_not_called()
    svc.mark_error.assert_not_called()
