"""U-652 — CodingSuggestion grain + multi-line recode_purchase_lines (pure logic)."""
import copy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError
from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
    PurchaseChangedInQboError,
)
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from tests.test_expense_recode import (
    CATEGORIZE_ACCOUNT,
    FAKE_ITEM_REF,
    PURCHASE_QBO_ID,
    REALM_ID,
    SYNC_TOKEN,
    SUB_COST_CODE_ID,
    _build_connector,
    _make_categorize_line_dict,
    _make_item_sibling_dict,
    _make_raw_purchase,
    _patch_raw_client,
)

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"


def _line_target(
    *,
    target_qbo_line_id: str,
    sub_cost_code_id: int = SUB_COST_CODE_ID,
    project_id: int = 202,
    description: str = "recode desc",
    quantity=None,
    rate=None,
    amount=None,
    is_billable=None,
) -> dict:
    return {
        "target_qbo_line_id": target_qbo_line_id,
        "sub_cost_code_id": sub_cost_code_id,
        "project_id": project_id,
        "description": description,
        "quantity": quantity,
        "rate": rate,
        "amount": amount,
        "is_billable": is_billable,
    }


def _call_recode_lines(connector, lines, **overrides):
    kwargs = {
        "realm_id": REALM_ID,
        "qbo_purchase_qbo_id": PURCHASE_QBO_ID,
        "lines": lines,
        "expected_sync_token": SYNC_TOKEN,
    }
    kwargs.update(overrides)
    return connector.recode_purchase_lines(**kwargs)


def _make_coding_suggestion(*, status="enqueued", sync_token_at_suggest="5"):
    return SimpleNamespace(
        public_id=PUBLIC_ID,
        status=status,
        qbo_purchase_qbo_id=PURCHASE_QBO_ID,
        qbo_purchase_id=999,
        realm_id=REALM_ID,
        confirmed_sub_cost_code_id=SUB_COST_CODE_ID,
        confirmed_project_id=202,
        confirmed_description="recode desc",
        sync_token_at_suggest=sync_token_at_suggest,
    )


def _make_outbox_row(*, entity_type="CodingSuggestion"):
    return QboOutbox(
        id=1,
        public_id="outbox-1",
        row_version="abc",
        kind="recode_purchase_line",
        entity_type=entity_type,
        entity_public_id=PUBLIC_ID,
        realm_id=REALM_ID,
        request_id="req-1",
        status="processing",
        attempts=0,
    )


# --- Connector: two-line purchase, one update, one SyncToken check ---


def test_two_line_purchase_single_update_purchase_raw():
    line1 = _make_categorize_line_dict(line_id="1", line_num=1, amount="10.00")
    line2 = _make_categorize_line_dict(line_id="2", line_num=2, amount="20.00")
    fresh = _make_raw_purchase(lines=[line1, line2])
    connector = _build_connector()

    targets = [
        _line_target(
            target_qbo_line_id="1",
            quantity=1,
            rate=Decimal("10.00"),
            amount=Decimal("10.00"),
        ),
        _line_target(
            target_qbo_line_id="2",
            quantity=1,
            rate=Decimal("20.00"),
            amount=Decimal("20.00"),
            project_id=303,
            sub_cost_code_id=102,
            description="second line",
        ),
    ]

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        result = _call_recode_lines(connector, targets)

    assert result["status"] == "written"
    mock_client.get_purchase_raw.assert_called_once()
    mock_client.update_purchase_raw.assert_called_once()
    posted = mock_client.update_purchase_raw.call_args[0][0]
    assert posted["Line"][0]["DetailType"] == "ItemBasedExpenseLineDetail"
    assert posted["Line"][1]["DetailType"] == "ItemBasedExpenseLineDetail"
    assert posted["Line"][0]["Amount"] == "10.00"
    assert posted["Line"][1]["Description"] == "second line"


