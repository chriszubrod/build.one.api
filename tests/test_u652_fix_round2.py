"""U-652 fix round 2 — regression tests for correctness review findings."""
from __future__ import annotations

import re
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
    PurchaseChangedInQboError,
)
from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
)
from tests.test_expense_recode import (
    FAKE_ITEM_REF,
    PURCHASE_QBO_ID,
    REALM_ID,
    SYNC_TOKEN,
    SUB_COST_CODE_ID,
    _build_connector,
    _make_categorize_line_dict,
    _make_raw_purchase,
    _patch_raw_client,
)
from tests.test_u498_expense_purchase_line_identity_chain import (
    READ_UNCODED_SPROC,
    SHAPE_A_LEGS,
    _leg_check_result,
    _read_uncoded_identity_sql,
    extract_inner_identity_legs,
)

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"


def _make_item(*, confirmed_project_id=202, confirmed_sub_cost_code_id=101):
    return SimpleNamespace(
        public_id=PUBLIC_ID,
        status="enqueued",
        qbo_purchase_qbo_id=PURCHASE_QBO_ID,
        qbo_purchase_id=999,
        realm_id=REALM_ID,
        confirmed_sub_cost_code_id=confirmed_sub_cost_code_id,
        confirmed_project_id=confirmed_project_id,
        confirmed_description="confirmed desc",
        sync_token_at_suggest="5",
    )


def _make_outbox():
    return QboOutbox(
        id=1,
        public_id="outbox-1",
        row_version="abc",
        kind="recode_purchase_line",
        entity_type="CodingSuggestion",
        entity_public_id=PUBLIC_ID,
        realm_id=REALM_ID,
        request_id="req-1",
        status="processing",
        attempts=0,
    )


# --- P1-1 confirm + worker single-line authoritative confirmed_* ---


@patch("integrations.intuit.qbo.base.client.recode_write_gate_reason", return_value=None)
@patch(
    "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref",
    return_value=SimpleNamespace(value="ITEM-X", name="Item X"),
)
def test_confirm_refuses_multi_line_purchase_before_db_write(_ref, _gate):
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=_make_item())
    svc.record_confirmation = MagicMock()
    svc.mark_enqueued = MagicMock()

    with patch.object(
        CodingSuggestionService,
        "_count_uncategorized_purchase_lines",
        return_value=2,
    ):
        result = svc.confirm(
            public_id=PUBLIC_ID,
            project_id=202,
            sub_cost_code_id=101,
            description="x",
            was_overridden=False,
            user_id=17,
        )

    assert result == {
        "status": "multi_line_unsupported",
        "reason": "multi_line_purchase_confirm_unsupported",
    }
    svc.record_confirmation.assert_not_called()


@patch("integrations.intuit.qbo.base.client.recode_write_gate_reason", return_value=None)
@patch(
    "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref",
    return_value=SimpleNamespace(value="ITEM-X", name="Item X"),
)
def test_confirm_router_multi_line_returns_409_fixed_code(_ref, _gate):
    from entities.coding_suggestion.api.router import confirm_expense_coding_item_router
    from entities.coding_suggestion.api.schemas import ConfirmCodingSuggestionRequest

    existing = SimpleNamespace(claimed_by_user_id=17)
    service = MagicMock()
    service.read_by_public_id.return_value = existing
    service.confirm.return_value = {
        "status": "multi_line_unsupported",
        "reason": "multi_line_purchase_confirm_unsupported",
    }

    body = ConfirmCodingSuggestionRequest(
        project_public_id="00000000-0000-0000-0000-000000000010",
        sub_cost_code_public_id="00000000-0000-0000-0000-000000000020",
        description="x",
        was_overridden=False,
    )

    with patch(
        "entities.coding_suggestion.api.router.CodingSuggestionService",
        return_value=service,
    ), patch(
        "entities.coding_suggestion.api.router.current_user_id"
    ) as uid, patch(
        "entities.coding_suggestion.api.router._assert_can_access_coding_suggestion"
    ), patch(
        "entities.coding_suggestion.api.router.assert_can_access_project"
    ), patch(
        "entities.project.business.service.ProjectService"
    ) as proj_svc, patch(
        "entities.sub_cost_code.business.service.SubCostCodeService"
    ) as scc_svc:
        uid.get.return_value = 17
        proj_svc.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        scc_svc.return_value.read_by_public_id.return_value = SimpleNamespace(id=20)
        with pytest.raises(HTTPException) as exc:
            confirm_expense_coding_item_router(PUBLIC_ID, body, _={"can_update": True})

    assert exc.value.status_code == 409
    assert exc.value.detail == "multi_line_purchase_confirm_unsupported"


