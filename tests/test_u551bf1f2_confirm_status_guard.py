"""U-551b-f2: a NON-repoint confirm is status-guarded (service), the cockpit route 409s every refusal
(never a silent 200), and the submit loop counts a recovered ``confirmed`` first write as enqueued.

Every test names the mutant it kills. Nothing here opens a DB/HTTP connection (get_connection is blocked
by conftest; the suggestion lock is granted autouse)."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from entities.coding_suggestion.business.model import REPOINT_CONFIRMATION_SOURCE
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.expense.business.service import ExpenseService
from integrations.intuit.qbo.outbox.business.service import QboOutboxDeadLetterExistsError

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
RESOLVER = "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref"
OUTBOX = "integrations.intuit.qbo.outbox.business.service.QboOutboxService"


WRITTEN_RECORD = [{"qbo_line_id": "L1", "item_ref_value": "ITEM-A", "sub_cost_code_id": 5}]


def _item(status, source="confirm_endpoint", seq=4, *, record=False):
    # confirmed coding = _confirm()'s default caller coding (project 202, sub cost code 101)
    return SimpleNamespace(
        public_id=PUBLIC_ID, status=status, confirmation_source=source, confirmation_seq=seq,
        realm_id="realm-1", qbo_purchase_qbo_id="P-1", qbo_purchase_id=9, expense_id=None,
        written_lines=list(WRITTEN_RECORD) if record else [], written_sync_token="6" if record else None,
        repoint_targets=None, confirmed_project_id=202, confirmed_sub_cost_code_id=101,
    )


def _svc(item, *, mark_enqueued=None):
    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=item)
    svc.record_confirmation = MagicMock()
    svc.mark_enqueued = MagicMock(return_value=mark_enqueued if mark_enqueued is not None else SimpleNamespace())
    svc.mark_written = MagicMock()
    svc.mark_error = MagicMock()
    return svc


def _confirm(svc, **over):
    kw = dict(public_id=PUBLIC_ID, project_id=202, sub_cost_code_id=101, description="x",
              was_overridden=False, user_id=17)
    kw.update(over)
    return svc.confirm(**kw)


def _assert_zero_writes(svc, outbox):
    svc.record_confirmation.assert_not_called()
    svc.mark_enqueued.assert_not_called()
    svc.mark_written.assert_not_called()
    svc.mark_error.assert_not_called()
    outbox.return_value.enqueue.assert_not_called()


# ---- service: the guard ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,source,expected", [
    # kills: a dropped `written` guard (seq+1 record -> drain no_target_lines -> error)
    ("written", "confirm_endpoint", "already_written"),
    # kills: a dropped `resolved_externally` guard
    ("resolved_externally", "confirm_endpoint", "resolved_externally"),
    # kills: a missing source check on `confirmed` (clears the repoint's targets, approval dropped)
    ("confirmed", REPOINT_CONFIRMATION_SOURCE, "repoint_pending"),
    # kills: a missing source check on `enqueued`
    ("enqueued", REPOINT_CONFIRMATION_SOURCE, "repoint_pending"),
    # kills: a dropped `enqueued` first-write guard
    ("enqueued", "confirm_endpoint", "already_enqueued"),
    ("enqueued", "review_submit", "already_enqueued"),
])
@patch(GATE, return_value=None)
def test_non_repoint_confirm_refuses_with_zero_writes(_gate, status, source, expected):
    svc = _svc(_item(status, source))
    with patch(OUTBOX) as outbox, \
            patch(RESOLVER, side_effect=AssertionError("resolver must not run before the guard")), \
            patch.object(CodingSuggestionService, "_count_uncategorized_purchase_lines",
                         side_effect=AssertionError("no purchase read before the guard")):
        result = _confirm(svc)
    assert result == {"status": expected}
    _assert_zero_writes(svc, outbox)


@pytest.mark.parametrize("source", ["confirm_endpoint", "review_submit"])
@patch(GATE, return_value=None)
def test_confirmed_first_write_with_equal_coding_is_recovered_without_a_new_revision(_gate, source):
    # kills: re-record on recovery (seq bump) and an unguarded mark_enqueued (no seq fence)
    svc = _svc(_item("confirmed", source, seq=7))
    with patch(OUTBOX) as outbox, \
            patch(RESOLVER, side_effect=AssertionError("recovery never resolves a coding")):
        result = _confirm(svc)  # caller coding == persisted coding
    assert result == {"status": "enqueued", "enqueued": True, "recovered": True}
    svc.record_confirmation.assert_not_called()
    outbox.return_value.enqueue.assert_called_once_with(
        kind="recode_purchase_line", entity_type="CodingSuggestion", entity_public_id=PUBLIC_ID, realm_id="realm-1")
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=7)


def _rerecord_patches():
    return (
        patch(RESOLVER, return_value=SimpleNamespace(value="ITEM-X")),
        patch.object(CodingSuggestionService, "_count_uncategorized_purchase_lines", return_value=1),
        patch.object(CodingSuggestionService, "_resolve_expense_id_for_purchase", return_value=None),
        patch("integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository"),
    )


@patch(GATE, return_value=None)
def test_confirmed_first_write_with_different_coding_records_a_seq_fenced_new_revision(_gate):
    # kills: "recovery ships the OLD coding" (no record, recovered:True) and an UNGUARDED re-record
    # (expected_confirmation_seq dropped -> a drain/other confirm that moved the row is overwritten)
    svc = _svc(_item("confirmed", seq=7))
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=8))
    r, c, e, purchases = _rerecord_patches()
    with patch(OUTBOX) as outbox, r, c, e, purchases as pr:
        pr.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        result = _confirm(svc, project_id=303, sub_cost_code_id=404)  # differs from 202 / 101
    assert result == {"status": "enqueued", "enqueued": True}  # a NEW revision, not ``recovered``
    svc.record_confirmation.assert_called_once()
    kw = svc.record_confirmation.call_args.kwargs
    assert kw["expected_confirmation_seq"] == 7
    assert kw["confirmed_project_id"] == 303 and kw["confirmed_sub_cost_code_id"] == 404
    outbox.return_value.enqueue.assert_called_once()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=8)


@patch(GATE, return_value=None)
def test_confirmed_first_write_with_different_coding_that_loses_the_race_is_not_recorded(_gate):
    # kills: ignoring a None record_confirmation (enqueue + mark after a fenced-out write)
    svc = _svc(_item("confirmed", seq=7))
    svc.record_confirmation = MagicMock(return_value=None)
    r, c, e, purchases = _rerecord_patches()
    with patch(OUTBOX) as outbox, r, c, e, purchases as pr:
        pr.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        result = _confirm(svc, project_id=303, sub_cost_code_id=404)
    assert result == {"status": "not_recorded"}
    svc.record_confirmation.assert_called_once()
    outbox.return_value.enqueue.assert_not_called()
    svc.mark_enqueued.assert_not_called()


@patch(GATE, return_value=None)
def test_recovery_goes_through_the_shared_enqueue_and_mark_helper(_gate):
    # kills: the first-write recovery site inlining its own enqueue/mark (the two sites drifting apart)
    svc = _svc(_item("confirmed", seq=7))
    with patch.object(CodingSuggestionService, "_enqueue_recode_and_mark", return_value=True) as helper:
        result = _confirm(svc)
    assert result == {"status": "enqueued", "enqueued": True, "recovered": True}
    helper.assert_called_once_with(PUBLIC_ID, "realm-1", 7)


@patch(GATE, return_value=None)
def test_repoint_recovery_goes_through_the_shared_enqueue_and_mark_helper(_gate):
    # kills: the repoint recovery site inlining its own enqueue/mark
    item = _item("confirmed", REPOINT_CONFIRMATION_SOURCE, seq=5, record=True)
    svc = _svc(item)
    with patch.object(CodingSuggestionService, "_enqueue_recode_and_mark", return_value=False) as helper:
        result = svc.confirm(public_id=PUBLIC_ID, project_id=1, sub_cost_code_id=1, description=None,
                             was_overridden=True, user_id=1, repoint=True, repoint_targets=None,
                             expected_confirmation_seq=5)
    assert result == {"status": "already_terminal"}
    helper.assert_called_once_with(PUBLIC_ID, "realm-1", 5)
    svc.record_confirmation.assert_not_called()


@patch(GATE, return_value=None)
def test_recovery_whose_mark_is_fenced_out_reports_already_terminal(_gate):
    # kills: ignoring a None mark_enqueued (reports success for a revision that moved on)
    svc = _svc(_item("confirmed", seq=7))
    svc.mark_enqueued = MagicMock(return_value=None)
    with patch(OUTBOX):
        result = _confirm(svc)
    assert result == {"status": "already_terminal"}
    svc.record_confirmation.assert_not_called()


@pytest.mark.parametrize("status", ["pending", "suggested", "flagged", "changed_in_qbo", "error"])
@patch(GATE, return_value=None)
def test_first_confirm_and_re_review_statuses_are_unchanged(_gate, status):
    # kills: an over-broad guard that blocks a first confirm / re-review (a recordless row)
    svc = _svc(_item(status))
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=5))
    with patch(OUTBOX) as outbox, \
            patch(RESOLVER, return_value=SimpleNamespace(value="ITEM-X")), \
            patch.object(CodingSuggestionService, "_count_uncategorized_purchase_lines", return_value=1), \
            patch.object(CodingSuggestionService, "_resolve_expense_id_for_purchase", return_value=None), \
            patch("integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository") as purchases:
        purchases.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        result = _confirm(svc)
    assert result == {"status": "enqueued", "enqueued": True}
    svc.record_confirmation.assert_called_once()
    assert svc.record_confirmation.call_args.kwargs["confirmation_source"] == "confirm_endpoint"
    assert svc.record_confirmation.call_args.kwargs["expected_confirmation_seq"] is None  # unguarded
    outbox.return_value.enqueue.assert_called_once()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=5)


@pytest.mark.parametrize("status", ["pending", "changed_in_qbo", "error"])
@patch(GATE, return_value=None)
def test_a_row_holding_a_written_record_is_never_a_first_write_target(_gate, status):
    # kills: a dropped F5 check (a cockpit confirm records seq+1, clears the repoint targets, and the drain
    # finds no placeholder line -> error); a check keyed on status instead of the record
    svc = _svc(_item(status, record=True))
    with patch(OUTBOX) as outbox, \
            patch(RESOLVER, side_effect=AssertionError("resolver must not run before the guard")):
        result = _confirm(svc)
    assert result == {"status": "already_written"}
    _assert_zero_writes(svc, outbox)


@pytest.mark.parametrize("token,lines", [("6", []), (None, WRITTEN_RECORD)])
@patch(GATE, return_value=None)
def test_either_half_of_a_written_record_trips_the_written_record_guard(_gate, token, lines):
    # kills (r3, twin P3): a check that needs BOTH halves - a first write never sets the token and MarkWritten
    # CASE-preserves it, so either half alone proves our drain wrote this purchase
    item = _item("changed_in_qbo")
    item.written_sync_token, item.written_lines = token, list(lines)
    svc = _svc(item)
    with patch(OUTBOX) as outbox, \
            patch(RESOLVER, side_effect=AssertionError("resolver must not run before the guard")):
        result = _confirm(svc)
    assert result == {"status": "already_written"}
    _assert_zero_writes(svc, outbox)


@patch(GATE, return_value=None)
def test_a_confirmed_first_write_that_holds_a_written_record_is_already_written_not_recovered(_gate):
    # kills (r3, Codex P2 + twin P3): the equal-coding recovery running BEFORE the record check - a legacy
    # row (pre-f2 non-repoint confirm on a written row) would re-enqueue a FIRST write over written work
    item = _item("confirmed", record=True)
    item.confirmed_project_id, item.confirmed_sub_cost_code_id = 7, 11
    svc = _svc(item)
    with patch(OUTBOX) as outbox, \
            patch(RESOLVER, side_effect=AssertionError("resolver must not run before the guard")):
        result = _confirm(svc, project_id=7, sub_cost_code_id=11)
    assert result == {"status": "already_written"}
    _assert_zero_writes(svc, outbox)


@patch(GATE, return_value=None)
def test_repoint_confirm_is_not_subject_to_the_non_repoint_guard(_gate):
    # kills: applying the guard to repoint=True (a `written` repoint must still reach its own branch)
    item = _item("written")
    svc = _svc(item)
    with patch(RESOLVER, return_value=SimpleNamespace(value="ITEM-X")):
        result = svc.confirm(public_id=PUBLIC_ID, project_id=1, sub_cost_code_id=1, description=None,
                             was_overridden=True, user_id=1, repoint=True, repoint_targets=None,
                             expected_confirmation_seq=4)
    assert result["status"] == "no_written_record"  # its own checks, not already_written
    svc.record_confirmation.assert_not_called()


# ---- router ---------------------------------------------------------------------------------------

def _route(service):
    """Run the cockpit confirm route; returns (response_or_None, HTTPException_or_None)."""
    from entities.coding_suggestion.api.router import confirm_expense_coding_item_router
    from entities.coding_suggestion.api.schemas import ConfirmCodingSuggestionRequest

    body = ConfirmCodingSuggestionRequest(
        project_public_id="00000000-0000-0000-0000-000000000010",
        sub_cost_code_public_id="00000000-0000-0000-0000-000000000020",
        description="x", was_overridden=False,
    )
    with patch("entities.coding_suggestion.api.router.CodingSuggestionService", return_value=service), \
            patch("entities.coding_suggestion.api.router.current_user_id") as uid, \
            patch("entities.coding_suggestion.api.router._assert_can_access_coding_suggestion"), \
            patch("entities.coding_suggestion.api.router.assert_can_access_project"), \
            patch("entities.project.business.service.ProjectService") as ps, \
            patch("entities.sub_cost_code.business.service.SubCostCodeService") as scs:
        uid.get.return_value = 17
        ps.return_value.read_by_public_id.return_value = SimpleNamespace(id=10)
        scs.return_value.read_by_public_id.return_value = SimpleNamespace(id=20)
        try:
            return confirm_expense_coding_item_router(PUBLIC_ID, body, _={"can_update": True}), None
        except HTTPException as exc:
            return None, exc


def _route_service(confirm_result, *, claimed_by=None, raises=None):
    service = MagicMock()
    service.read_by_public_id.return_value = SimpleNamespace(claimed_by_user_id=claimed_by)
    service.is_multi_line_purchase.return_value = False
    service.claim.return_value = SimpleNamespace(claimed_by_user_id=17)
    if raises is not None:
        service.confirm.side_effect = raises
    else:
        service.confirm.return_value = confirm_result
    service.to_api_dict.return_value = {}
    return service


REFUSALS = ["already_written", "resolved_externally", "repoint_pending", "already_enqueued", "already_terminal"]


@pytest.mark.parametrize("refusal", REFUSALS)
def test_router_409s_each_refusal_and_releases_a_claim_taken_here(refusal):
    # kills: a refusal that falls through to 200, a wrong detail, and a leaked claim; the collapsed 409
    # still yields each of the five names
    service = _route_service({"status": refusal})
    response, err = _route(service)
    assert response is None and err.status_code == 409 and err.detail == refusal
    service.claim.assert_called_once()
    service.release.assert_called_once_with(public_id=PUBLIC_ID, user_id=17)


@pytest.mark.parametrize("refusal", REFUSALS)
def test_router_keeps_a_claim_the_caller_already_held(refusal):
    # kills: releasing a claim this request did not take
    service = _route_service({"status": refusal}, claimed_by=17)
    response, err = _route(service)
    assert err.status_code == 409 and err.detail == refusal
    service.claim.assert_not_called()
    service.release.assert_not_called()


_DEAD_LETTER = QboOutboxDeadLetterExistsError(
    entity_type="CodingSuggestion", entity_public_id=PUBLIC_ID, kind="recode_purchase_line",
    dead_letter_public_id="dl-1")

# (case id, confirm envelope, exception, expected HTTP status, expected detail)
PREEXISTING_REFUSALS = [
    ("not_found", {"status": "not_found"}, None, 404, "Expense coding item not found"),
    ("dead_letter", None, _DEAD_LETTER, 409, str(_DEAD_LETTER)),
    ("invalid", {"status": "invalid", "reason": "project and sub_cost_code required"}, None, 422,
     "project and sub_cost_code required"),
    ("mapping_missing", {"status": "mapping_missing", "reason": "SubCostCode has no QBO Item mapping"}, None, 422,
     "SubCostCode has no QBO Item mapping"),
    ("writes_disabled", {"status": "writes_disabled", "reason": "qbo_writes_disabled"}, None, 422,
     "qbo_writes_disabled"),
    ("invalid_no_reason", {"status": "invalid"}, None, 422, "Invalid confirmation request."),
]


@pytest.mark.parametrize("case,envelope,exc,http,detail", PREEXISTING_REFUSALS, ids=[c[0] for c in PREEXISTING_REFUSALS])
def test_router_releases_the_claim_taken_here_on_every_preexisting_refusal(case, envelope, exc, http, detail):
    # kills: a refusal branch that omits _release_if_claimed (the claim sticks for the 900 s reclaim window,
    # locking out the retry of a first-write ``confirmed`` whose earlier outbox row dead-lettered) and any
    # change of status / detail on those branches
    service = _route_service(envelope, raises=exc)
    response, err = _route(service)
    assert response is None and err.status_code == http and err.detail == detail
    service.claim.assert_called_once()
    service.release.assert_called_once_with(public_id=PUBLIC_ID, user_id=17)


@pytest.mark.parametrize("case,envelope,exc,http,detail", PREEXISTING_REFUSALS, ids=[c[0] for c in PREEXISTING_REFUSALS])
def test_router_does_not_release_a_claim_the_caller_already_held_on_a_preexisting_refusal(
    case, envelope, exc, http, detail
):
    # kills: an over-release (releasing a claim this request did not take)
    service = _route_service(envelope, claimed_by=17, raises=exc)
    response, err = _route(service)
    assert err.status_code == http and err.detail == detail
    service.claim.assert_not_called()
    service.release.assert_not_called()


@pytest.mark.parametrize("fabricated,detail", [
    ("brand_new_status", "brand_new_status"), ("not_recorded", "not_recorded"), (None, "confirm_refused"),
])
def test_router_unmapped_status_is_409_never_200(fabricated, detail):
    # kills: restoring the 200 fall-through (``enqueued: False``)
    service = _route_service({"status": fabricated} if fabricated else {})
    response, err = _route(service)
    assert response is None and err.status_code == 409 and err.detail == detail
    service.release.assert_called_once_with(public_id=PUBLIC_ID, user_id=17)


@pytest.mark.parametrize("recovered", [True, False])
def test_router_200_for_enqueued_reports_recovered(recovered):
    # kills: dropping the ``recovered`` payload key; releasing the claim on success
    result = {"status": "enqueued", "enqueued": True}
    if recovered:
        result["recovered"] = True
    service = _route_service(result)
    response, err = _route(service)
    assert err is None and response is not None
    payload = service.to_api_dict.return_value
    assert payload["enqueued"] is True and payload["recovered"] is recovered
    service.release.assert_not_called()


@pytest.mark.parametrize("status_value", ["brand_new_status", "not_recorded", "enqueued_later"])
def test_router_never_200s_a_non_enqueued_status_even_one_that_looks_successful(status_value):
    # kills: a 200 for any status but ``enqueued`` (a status outside the five names, carrying enqueued:True)
    service = _route_service({"status": status_value, "enqueued": True, "recovered": True})
    response, err = _route(service)
    assert response is None and err is not None and err.status_code == 409 and err.detail == status_value
    service.to_api_dict.assert_not_called()


# ---- submit loop ----------------------------------------------------------------------------------

STATE_READ = "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids"
CONFIRM = "entities.coding_suggestion.business.service.CodingSuggestionService.confirm"
DOMINANT = "entities.expense.business.service.ExpenseService._resolve_dominant_expense_coding_for_submit"
HOLDING = "entities.expense.business.service.any_holding_sub_cost_code"


def _submit(states, confirm_side_effect):
    svc = ExpenseService(repo=MagicMock())
    eli = MagicMock()
    eli.read_by_expense_id.return_value = []
    svc._expense_line_item_service = eli
    confirm = MagicMock(side_effect=confirm_side_effect)
    dominant = {"project_id": 42, "sub_cost_code_id": 481, "description": "d", "confirmed_mixed_codes": False}
    with patch(STATE_READ, return_value={55: states}), patch(CONFIRM, confirm), \
            patch(DOMINANT, return_value=dominant), patch(HOLDING, return_value=False):
        summary = svc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)
    return summary, confirm


def test_submit_loop_counts_a_recovered_confirmed_first_write_as_enqueued_and_never_repoints():
    # kills: counting `recovered` as a skip; the loop passing repoint=True
    states = [
        {"public_id": "cs-confirmed", "status": "confirmed", "confirmation_source": "review_submit"},
        {"public_id": "cs-suggested", "status": "suggested"},
        {"public_id": "cs-repoint", "status": "confirmed", "confirmation_source": REPOINT_CONFIRMATION_SOURCE},
        {"public_id": "cs-written", "status": "written"},
    ]
    results = {
        "cs-confirmed": {"status": "enqueued", "enqueued": True, "recovered": True},
        "cs-suggested": {"status": "enqueued", "enqueued": True},
    }
    summary, confirm = _submit(states, lambda **kw: results[kw["public_id"]])
    assert summary["enqueued"] == 2
    by_id = {i["public_id"]: i for i in summary["items"]}
    assert by_id["cs-confirmed"]["outcome"] == "enqueued" and by_id["cs-confirmed"]["recovered"] is True
    assert "recovered" not in by_id["cs-suggested"]
    assert by_id["cs-repoint"]["reason"] == "repoint_pending" and by_id["cs-written"]["reason"] == "terminal_status"
    assert [c.kwargs["public_id"] for c in confirm.call_args_list] == ["cs-confirmed", "cs-suggested"]
    for call in confirm.call_args_list:
        assert call.kwargs.get("repoint", False) is False
        assert "repoint_targets" not in call.kwargs and "expected_confirmation_seq" not in call.kwargs


def test_submit_loop_reports_a_service_refusal_as_a_skip_with_its_reason():
    # kills: counting an already_* refusal as enqueued
    summary, _ = _submit([{"public_id": "cs-1", "status": "suggested"}], lambda **kw: {"status": "already_written"})
    assert summary["enqueued"] == 0 and summary["skipped"] == 1
    assert summary["items"][0]["outcome"] == "skipped" and summary["items"][0]["reason"] == "already_written"


def _submit_real_confirm(item, svc_overrides=None):
    """Submit loop driving the REAL ``confirm`` against mocked persistence (only the item read, the writes and
    the outbox are mocked), so recovery is told apart from a re-record."""
    svc = _svc(item)
    svc.record_confirmation = MagicMock(return_value=SimpleNamespace(confirmation_seq=item.confirmation_seq + 1))
    states = [{"public_id": PUBLIC_ID, "status": item.status, "confirmation_source": item.confirmation_source}]
    esvc = ExpenseService(repo=MagicMock())
    eli = MagicMock()
    eli.read_by_expense_id.return_value = []
    esvc._expense_line_item_service = eli
    dominant = {"project_id": 202, "sub_cost_code_id": 101, "description": "d", "confirmed_mixed_codes": False}
    with patch(STATE_READ, return_value={55: states}), \
            patch("entities.coding_suggestion.business.service.CodingSuggestionService", return_value=svc), \
            patch(GATE, return_value=None), patch(OUTBOX) as outbox, \
            patch(DOMINANT, return_value=dominant), patch(HOLDING, return_value=False), \
            patch(RESOLVER, return_value=SimpleNamespace(value="ITEM-X")), \
            patch.object(CodingSuggestionService, "_count_uncategorized_purchase_lines", return_value=1), \
            patch.object(CodingSuggestionService, "_resolve_expense_id_for_purchase", return_value=None), \
            patch("integrations.intuit.qbo.purchase.persistence.repo.QboPurchaseRepository") as purchases:
        purchases.return_value.read_by_id.return_value = SimpleNamespace(sync_token="3")
        summary = esvc.enqueue_coding_recode_on_submit(expense_id=55, user_id=17)
    return summary, svc, outbox


def test_submit_loop_real_confirm_recovers_a_confirmed_row_with_the_same_coding_without_re_recording():
    # kills: a loop/confirm pair that re-records a stranded ``confirmed`` row (seq bump) instead of recovering it
    summary, svc, outbox = _submit_real_confirm(_item("confirmed", "review_submit", seq=7))
    assert summary["enqueued"] == 1
    assert summary["items"][0]["outcome"] == "enqueued" and summary["items"][0]["recovered"] is True
    svc.record_confirmation.assert_not_called()
    outbox.return_value.enqueue.assert_called_once()
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=7)


def test_submit_loop_real_confirm_records_a_fenced_new_revision_when_the_dominant_coding_differs():
    # kills: recovery shipping the OLD coding when submit's dominant coding changed (no record, recovered:True)
    item = _item("confirmed", "review_submit", seq=7)
    item.confirmed_project_id, item.confirmed_sub_cost_code_id = 999, 998
    summary, svc, outbox = _submit_real_confirm(item)
    assert summary["enqueued"] == 1 and "recovered" not in summary["items"][0]
    svc.record_confirmation.assert_called_once()
    kw = svc.record_confirmation.call_args.kwargs
    assert kw["expected_confirmation_seq"] == 7 and kw["confirmed_sub_cost_code_id"] == 101
    svc.mark_enqueued.assert_called_once_with(PUBLIC_ID, expected_confirmation_seq=8)