def test_token_mismatch_changed_in_qbo_no_update():
    line1 = _make_categorize_line_dict(line_id="1")
    fresh = _make_raw_purchase(lines=[line1], sync_token="9")
    connector = _build_connector()
    targets = [_line_target(target_qbo_line_id="1")]

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        with pytest.raises(PurchaseChangedInQboError):
            _call_recode_lines(connector, targets, expected_sync_token="5")

    mock_client.update_purchase_raw.assert_not_called()


def test_one_already_recoded_one_still_uncategorized_updates_only_second():
    already = {
        "Id": "1",
        "LineNum": 1,
        "Amount": "10.00",
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": FAKE_ITEM_REF.value, "name": FAKE_ITEM_REF.name},
        },
    }
    line2 = _make_categorize_line_dict(line_id="2", line_num=2, amount="20.00")
    fresh = _make_raw_purchase(lines=[already, line2])
    connector = _build_connector()
    targets = [
        _line_target(target_qbo_line_id="1"),
        _line_target(target_qbo_line_id="2"),
    ]

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        result = _call_recode_lines(connector, targets)

    assert result["status"] == "written"
    posted = mock_client.update_purchase_raw.call_args[0][0]
    assert posted["Line"][0]["DetailType"] == "ItemBasedExpenseLineDetail"
    assert posted["Line"][1]["DetailType"] == "ItemBasedExpenseLineDetail"


def test_foreign_item_off_placeholder_raises_no_update():
    foreign = {
        "Id": "1",
        "LineNum": 1,
        "Amount": "10.00",
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": "other-item", "name": "Other"},
        },
    }
    fresh = _make_raw_purchase(lines=[foreign])
    connector = _build_connector()
    targets = [_line_target(target_qbo_line_id="1")]

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        with pytest.raises(PurchaseChangedInQboError):
            _call_recode_lines(connector, targets)

    mock_client.update_purchase_raw.assert_not_called()


def test_all_targets_already_recoded_returns_already_recoded():
    already1 = {
        "Id": "1",
        "LineNum": 1,
        "Amount": "10.00",
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": FAKE_ITEM_REF.value, "name": FAKE_ITEM_REF.name},
        },
    }
    fresh = _make_raw_purchase(lines=[already1])
    connector = _build_connector()
    targets = [_line_target(target_qbo_line_id="1")]

    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        result = _call_recode_lines(connector, targets)

    assert result["status"] == "already_recoded"
    mock_client.update_purchase_raw.assert_not_called()


# --- Worker entity_type + multi-line resolution ---


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.coding_suggestion.business.service.CodingSuggestionService")
def test_worker_accepts_entity_type_coding_suggestion(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_coding_suggestion(status="enqueued")

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_lines.return_value = {"status": "written", "sync_token": "6"}

    with patch.object(
        QboOutboxWorker,
        "_resolve_recode_purchase_line_targets",
        return_value=([_line_target(target_qbo_line_id="1")], None),
    ):
        QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row(entity_type="CodingSuggestion"))

    connector.recode_purchase_lines.assert_called_once()
    svc.mark_written.assert_called_once_with(
        PUBLIC_ID, sync_token="6", expected_confirmation_seq=None
    )


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.coding_suggestion.business.service.CodingSuggestionService")
def test_worker_accepts_legacy_entity_type_expense_coding_item(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_coding_suggestion(status="enqueued")

    connector = MagicMock()
    mock_connector_cls.return_value = connector
    connector.recode_purchase_lines.return_value = {"status": "written", "sync_token": "6"}

    with patch.object(
        QboOutboxWorker,
        "_resolve_recode_purchase_line_targets",
        return_value=([_line_target(target_qbo_line_id="1")], None),
    ):
        QboOutboxWorker()._handle_recode_purchase_line(
            _make_outbox_row(entity_type="CodingSuggestion")
        )

    svc.mark_written.assert_called_once()


# --- Submit: dominant line + ConfirmedMixedCodes + one outbox ---


def test_submit_two_lines_different_codes_dominant_and_mixed_one_outbox():
    from entities.expense.business.service import ExpenseService

    expense_id = 55
    svc = ExpenseService()
    state = {
        "public_id": "ci-pub-1",
        "status": "pending",
        "suggested_project_id": 10,
        "suggested_sub_cost_code_id": 20,
    }

    dominant = {
        "project_id": 77,
        "sub_cost_code_id": 481,
        "description": "Dominant line",
        "confirmed_mixed_codes": True,
    }

    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={expense_id: [state]},
    ), patch.object(
        ExpenseService,
        "_resolve_dominant_expense_coding_for_submit",
        return_value=dominant,
    ), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
        return_value={"status": "enqueued", "enqueued": True},
    ) as mock_confirm, patch(
        "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
    ) as mock_outbox_svc:
        mock_outbox_svc.return_value.enqueue = MagicMock()
        summary = svc.enqueue_coding_recode_on_submit(expense_id=expense_id, user_id=17)

    mock_confirm.assert_called_once()
    call_kw = mock_confirm.call_args.kwargs
    assert call_kw["project_id"] == 77
    assert call_kw["sub_cost_code_id"] == 481
    assert call_kw.get("confirmed_mixed_codes") is True
    assert summary["enqueued"] == 1