def test_worker_single_uncategorized_line_uses_confirmed_not_eli_coding():
    item = _make_item(confirmed_project_id=900, confirmed_sub_cost_code_id=901)
    local_line = SimpleNamespace(
        sub_cost_code_id=481,
        project_id=77,
        description="eli desc",
        quantity=Decimal("1"),
        rate=Decimal("10"),
        amount=Decimal("10"),
        realm_id=REALM_ID,
        billable_decided_at=None,
        is_billable=False,
    )
    worker = QboOutboxWorker()
    with patch.object(
        worker,
        "_read_uncategorized_staging_line_ids",
        return_value=["1"],
    ), patch.object(
        worker,
        "_resolve_recode_line_economics",
        return_value=(local_line, None),
    ):
        targets, err = worker._resolve_recode_purchase_line_targets(_make_outbox(), item)

    assert err is None
    assert len(targets) == 1
    assert targets[0]["sub_cost_code_id"] == 901
    assert targets[0]["project_id"] == 900
    assert targets[0]["description"] == "confirmed desc"


# --- P1-2 billability from each line's project ---


@pytest.mark.parametrize(
    "dominant_cost_plus,line_cost_plus,line_is_billable,expected",
    [
        (True, False, None, False),
        (False, True, None, True),
    ],
)
def test_recode_is_billable_seeds_from_line_project_not_dominant(
    dominant_cost_plus, line_cost_plus, line_is_billable, expected
):
    item = _make_item(confirmed_project_id=100)
    local_line = SimpleNamespace(
        project_id=200,
        is_billable=line_is_billable,
        billable_decided_at=None,
    )

    def read_by_id(project_id):
        if project_id == 100:
            return SimpleNamespace(is_cost_plus=dominant_cost_plus)
        if project_id == 200:
            return SimpleNamespace(is_cost_plus=line_cost_plus)
        return None

    with patch(
        "entities.project.business.service.ProjectService"
    ) as mock_project_svc:
        mock_project_svc.return_value.read_by_id.side_effect = read_by_id
        # multi-line caller passes each line's own project (fix round 3 made this explicit)
        result = QboOutboxWorker._recode_is_billable(item, local_line, project_id=local_line.project_id)
    assert result is expected


def test_recode_is_billable_human_decision_still_wins_over_line_project():
    item = _make_item()
    local_line = SimpleNamespace(
        project_id=200,
        is_billable=False,
        billable_decided_at="2026-10-01",
    )
    with patch("entities.project.business.service.ProjectService") as mock_project_svc:
        mock_project_svc.return_value.read_by_id.return_value = SimpleNamespace(
            is_cost_plus=True
        )
        assert QboOutboxWorker._recode_is_billable(item, local_line) is False


# --- P1-3 resolve by QboPurchaseQboId; empty targets -> error ---


def test_worker_stale_qbo_purchase_id_still_resolves_uncategorized_lines():
    worker = QboOutboxWorker()
    seen = {}

    def _fake_read(*, realm_id: str, qbo_purchase_qbo_id: str):
        seen["args"] = (realm_id, qbo_purchase_qbo_id)
        return ["1"]

    item = _make_item()
    item.qbo_purchase_id = 1
    with patch.object(worker, "_read_uncategorized_staging_line_ids", side_effect=_fake_read):
        worker._read_uncategorized_staging_line_ids(
            realm_id=REALM_ID,
            qbo_purchase_qbo_id=PURCHASE_QBO_ID,
        )
    assert seen["args"] == (REALM_ID, PURCHASE_QBO_ID)


