"""U-551b-f1-f9: ONE coding-suggestion status policy (model.py) used by every site that used to respell it.

Every test names the mutant it kills. No DB/HTTP: repositories and services are stubbed."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.coding_suggestion.business.model import (
    IN_FLIGHT_STATUSES,
    REPOINT_CANDIDATE_STATUSES,
    REPOINT_CONFIRMATION_SOURCE,
    TERMINAL_STATUSES,
    has_written_record,
    is_repoint_pending,
)
from entities.coding_suggestion.business.service import CodingSuggestionService
from entities.expense.api.router import EXPENSE_CODING_TERMINAL_STATUSES
from entities.expense.business.service import ExpenseService
from tests.test_u551bf1_complete_repoint import _svc as _complete_svc
from tests.test_u551bf1_repoint_worker import _item as _worker_item, _run as _worker_run

PUBLIC_ID = "11111111-1111-1111-1111-111111111111"
STATE_READ = "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids"
CONFIRM = "entities.coding_suggestion.business.service.CodingSuggestionService.confirm"
DOMINANT = "entities.expense.business.service.ExpenseService._resolve_dominant_expense_coding_for_submit"


# ---- (a) the policy itself ------------------------------------------------------------------------

def test_status_sets_have_exact_membership():
    # kills: adding/dropping a member of either set (e.g. resolved_externally out of TERMINAL, error into IN_FLIGHT)
    assert TERMINAL_STATUSES == frozenset({"written", "resolved_externally"})
    assert IN_FLIGHT_STATUSES == frozenset({"confirmed", "enqueued"})
    assert REPOINT_CANDIDATE_STATUSES == frozenset({"written", "confirmed", "enqueued"})
    assert isinstance(TERMINAL_STATUSES, frozenset) and isinstance(IN_FLIGHT_STATUSES, frozenset)


def test_read_model_terminal_set_stays_narrower():
    # kills: widening the read model's set (the cockpit must keep showing resolved_externally rows)
    assert EXPENSE_CODING_TERMINAL_STATUSES == frozenset({"written"})
    assert EXPENSE_CODING_TERMINAL_STATUSES < TERMINAL_STATUSES


@pytest.mark.parametrize("status", ["pending", "suggested", "flagged", "changed_in_qbo", "error",
                                    "written", "resolved_externally", "confirmed", "enqueued", None])
@pytest.mark.parametrize("source", [None, "confirm_endpoint", REPOINT_CONFIRMATION_SOURCE])
def test_is_repoint_pending_truth_table(status, source):
    # kills: dropping the source test, dropping the status test, or either in-flight member
    expected = status in ("confirmed", "enqueued") and source == "complete_repoint"
    assert is_repoint_pending(status, source) is expected


@pytest.mark.parametrize("token,lines,expected", [
    (None, None, False), (None, [], False), ("", [], False),
    ("6", None, True), (None, [{"qbo_line_id": "L1"}], True), ("6", [{"qbo_line_id": "L1"}], True),
])
def test_has_written_record_truth_table(token, lines, expected):
    # kills: `and` for `or` (either half counts), dropping one half
    item = SimpleNamespace(written_sync_token=token, written_lines=lines)
    assert has_written_record(item) is expected


def test_has_written_record_tolerates_missing_attributes():
    # kills: direct attribute access (AttributeError on an item without the fields)
    assert has_written_record(SimpleNamespace()) is False
    assert has_written_record(SimpleNamespace(written_sync_token="6")) is True


# ---- (b) call sites use the policy ----------------------------------------------------------------

def _guard_item(status, source="confirm_endpoint", *, token=None, lines=None):
    return SimpleNamespace(
        public_id=PUBLIC_ID, status=status, confirmation_source=source, confirmation_seq=4, realm_id="r",
        qbo_purchase_qbo_id="P-1", qbo_purchase_id=9, expense_id=None, written_lines=lines or [],
        written_sync_token=token, repoint_targets=None, confirmed_project_id=202, confirmed_sub_cost_code_id=101)


@pytest.mark.parametrize("status,source,token,lines,expected", [
    ("written", "confirm_endpoint", None, None, "already_written"),
    ("resolved_externally", "confirm_endpoint", None, None, "resolved_externally"),
    ("confirmed", REPOINT_CONFIRMATION_SOURCE, None, None, "repoint_pending"),
    ("enqueued", REPOINT_CONFIRMATION_SOURCE, None, None, "repoint_pending"),
    ("enqueued", "confirm_endpoint", None, None, "already_enqueued"),
    # kills: guard ignoring the token half / the lines half of the written record
    ("error", "confirm_endpoint", "6", None, "already_written"),
    ("error", "confirm_endpoint", None, [{"qbo_line_id": "L1"}], "already_written"),
])
def test_confirm_guard_uses_policy(status, source, token, lines, expected):
    # kills: a member dropped from IN_FLIGHT/TERMINAL, or the guard's reason strings merged
    svc = CodingSuggestionService()
    out = svc._non_repoint_status_guard(
        _guard_item(status, source, token=token, lines=lines), PUBLIC_ID, project_id=202, sub_cost_code_id=101)
    assert out is not None and out["status"] == expected


@pytest.fixture(autouse=True)
def _no_expense_line_item_db(monkeypatch):
    eli = MagicMock()
    eli.read_by_expense_id.return_value = []
    monkeypatch.setattr("entities.expense_line_item.business.service.ExpenseLineItemService", lambda *a, **k: eli)


def _submit(status, source=None):
    state = {"public_id": "ci-1", "status": status, "confirmation_source": source,
             "suggested_project_id": 10, "suggested_sub_cost_code_id": 20}
    with patch(STATE_READ, return_value={55: [state]}), patch(
        DOMINANT, return_value={"project_id": 77, "sub_cost_code_id": 481, "description": "x",
                                "confirmed_mixed_codes": False}), patch(CONFIRM) as confirm:
        summary = ExpenseService().enqueue_coding_recode_on_submit(expense_id=55, user_id=17)
    return summary, confirm


@pytest.mark.parametrize("status,source,reason", [
    ("written", None, "terminal_status"),
    ("resolved_externally", None, "terminal_status"),
    ("confirmed", REPOINT_CONFIRMATION_SOURCE, "repoint_pending"),
    ("enqueued", REPOINT_CONFIRMATION_SOURCE, "repoint_pending"),
    ("enqueued", "review_submit", "already_enqueued"),
])
def test_submit_loop_prefilter_uses_policy(status, source, reason):
    # kills: a member removed from TERMINAL_STATUSES / IN_FLIGHT_STATUSES, or the submit pre-filter ignoring the source
    summary, confirm = _submit(status, source)
    confirm.assert_not_called()
    assert summary["items"][0]["reason"] == reason


def test_submit_loop_still_confirms_a_confirmed_non_repoint_row():
    # kills: IN_FLIGHT growing "confirmed" into the submit skip (a stranded first write must be recovered)
    summary, confirm = _submit("confirmed", "review_submit")
    confirm.assert_called_once()


def _candidate_reads(state_status, source=None):
    state = {"public_id": "cs-1", "status": state_status}
    full = SimpleNamespace(status=state_status, written_sync_token="6", confirmation_source=source, confirmation_seq=3,
                           written_lines=[{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5}])
    full_read = MagicMock(return_value=full)
    with patch(STATE_READ, return_value={55: [state]}), \
            patch("entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_by_public_id", full_read), \
            patch("entities.expense.business.service.ExpenseService._expense_has_genuine_uncoded_staged_lines",
                  return_value=False), \
            patch("integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref",
                  side_effect=lambda *a, **k: SimpleNamespace(value="item-B")), \
            patch(CONFIRM, return_value={"status": "enqueued", "enqueued": True}):
        svc = _complete_svc()
        svc._enqueue_coding_recode_on_complete(expense_id=55, qbo_linked=True)
    return full_read


@pytest.mark.parametrize("status,source,is_candidate", [
    ("written", None, True),
    ("confirmed", REPOINT_CONFIRMATION_SOURCE, True),
    ("enqueued", REPOINT_CONFIRMATION_SOURCE, True),
    ("resolved_externally", None, False),
    ("error", None, False),
])
def test_completion_candidates_use_policy(status, source, is_candidate):
    # kills: resolved_externally becoming a candidate; written or an in-flight member dropped from the candidate filter
    assert _candidate_reads(status, source).called is is_candidate


@pytest.mark.parametrize("status,source,probed", [
    ("written", None, True),
    ("enqueued", REPOINT_CONFIRMATION_SOURCE, True),
    ("enqueued", "confirm_endpoint", False),
    ("resolved_externally", None, False),
])
def test_completion_probe_uses_policy(status, source, probed):
    # kills: the probe's `pending` test ignoring the source or the in-flight set (non-repoint in-flight must return None)
    svc = _complete_svc()
    full = SimpleNamespace(status=status, confirmation_source=source, written_sync_token="6", confirmation_seq=3,
                           written_lines=[{"qbo_line_id": "L1", "item_ref_value": "item-A", "sub_cost_code_id": 5}])
    repo = MagicMock()
    repo.read_by_public_id.return_value = full
    svc._written_record_is_malformed = MagicMock(return_value=False)
    with patch("integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref",
               side_effect=lambda *a, **k: SimpleNamespace(value="item-B")):
        out = svc._repoint_revision_for_candidate(
            expense_id=55, public_id="cs-1", expense=SimpleNamespace(realm_id="realm"), repo=repo)
    assert (out is not None) is probed


@pytest.mark.parametrize("status,drains", [("confirmed", True), ("enqueued", True), ("written", False),
                                           ("resolved_externally", False), ("error", False)])
def test_drain_worker_uses_policy(status, drains):
    # kills: a member dropped from IN_FLIGHT_STATUSES (confirmed crash-window write silently dropped), or the worker
    # draining a terminal row
    item = _worker_item()
    item.status = status
    _svc, connector, _w = _worker_run(item)
    assert connector.recode_purchase_lines.called is drains
