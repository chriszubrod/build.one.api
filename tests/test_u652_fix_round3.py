"""U-652 fix round 3 — regression tests for the second correctness review.

1. Multi-line submit must still enqueue its recode (the refusal belongs to the confirm endpoint only).
2. Single-line recode seeds billability from the project actually written (confirmed_*), multi-line
   from each line's own project; a human decision still wins.
3. The confirm endpoint refuses a multi-line purchase BEFORE claiming (no write at all).
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker
from tests.test_u652_fix_round2 import PUBLIC_ID, _make_item, _make_outbox

_GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
_ITEM_REF = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
_OUTBOX = "integrations.intuit.qbo.outbox.business.service.QboOutboxService"


# --- 1. submit path enqueues a multi-line purchase -------------------------------------------


@patch(_GATE, return_value=None)
@patch(_ITEM_REF, return_value=SimpleNamespace(value="ITEM-X", name="Item X"))
def test_confirm_from_submit_enqueues_multi_line_purchase(_ref, _gate):
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=_make_item())
    svc.record_confirmation = MagicMock()
    svc.mark_enqueued = MagicMock()
    with patch.object(CodingSuggestionService, "_count_uncategorized_purchase_lines", return_value=2), \
         patch(_OUTBOX) as outbox:
        result = svc.confirm(
            public_id=PUBLIC_ID, project_id=202, sub_cost_code_id=101, description="x",
            was_overridden=False, user_id=17, confirmed_mixed_codes=True, allow_multi_line=True,
        )
    assert result == {"status": "enqueued", "enqueued": True}
    svc.record_confirmation.assert_called_once()
    outbox.return_value.enqueue.assert_called_once()
    assert outbox.return_value.enqueue.call_args.kwargs["entity_type"] == "CodingSuggestion"


@patch(_GATE, return_value=None)
@patch(_ITEM_REF, return_value=SimpleNamespace(value="ITEM-X", name="Item X"))
def test_confirm_default_still_refuses_multi_line(_ref, _gate):
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=_make_item())
    svc.record_confirmation = MagicMock()
    with patch.object(CodingSuggestionService, "_count_uncategorized_purchase_lines", return_value=2):
        result = svc.confirm(
            public_id=PUBLIC_ID, project_id=202, sub_cost_code_id=101, description="x",
            was_overridden=False, user_id=17,
        )
    assert result["status"] == "multi_line_unsupported"
    svc.record_confirmation.assert_not_called()


def test_submit_path_passes_allow_multi_line():
    """The submit path is the one place a multi-line purchase may be confirmed."""
    import inspect

    from entities.expense.business.service import ExpenseService

    src = inspect.getsource(ExpenseService.enqueue_coding_recode_on_submit)
    assert "allow_multi_line=True" in src


# --- 2. billability from the project actually written ----------------------------------------


def _projects(cost_plus_by_id):
    def read_by_id(project_id):
        cp = cost_plus_by_id.get(project_id)
        return None if cp is None else SimpleNamespace(is_cost_plus=cp)

    return read_by_id


@pytest.mark.parametrize("confirmed_cp,line_cp,expected", [(True, False, True), (False, True, False)])
def test_single_line_billability_follows_confirmed_project(confirmed_cp, line_cp, expected):
    item = _make_item(confirmed_project_id=900, confirmed_sub_cost_code_id=901)
    local_line = SimpleNamespace(
        sub_cost_code_id=481, project_id=77, description="eli", quantity=Decimal("1"),
        rate=Decimal("10"), amount=Decimal("10"), realm_id=item.realm_id,
        billable_decided_at=None, is_billable=not expected,
    )
    worker = QboOutboxWorker()
    with patch.object(worker, "_read_uncategorized_staging_line_ids", return_value=["1"]), \
         patch.object(worker, "_resolve_recode_line_economics", return_value=(local_line, None)), \
         patch("entities.project.business.service.ProjectService") as ps:
        ps.return_value.read_by_id.side_effect = _projects({900: confirmed_cp, 77: line_cp})
        targets, err = worker._resolve_recode_purchase_line_targets(_make_outbox(), item)
    assert err is None
    assert targets[0]["project_id"] == 900
    assert targets[0]["is_billable"] is expected


def test_multi_line_billability_follows_each_lines_project():
    item = _make_item(confirmed_project_id=900)
    item.confirmation_source = "review_submit"  # only review-submit may write per-line coding
    lines = {
        "1": SimpleNamespace(sub_cost_code_id=11, project_id=100, description="a", quantity=None,
                             rate=None, amount=Decimal("50"), realm_id=item.realm_id,
                             billable_decided_at=None, is_billable=False),
        "2": SimpleNamespace(sub_cost_code_id=12, project_id=200, description="b", quantity=None,
                             rate=None, amount=Decimal("40"), realm_id=item.realm_id,
                             billable_decided_at=None, is_billable=True),
    }
    worker = QboOutboxWorker()
    with patch.object(worker, "_read_uncategorized_staging_line_ids", return_value=["1", "2"]), \
         patch.object(worker, "_resolve_recode_line_economics",
                      side_effect=lambda row, it, target_qbo_line_id: (lines[target_qbo_line_id], None)), \
         patch("entities.project.business.service.ProjectService") as ps:
        ps.return_value.read_by_id.side_effect = _projects({900: False, 100: True, 200: False})
        targets, err = worker._resolve_recode_purchase_line_targets(_make_outbox(), item)
    assert err is None
    by_line = {t["target_qbo_line_id"]: t for t in targets}
    assert by_line["1"]["is_billable"] is True   # project 100 cost-plus
    assert by_line["2"]["is_billable"] is False  # project 200 fixed price


def test_human_billable_decision_wins_over_written_project():
    item = _make_item(confirmed_project_id=900)
    local_line = SimpleNamespace(project_id=77, is_billable=False, billable_decided_at="2026-10-01")
    with patch("entities.project.business.service.ProjectService") as ps:
        ps.return_value.read_by_id.return_value = SimpleNamespace(is_cost_plus=True)
        assert QboOutboxWorker._recode_is_billable(item, local_line, project_id=900) is False


# --- 3. the confirm endpoint refuses multi-line BEFORE claiming ------------------------------


def test_confirm_router_refuses_multi_line_before_claim():
    from entities.coding_suggestion.api.router import confirm_expense_coding_item_router
    from entities.coding_suggestion.api.schemas import ConfirmCodingSuggestionRequest

    service = MagicMock()
    service.read_by_public_id.return_value = SimpleNamespace(claimed_by_user_id=None)
    service.is_multi_line_purchase.return_value = True
    body = ConfirmCodingSuggestionRequest(
        project_public_id="00000000-0000-0000-0000-000000000010",
        sub_cost_code_public_id="00000000-0000-0000-0000-000000000020",
        description="x", was_overridden=False,
    )
    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), \
         patch("entities.coding_suggestion.api.router.current_user_id") as uid:
        uid.get.return_value = 17
        with pytest.raises(HTTPException) as exc:
            confirm_expense_coding_item_router(PUBLIC_ID, body, _={"can_update": True})
    assert exc.value.status_code == 409
    assert exc.value.detail == "multi_line_purchase_confirm_unsupported"
    service.claim.assert_not_called()
    service.confirm.assert_not_called()


def _route_with(service):
    from entities.coding_suggestion.api.router import confirm_expense_coding_item_router
    from entities.coding_suggestion.api.schemas import ConfirmCodingSuggestionRequest

    body = ConfirmCodingSuggestionRequest(
        project_public_id="00000000-0000-0000-0000-000000000010",
        sub_cost_code_public_id="00000000-0000-0000-0000-000000000020",
        description="x", was_overridden=False,
    )
    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), \
         patch("entities.coding_suggestion.api.router.current_user_id") as uid, \
         patch("entities.project.business.service.ProjectService") as ps, \
         patch("entities.sub_cost_code.business.service.SubCostCodeService") as scs:
        uid.get.return_value = 17
        ps.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        scs.return_value.read_by_public_id.return_value = SimpleNamespace(id=20)
        with pytest.raises(HTTPException) as exc:
            confirm_expense_coding_item_router(PUBLIC_ID, body, _={"can_update": True})
    return exc.value


def test_confirm_router_releases_its_own_claim_when_a_line_appears_mid_request():
    """Race: one line at the pre-check, a concurrent pull adds a second before confirm recounts.
    The claim this request took must be released, so nobody is locked out by a refusal."""
    service = MagicMock()
    service.read_by_public_id.return_value = SimpleNamespace(claimed_by_user_id=None)
    service.is_multi_line_purchase.return_value = False
    service.claim.return_value = SimpleNamespace(claimed_by_user_id=17)
    service.confirm.return_value = {
        "status": "multi_line_unsupported", "reason": "multi_line_purchase_confirm_unsupported",
    }
    err = _route_with(service)
    assert err.status_code == 409
    service.claim.assert_called_once()
    service.release.assert_called_once_with(public_id=PUBLIC_ID, user_id=17)


def test_confirm_router_keeps_a_claim_it_already_held():
    """If the caller held the claim before this request, a refusal must not drop it."""
    service = MagicMock()
    service.read_by_public_id.return_value = SimpleNamespace(claimed_by_user_id=17)
    service.is_multi_line_purchase.return_value = False
    service.confirm.return_value = {
        "status": "multi_line_unsupported", "reason": "multi_line_purchase_confirm_unsupported",
    }
    err = _route_with(service)
    assert err.status_code == 409
    service.claim.assert_not_called()
    service.release.assert_not_called()


# --- round 4: the worker writes per-line coding ONLY for a review-submit confirmation --------


@pytest.mark.parametrize("source", ["confirm_endpoint", None])
def test_worker_refuses_multi_line_unless_confirmed_by_review_submit(source):
    """Race: the confirm endpoint saw one line, a pull added a second before the token snapshot.
    The worker must not swap the user's confirmed code for the lines' stored coding."""
    item = _make_item(confirmed_project_id=900, confirmed_sub_cost_code_id=901)
    item.confirmation_source = source
    worker = QboOutboxWorker()
    with patch.object(worker, "_read_uncategorized_staging_line_ids", return_value=["1", "2"]), \
         patch.object(worker, "_resolve_recode_line_economics") as econ:
        targets, err = worker._resolve_recode_purchase_line_targets(_make_outbox(), item)
    assert targets is None
    assert err == "confirmation_scope_changed"
    econ.assert_not_called()


def test_worker_marks_scope_change_changed_in_qbo_without_writing():
    item = _make_item()
    item.confirmation_source = "confirm_endpoint"
    item.confirmation_seq = 2
    worker = QboOutboxWorker()
    with patch("entities.coding_suggestion.business.service.CodingSuggestionService") as svc_cls, \
         patch("integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector") as conn_cls, \
         patch.object(worker, "_read_uncategorized_staging_line_ids", return_value=["1", "2"]):
        svc_cls.return_value.read_by_public_id.return_value = item
        worker._handle_recode_purchase_line(_make_outbox())
    svc_cls.return_value.mark_changed_in_qbo.assert_called_once_with(
        PUBLIC_ID, expected_confirmation_seq=2
    )
    svc_cls.return_value.mark_written.assert_not_called()
    conn_cls.return_value.recode_purchase_lines.assert_not_called()


@pytest.mark.parametrize("allow_multi_line,expected", [(True, "review_submit"), (False, "confirm_endpoint")])
@patch(_GATE, return_value=None)
@patch(_ITEM_REF, return_value=SimpleNamespace(value="ITEM-X", name="Item X"))
def test_confirm_records_its_confirmation_source(_ref, _gate, allow_multi_line, expected):
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=_make_item())
    svc.record_confirmation = MagicMock()
    svc.mark_enqueued = MagicMock()
    with patch.object(CodingSuggestionService, "_count_uncategorized_purchase_lines", return_value=1), \
         patch(_OUTBOX):
        svc.confirm(public_id=PUBLIC_ID, project_id=202, sub_cost_code_id=101, description="x",
                    was_overridden=False, user_id=17, allow_multi_line=allow_multi_line)
    assert svc.record_confirmation.call_args.kwargs["confirmation_source"] == expected


# --- U-652c: stale ConfirmationSeq guard skips without reread/re-apply -----------------------


def test_scope_change_guard_miss_does_not_reread_or_reapply():
    """A newer confirmation owns the row; the stale job logs and exits."""
    import logging

    item = _make_item()
    item.confirmation_source = "confirm_endpoint"
    item.confirmation_seq = 1
    worker = QboOutboxWorker()
    with patch("entities.coding_suggestion.business.service.CodingSuggestionService") as svc_cls, \
         patch("integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector") as conn_cls, \
         patch.object(worker, "_read_uncategorized_staging_line_ids", return_value=["1", "2"]), \
         patch.object(worker, "_resolve_recode_purchase_line_targets", return_value=(None, "confirmation_scope_changed")):
        svc = svc_cls.return_value
        svc.read_by_public_id.return_value = item
        svc.mark_changed_in_qbo.return_value = None
        with patch.object(logging.getLogger("integrations.intuit.qbo.outbox.business.worker"), "info") as log_info:
            worker._handle_recode_purchase_line(_make_outbox())
    svc.mark_changed_in_qbo.assert_called_once_with(
        PUBLIC_ID, expected_confirmation_seq=1
    )
    assert svc.read_by_public_id.call_count == 1
    conn_cls.return_value.recode_purchase_lines.assert_not_called()
    assert log_info.called
