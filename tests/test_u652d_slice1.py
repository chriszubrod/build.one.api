"""U-652d slice 1: per-call memoization in the recode paths, no cross-call state, error ordering kept,
and MarkCodingSuggestionChangedInQbo without the retired @ExpectedRowVersion."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.outbox.business.model import QboOutbox
from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
    PurchaseChangedInQboError,
    PurchaseRecodeMappingError,
)
from tests.test_expense_recode import (
    FAKE_CUSTOMER_REF,
    FAKE_ITEM_REF,
    PURCHASE_QBO_ID,
    REALM_ID,
    SYNC_TOKEN,
    _build_connector,
    _make_categorize_line_dict,
    _make_raw_purchase,
    _patch_raw_client,
)
from tests.test_u551bf1_coding_suggestion_sql import _proc

_EXP = "entities.expense.business.service.ExpenseService"
_ELI = "entities.expense_line_item.business.service.ExpenseLineItemService"
_PROJ = "entities.project.business.service.ProjectService"


def _outbox():
    return QboOutbox(
        id=1, public_id="o-1", row_version="a", kind="recode_purchase_line",
        entity_type="CodingSuggestion", entity_public_id="11111111-1111-1111-1111-111111111111",
        realm_id=REALM_ID, request_id="r", status="processing", attempts=0,
    )


def _item():
    return SimpleNamespace(
        qbo_purchase_qbo_id="P-1", realm_id=REALM_ID, confirmation_source="review_submit",
        confirmed_project_id=None, confirmed_sub_cost_code_id=None, confirmed_description=None,
    )


def _local_line(project_id=900, sub=901):
    return SimpleNamespace(
        id=5, realm_id=None, sub_cost_code_id=sub, project_id=project_id, description="d",
        quantity=1, rate=Decimal("1"), amount=Decimal("1"), billable_decided_at=None, is_billable=False,
    )


def _resolve(worker, lines=("1", "2", "3")):
    with patch.object(worker, "_read_uncategorized_staging_line_ids", return_value=list(lines)):
        return worker._resolve_recode_purchase_line_targets(_outbox(), _item())


# ---- (a) worker memo ----

def test_worker_reads_expense_and_project_once_across_three_lines():
    with patch(_EXP) as exp, patch(_ELI) as eli, patch(_PROJ) as proj:
        exp.return_value.read_by_qbo_identity.return_value = SimpleNamespace(id=7)
        eli.return_value.read_by_qbo_identity.side_effect = lambda *_a: _local_line()
        proj.return_value.read_by_id.return_value = SimpleNamespace(is_cost_plus=True)
        targets, err = _resolve(QboOutboxWorker())
    assert err is None and len(targets) == 3
    assert all(t["is_billable"] is True for t in targets)
    exp.return_value.read_by_qbo_identity.assert_called_once_with("P-1", REALM_ID)
    proj.return_value.read_by_id.assert_called_once_with(900)
    assert eli.return_value.read_by_qbo_identity.call_count == 3


def test_worker_reads_project_once_per_distinct_id():
    projects = iter([900, 900, 901])
    with patch(_EXP) as exp, patch(_ELI) as eli, patch(_PROJ) as proj:
        exp.return_value.read_by_qbo_identity.return_value = SimpleNamespace(id=7)
        eli.return_value.read_by_qbo_identity.side_effect = lambda *_a: _local_line(project_id=next(projects))
        proj.return_value.read_by_id.return_value = SimpleNamespace(is_cost_plus=False)
        _resolve(QboOutboxWorker())
    assert sorted(c.args[0] for c in proj.return_value.read_by_id.call_args_list) == [900, 901]


def test_worker_cache_does_not_cross_calls():
    with patch(_EXP) as exp, patch(_ELI) as eli, patch(_PROJ) as proj:
        exp.return_value.read_by_qbo_identity.return_value = SimpleNamespace(id=7)
        eli.return_value.read_by_qbo_identity.side_effect = lambda *_a: _local_line()
        proj.return_value.read_by_id.return_value = SimpleNamespace(is_cost_plus=True)
        worker = QboOutboxWorker()
        _resolve(worker)
        _resolve(worker)
    assert exp.return_value.read_by_qbo_identity.call_count == 2
    assert proj.return_value.read_by_id.call_count == 2


def test_resolve_without_cache_reads_every_time():
    with patch(_EXP) as exp, patch(_ELI) as eli:
        exp.return_value.read_by_qbo_identity.return_value = SimpleNamespace(id=7)
        eli.return_value.read_by_qbo_identity.return_value = _local_line()
        worker = QboOutboxWorker()
        for _ in range(2):
            worker._resolve_recode_line_economics(_outbox(), _item(), target_qbo_line_id="1")
    assert exp.return_value.read_by_qbo_identity.call_count == 2


# ---- (c) failed lookups are not cached ----

def test_worker_failed_expense_lookup_is_not_cached():
    with patch(_EXP) as exp, patch(_ELI) as eli, patch(_PROJ):
        exp.return_value.read_by_qbo_identity.side_effect = [
            RuntimeError("db down"), SimpleNamespace(id=7), SimpleNamespace(id=7),
        ]
        eli.return_value.read_by_qbo_identity.side_effect = lambda *_a: _local_line()
        targets, err = _resolve(QboOutboxWorker())
    # line 1 failed to resolve (multi-line -> unmapped); lines 2 and 3 retried the lookup
    assert targets is None and err == QboOutboxWorker._MULTI_LINE_UNMAPPED_ERROR
    assert exp.return_value.read_by_qbo_identity.call_count == 2


def test_worker_failed_project_lookup_is_not_cached():
    item, line = _item(), _local_line()
    cache: dict = {}
    with patch(_PROJ) as proj:
        proj.return_value.read_by_id.side_effect = [RuntimeError("x"), SimpleNamespace(is_cost_plus=True)]
        first = QboOutboxWorker._recode_is_billable(item, line, project_id=900, project_cache=cache)
        second = QboOutboxWorker._recode_is_billable(item, line, project_id=900, project_cache=cache)
    assert first is False and second is True
    assert proj.return_value.read_by_id.call_count == 2


# ---- (b) connector memo ----

def _three_placeholder_purchase():
    return _make_raw_purchase(
        lines=[_make_categorize_line_dict(line_id=str(i), line_num=i) for i in (1, 2, 3)]
    )


def _specs(subs, projects):
    return [
        {"target_qbo_line_id": str(i + 1), "sub_cost_code_id": s, "project_id": p, "description": "d"}
        for i, (s, p) in enumerate(zip(subs, projects))
    ]


def _recode(connector, lines, raw):
    client_patch, mock_client = _patch_raw_client(fresh=raw)
    with client_patch:
        result = connector.recode_purchase_lines(
            realm_id=REALM_ID, qbo_purchase_qbo_id=PURCHASE_QBO_ID, lines=lines,
            expected_sync_token=SYNC_TOKEN,
        )
    return result, mock_client


def test_connector_looks_up_item_and_customer_once_per_distinct_id():
    connector = _build_connector()
    result, _ = _recode(connector, _specs([7, 7, 7], [9, 9, 9]), _three_placeholder_purchase())
    assert result["status"] == "written"
    connector._get_qbo_item_ref.assert_called_once_with(7, REALM_ID)
    connector._get_qbo_customer_ref.assert_called_once_with(9)

    connector = _build_connector()
    _recode(connector, _specs([7, 8, 7], [9, 10, 9]), _three_placeholder_purchase())
    assert sorted(c.args[0] for c in connector._get_qbo_item_ref.call_args_list) == [7, 8]
    assert sorted(c.args[0] for c in connector._get_qbo_customer_ref.call_args_list) == [9, 10]


def test_connector_failed_lookup_is_not_cached_across_calls():
    connector = _build_connector()
    connector._get_qbo_item_ref.side_effect = [RuntimeError("boom"), FAKE_ITEM_REF]
    lines = _specs([7], [None])
    with pytest.raises(RuntimeError):
        _recode(connector, lines, _three_placeholder_purchase())
    result, _ = _recode(connector, lines, _three_placeholder_purchase())
    assert result["status"] == "written"
    assert connector._get_qbo_item_ref.call_count == 2


def test_connector_failed_customer_lookup_is_not_cached():
    connector = _build_connector()
    connector._get_qbo_customer_ref.side_effect = [RuntimeError("boom"), FAKE_CUSTOMER_REF]
    lines = _specs([7], [9])
    with pytest.raises(RuntimeError):
        _recode(connector, lines, _three_placeholder_purchase())
    result, _ = _recode(connector, lines, _three_placeholder_purchase())
    assert result["status"] == "written"
    assert connector._get_qbo_customer_ref.call_count == 2


# ---- (d) error ordering ----

def test_missing_line_still_wins_over_mapping_failure():
    connector = _build_connector(item_ref=None)
    lines = [{"target_qbo_line_id": "99", "sub_cost_code_id": 7, "project_id": None, "description": "d"}]
    result, mock_client = _recode(connector, lines, _three_placeholder_purchase())
    assert result["status"] == "line_not_found"
    mock_client.update_purchase_raw.assert_not_called()
    connector._get_qbo_item_ref.assert_not_called()


def test_mapping_failure_still_raised_for_present_line():
    connector = _build_connector(item_ref=None)
    with pytest.raises(PurchaseRecodeMappingError):
        _recode(connector, _specs([7], [None]), _three_placeholder_purchase())


def test_changed_in_qbo_still_precedes_mapping_failure():
    connector = _build_connector(item_ref=None)
    raw = _make_raw_purchase(
        lines=[{
            "Id": "1", "LineNum": 1, "Amount": "1.00", "DetailType": "ItemBasedExpenseLineDetail",
            "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "foreign", "name": "x"}},
        }]
    )
    with pytest.raises(PurchaseChangedInQboError):
        _recode(connector, _specs([7], [None]), raw)


def test_single_line_wrapper_is_gone():
    assert not hasattr(type(_build_connector()), "recode_purchase_line")


# ---- (e) SQL ----

def test_changed_in_qbo_has_no_expected_row_version():
    assert "ExpectedRowVersion" not in _proc("MarkCodingSuggestionChangedInQbo")
    assert "@ExpectedConfirmationSeq" in _proc("MarkCodingSuggestionChangedInQbo")
    assert "@SyncToken" in _proc("MarkCodingSuggestionWritten")


# --- (7) submit: a coding item without a public_id is skipped, never confirmed -------------------

def _submit_with_states(monkeypatch, states):
    from entities.expense.business.service import ExpenseService

    eli = MagicMock()
    eli.read_by_expense_id.return_value = []
    monkeypatch.setattr(
        "entities.expense_line_item.business.service.ExpenseLineItemService", lambda *a, **k: eli
    )
    svc = ExpenseService()
    with patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={55: states},
    ), patch.object(
        ExpenseService, "_resolve_dominant_expense_coding_for_submit",
        return_value={"project_id": 77, "sub_cost_code_id": 481, "description": None, "confirmed_mixed_codes": False},
    ), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService.confirm",
        return_value={"status": "writes_disabled", "reason": "recode_writes_disabled"},
    ) as confirm:
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)
    return summary, confirm


def test_submit_skips_a_coding_item_without_public_id(monkeypatch):
    summary, confirm = _submit_with_states(
        monkeypatch,
        [{"public_id": None, "status": "pending"}, {"public_id": "ci-1", "status": "pending"}],
    )
    assert [c.kwargs["public_id"] for c in confirm.call_args_list] == ["ci-1"]


def test_submit_with_only_id_less_items_reports_no_coding_items(monkeypatch):
    summary, confirm = _submit_with_states(monkeypatch, [{"public_id": "", "status": "pending"}])
    confirm.assert_not_called()
    assert summary["skipped"] == 1 and summary["items"] == [{"reason": "no_coding_items"}]