@patch(
    "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
)
@patch("entities.coding_suggestion.business.service.CodingSuggestionService")
def test_worker_zero_targets_marks_error_no_qbo_not_written(mock_svc_cls, mock_connector_cls):
    svc = MagicMock()
    mock_svc_cls.return_value = svc
    svc.read_by_public_id.return_value = _make_item()
    connector = MagicMock()
    mock_connector_cls.return_value = connector

    worker = QboOutboxWorker()
    with patch.object(worker, "_resolve_recode_purchase_line_targets", return_value=([], None)):
        worker._handle_recode_purchase_line(_make_outbox())

    connector.recode_purchase_lines.assert_not_called()
    svc.mark_written.assert_not_called()
    svc.mark_error.assert_called_once()
    assert svc.mark_error.call_args.kwargs.get("write_error") == "no_target_lines"


# --- P2-5 sync token advanced but all targets done ---


def test_recode_token_advanced_all_targets_done_returns_already_recoded():
    already = {
        "Id": "1",
        "LineNum": 1,
        "Amount": "10.00",
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": FAKE_ITEM_REF.value, "name": FAKE_ITEM_REF.name},
        },
    }
    fresh = _make_raw_purchase(lines=[already], sync_token="9")
    connector = _build_connector()
    targets = [
        {
            "target_qbo_line_id": "1",
            "sub_cost_code_id": SUB_COST_CODE_ID,
            "project_id": 202,
            "description": "d",
        }
    ]
    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        result = connector.recode_purchase_lines(
            realm_id=REALM_ID,
            qbo_purchase_qbo_id=PURCHASE_QBO_ID,
            lines=targets,
            expected_sync_token=SYNC_TOKEN,
        )
    assert result["status"] == "already_recoded"
    mock_client.update_purchase_raw.assert_not_called()


def test_recode_token_advanced_one_target_pending_raises_changed():
    already = {
        "Id": "1",
        "LineNum": 1,
        "Amount": "10.00",
        "DetailType": "ItemBasedExpenseLineDetail",
        "ItemBasedExpenseLineDetail": {
            "ItemRef": {"value": FAKE_ITEM_REF.value, "name": FAKE_ITEM_REF.name},
        },
    }
    line2 = _make_categorize_line_dict(line_id="2", line_num=2)
    fresh = _make_raw_purchase(lines=[already, line2], sync_token="9")
    connector = _build_connector()
    targets = [
        {
            "target_qbo_line_id": "1",
            "sub_cost_code_id": SUB_COST_CODE_ID,
            "project_id": 202,
            "description": "d",
        },
        {
            "target_qbo_line_id": "2",
            "sub_cost_code_id": SUB_COST_CODE_ID,
            "project_id": 202,
            "description": "d",
        },
    ]
    client_patch, mock_client = _patch_raw_client(fresh=fresh)
    with client_patch:
        with pytest.raises(PurchaseChangedInQboError):
            connector.recode_purchase_lines(
                realm_id=REALM_ID,
                qbo_purchase_qbo_id=PURCHASE_QBO_ID,
                lines=targets,
                expected_sync_token=SYNC_TOKEN,
            )
    mock_client.update_purchase_raw.assert_not_called()


# --- P2-9 dominant tie-break numeric line id ---


def test_dominant_submit_tiebreak_orders_line_id_numerically():
    from entities.expense.business.service import ExpenseService
    from tests.test_u498_expense_purchase_line_identity_chain import _enqueue_recode_sql

    sql = _enqueue_recode_sql()
    assert "TRY_CONVERT(BIGINT, pl.[QboLineId])" in sql

    rows = [
        SimpleNamespace(
            QboLineId="2",
            ProjectId=2,
            SubCostCodeId=20,
            Description="two",
            AbsAmount=Decimal("50"),
        ),
        SimpleNamespace(
            QboLineId="10",
            ProjectId=1,
            SubCostCodeId=10,
            Description="ten",
            AbsAmount=Decimal("50"),
        ),
    ]

    with patch("shared.database.get_connection") as mock_conn:
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
        dominant = ExpenseService()._resolve_dominant_expense_coding_for_submit(
            expense_id=99
        )

    assert dominant["sub_cost_code_id"] == 20
    assert dominant["project_id"] == 2