@patch("integrations.intuit.qbo.base.client.writes_allowed", return_value=False)
@patch("integrations.intuit.qbo.base.client._recode_writes_allowed", return_value=False)
def test_submit_gates_off_writes_disabled_no_repo_write(_r, _w):
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(
        return_value=_make_coding_suggestion(status="suggested")
    )
    svc.record_confirmation = MagicMock()
    svc.repo = MagicMock()

    result = svc.confirm(
        public_id=PUBLIC_ID,
        project_id=202,
        sub_cost_code_id=101,
        description="desc",
        was_overridden=False,
        user_id=17,
        confirmed_mixed_codes=False,
    )

    assert result["status"] == "writes_disabled"
    svc.record_confirmation.assert_not_called()
    svc.repo.record_confirmation.assert_not_called()


def test_worker_two_uncategorized_one_unmapped_marks_error_no_recode():
    svc = MagicMock()
    item = _make_coding_suggestion(status="enqueued")

    with patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService",
        return_value=svc,
    ), patch(
        "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
    ) as mock_connector_cls:
        svc.read_by_public_id.return_value = item
        connector = MagicMock()
        mock_connector_cls.return_value = connector

        with patch.object(
            QboOutboxWorker,
            "_resolve_recode_purchase_line_targets",
            return_value=(None, "multi_line_unmapped_expense_line_item"),
        ):
            QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

        connector.recode_purchase_lines.assert_not_called()
        svc.mark_error.assert_called_once()


def test_worker_single_unmapped_line_uses_confirmed_fallback():
    """72-style: one uncategorized line, no ELI — connector gets confirmed_* only."""
    svc = MagicMock()
    item = _make_coding_suggestion(status="enqueued")

    fallback_target = [
        _line_target(
            target_qbo_line_id="1",
            project_id=item.confirmed_project_id,
            sub_cost_code_id=item.confirmed_sub_cost_code_id,
            description=item.confirmed_description,
        )
    ]

    with patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService",
        return_value=svc,
    ), patch(
        "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
    ) as mock_connector_cls:
        svc.read_by_public_id.return_value = item
        connector = MagicMock()
        mock_connector_cls.return_value = connector
        connector.recode_purchase_lines.return_value = {"status": "written", "sync_token": "7"}

        with patch.object(
            QboOutboxWorker,
            "_resolve_recode_purchase_line_targets",
            return_value=(fallback_target, None),
        ):
            QboOutboxWorker()._handle_recode_purchase_line(_make_outbox_row())

        connector.recode_purchase_lines.assert_called_once()
        lines_arg = connector.recode_purchase_lines.call_args.kwargs["lines"]
        assert len(lines_arg) == 1
        assert lines_arg[0]["sub_cost_code_id"] == item.confirmed_sub_cost_code_id