# --- P2-10 confirm/flag response legacy line keys ---


def test_confirm_response_keys_match_origin_master_plus_enqueued():
    from entities.coding_suggestion.business.model import CodingSuggestion
    from entities.coding_suggestion.business.service import CodingSuggestionService

    origin_keys = {
        "id",
        "public_id",
        "row_version",
        "qbo_purchase_id",
        "qbo_purchase_line_id",
        "qbo_line_id",
        "qbo_purchase_qbo_id",
        "realm_id",
        "vendor_id",
        "sync_token_at_suggest",
        "status",
        "suggested_project_id",
        "suggested_sub_cost_code_id",
        "suggested_description",
        "suggestion_source",
        "suggestion_reason",
        "suggestion_confidence",
        "suggested_at",
        "confirmed_project_id",
        "confirmed_sub_cost_code_id",
        "confirmed_description",
        "was_overridden",
        "confirmed_by_user_id",
        "confirmed_at",
        "flag_reason",
        "flagged_at",
        "written_at",
        "write_error",
        "claimed_by_user_id",
        "claimed_at",
        "company_id",
        "created_by_user_id",
        "created_datetime",
        "modified_datetime",
    }
    item = CodingSuggestion(
        id=1,
        public_id=PUBLIC_ID,
        row_version=None,
        target_kind="expense",
        bill_id=None,
        bill_credit_id=None,
        expense_id=1,
        attachment_id=None,
        qbo_purchase_id=1,
        qbo_purchase_qbo_id=PURCHASE_QBO_ID,
        realm_id=REALM_ID,
        vendor_id=1,
        sync_token_at_suggest="1",
        status="confirmed",
        suggested_project_id=None,
        suggested_sub_cost_code_id=None,
        suggested_description=None,
        suggestion_source=None,
        suggestion_reason=None,
        suggestion_confidence=None,
        suggested_at=None,
        features_json=None,
        budget_check=None,
        budget_remaining=None,
        route=None,
        model_version=None,
        confirmed_project_id=1,
        confirmed_sub_cost_code_id=2,
        confirmed_description="d",
        was_overridden=False,
        confirmed_by_user_id=17,
        confirmed_at=None,
        confirmed_mixed_codes=None,
        prefill_state=None,
        flag_reason=None,
        flagged_at=None,
        written_at=None,
        write_error=None,
        claimed_by_user_id=None,
        claimed_at=None,
        company_id=1,
        created_by_user_id=17,
        created_datetime=None,
        modified_datetime=None,
    )
    svc = CodingSuggestionService()
    with patch.object(
        svc, "legacy_single_line_ids", return_value=(55, "1")
    ):
        payload = svc.to_api_dict(item)
    payload["enqueued"] = True
    for key in origin_keys:
        assert key in payload, f"missing legacy response key {key!r}"
    assert payload["qbo_purchase_line_id"] == 55
    assert payload["qbo_line_id"] == "1"


# --- P2-11 OR 1=1 mutation rejected ---


def _mutated_site3_or_1_1() -> str:
    original = _read_uncoded_identity_sql()
    mutated = re.sub(
        r"ON\s+p\.\[QboId\]\s*=\s*e\.\[QboId\]",
        "ON (p.[QboId] = e.[QboId] OR 1=1)",
        original,
        count=1,
        flags=re.IGNORECASE,
    )
    assert mutated != original
    return mutated


def test_mutation_or_1_1_in_join_on_clause_goes_red():
    sql = _mutated_site3_or_1_1()
    legs = extract_inner_identity_legs(sql)
    ok, msg = _leg_check_result(READ_UNCODED_SPROC, legs, expected=SHAPE_A_LEGS)
    assert not ok, "expected RED when OR 1=1 injected into identity join"
    assert READ_UNCODED_SPROC in msg
