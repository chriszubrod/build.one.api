"""U-652d4 item 14: row-level access for coding suggestions lives in the service (`assert_accessible`),
not only in the router. Covers confirm, flag, record, and the suggest_pending batch filter.

Every test names the mutant it kills. The repo, outbox and resolver are mocked; no DB connection."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.access import EntityNotAccessibleError
from shared.authz.context import system_authz

GATE = "integrations.intuit.qbo.base.client.recode_write_gate_reason"
ACC_EXP = "entities.coding_suggestion.business.service.assert_can_access_expense"
SUG = "entities.coding_suggestion.business.suggestion_service"
PUBLIC_ID = "44444444-4444-4444-4444-444444444444"


def _item(expense_id=5, **extra):
    base = dict(public_id=PUBLIC_ID, expense_id=expense_id, status="pending", confirmation_seq=1,
                realm_id="realm-1", confirmed_project_id=None, suggested_project_id=None,
                claimed_by_user_id=None, confirmation_source=None, repoint_targets=None)
    base.update(extra)
    return SimpleNamespace(**base)


def _svc():
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    return svc


# ---- assert_accessible -------------------------------------------------------------------------------

def test_assert_accessible_raises_for_an_inaccessible_expense():
    # kills: an assert_accessible that is a no-op (the router check moved but nothing replaced it)
    svc = _svc()
    with patch(ACC_EXP, side_effect=EntityNotAccessibleError("Expense", 5)):
        with pytest.raises(EntityNotAccessibleError):
            svc.assert_accessible(_item(expense_id=5))


def test_assert_accessible_checks_the_project_when_there_is_no_expense():
    # kills: dropping the project-scope branch (a suggestion with no expense is checked on its project)
    from entities.coding_suggestion.business import service as service_module

    svc = _svc()
    with patch.object(service_module, "assert_can_access_project",
                      side_effect=EntityNotAccessibleError("Project", 9)) as acc:
        with pytest.raises(EntityNotAccessibleError):
            svc.assert_accessible(_item(expense_id=None, suggested_project_id=9))
    acc.assert_called_once_with(9)


# ---- confirm: refused before any write --------------------------------------------------------------

@patch(GATE, return_value=None)
def test_confirm_refuses_an_inaccessible_row_before_recording(_gate):
    # kills: a confirm that records/enqueues for a row the actor cannot see (only the router checked)
    svc = _svc()
    svc.read_by_public_id = MagicMock(return_value=_item())
    svc.record_confirmation = MagicMock()
    svc._enqueue_recode_and_mark = MagicMock()
    with patch(ACC_EXP, side_effect=EntityNotAccessibleError("Expense", 5)):
        with pytest.raises(EntityNotAccessibleError):
            svc.confirm(public_id=PUBLIC_ID, project_id=10, sub_cost_code_id=20, description="x",
                        was_overridden=False, user_id=17)
    svc.record_confirmation.assert_not_called()
    svc._enqueue_recode_and_mark.assert_not_called()


# ---- flag and record -----------------------------------------------------------------------------------

def test_record_flag_refuses_an_inaccessible_row_before_the_write():
    # kills: a flag write on a row the actor cannot see (the internal caller path has no router check)
    svc = _svc()
    svc.read_by_public_id = MagicMock(return_value=_item())
    with patch(ACC_EXP, side_effect=EntityNotAccessibleError("Expense", 5)):
        with pytest.raises(EntityNotAccessibleError):
            svc.record_flag(public_id=PUBLIC_ID, reason="x", modified_by_user_id=17)
    svc.repo.record_flag.assert_not_called()


def test_record_suggestion_refuses_an_inaccessible_row_before_the_write():
    # kills: a suggestion persisted for a purchase the actor cannot see
    svc = _svc()
    svc.read_by_public_id = MagicMock(return_value=_item())
    with patch(ACC_EXP, side_effect=EntityNotAccessibleError("Expense", 5)):
        with pytest.raises(EntityNotAccessibleError):
            svc.record_suggestion(public_id=PUBLIC_ID, suggested_project_id=10, status="suggested")
    svc.repo.record_suggestion.assert_not_called()


def test_accessible_row_still_flags():
    # kills: an over-broad check that refuses an accessible actor
    svc = _svc()
    svc.read_by_public_id = MagicMock(return_value=_item())
    svc.repo.record_flag.return_value = "flagged"
    with patch(ACC_EXP, return_value=None):
        assert svc.record_flag(public_id=PUBLIC_ID, reason="x", modified_by_user_id=17) == "flagged"


# ---- suggest_pending: batch skips, never raises -------------------------------------------------------

def _engine(queue_rows, items):
    from entities.coding_suggestion.business.suggestion_service import CodingSuggestionEngine

    coding_item_service = MagicMock()
    coding_item_service.read_by_public_id.side_effect = lambda pid: items[pid]
    qbo = MagicMock()
    qbo.get_expense_coding_queue.return_value = queue_rows
    eng = CodingSuggestionEngine(
        project_service=MagicMock(), sub_cost_code_service=MagicMock(),
        coding_item_service=coding_item_service, suggestion_repo=MagicMock(),
        resolve_qbo_item_ref=MagicMock(), qbo_purchase_service=qbo,
    )
    eng.suggest_for_item = MagicMock(return_value={"status": "flagged", "reason": "r"})
    eng.persist_result = MagicMock()
    return eng


def _row(pid):
    return {"coding_status": "pending", "coding_item_public_id": pid, "suggestion_source": None,
            "private_note": None, "vendor_id": None, "sync_token": "1"}


def test_suggest_pending_skips_a_row_the_non_admin_cannot_access_and_does_not_raise():
    # kills: suggest_pending writing suggestions for purchases the actor cannot see (the walk-the-realm bug)
    A, B = "55555555-5555-5555-5555-555555555555", "66666666-6666-6666-6666-666666666666"
    items = {A: _item(expense_id=5, public_id=A), B: _item(expense_id=6, public_id=B)}
    eng = _engine([_row(A), _row(B)], items)
    with patch(f"{SUG}.resolve_accessible_project_ids", return_value=frozenset({1})), \
            patch(f"{SUG}.can_access_expense", side_effect=lambda eid: eid == 6):
        out = eng.suggest_pending(realm_id="R1", max_items=10)
    processed = [c.kwargs["public_id"] for c in eng.persist_result.call_args_list]
    assert processed == [B]
    assert out["processed"] == 1 and out["flagged"] == 1


def test_suggest_pending_skips_a_non_admin_row_with_no_expense():
    # kills: a row with no expense treated as accessible by default (fail-open for a non-admin)
    A = "77777777-7777-7777-7777-777777777777"
    eng = _engine([_row(A)], {A: _item(expense_id=None, public_id=A)})
    with patch(f"{SUG}.resolve_accessible_project_ids", return_value=frozenset({1})):
        out = eng.suggest_pending(realm_id="R1", max_items=10)
    eng.persist_result.assert_not_called()
    assert out["processed"] == 0


def test_suggest_pending_under_system_authz_processes_every_row():
    # kills: system callers (completion job, pull, CLI) losing the bypass the design keeps
    A, B = "88888888-8888-8888-8888-888888888888", "99999999-9999-9999-9999-999999999999"
    items = {A: _item(expense_id=5, public_id=A), B: _item(expense_id=None, public_id=B)}
    eng = _engine([_row(A), _row(B)], items)
    with system_authz():
        out = eng.suggest_pending(realm_id="R1", max_items=10)
    assert out["processed"] == 2
    assert eng.persist_result.call_count == 2


# ---- EM round 4 (P3): flag router 404, fail-closed attribute, lazy filter, real-check confirms ---------

def test_assert_accessible_fails_closed_when_the_item_has_no_expense_id_attribute():
    # kills: getattr(item, "expense_id", None) silently treating a renamed attribute as "no expense"
    svc = _svc()
    with pytest.raises(AttributeError):
        svc.assert_accessible(SimpleNamespace(confirmed_project_id=None, suggested_project_id=9))


def test_flag_router_maps_a_service_refusal_to_404_not_500():
    # kills: the flag route's generic arm turning a service-side EntityNotAccessibleError into a 500
    from fastapi import HTTPException

    from entities.coding_suggestion.api import router as router_mod
    from entities.coding_suggestion.api.schemas import FlagCodingSuggestionRequest
    from entities.coding_suggestion.business.service import CodingSuggestionService
    from shared.access import EntityNotAccessibleError

    service = MagicMock(spec=CodingSuggestionService)
    service.read_by_public_id.return_value = _item()
    service.record_flag.side_effect = EntityNotAccessibleError("Expense", 5)
    body = FlagCodingSuggestionRequest(reason="x")
    with patch.object(router_mod, "CodingSuggestionService", return_value=service), \
            patch.object(router_mod, "current_user_id") as uid:
        uid.get.return_value = 17
        with pytest.raises(HTTPException) as err:
            router_mod.flag_expense_coding_item_router(PUBLIC_ID, body, _={"can_update": True})
    assert err.value.status_code == 404


def test_suggest_pending_stops_once_max_items_accessible_rows_are_found():
    # kills: filtering the whole queue before the cap (one access + one read per row, however many)
    A, B, C = ("aaaaaaaa-0000-0000-0000-00000000000%d" % i for i in (1, 2, 3))
    items = {pid: _item(expense_id=i + 1, public_id=pid) for i, pid in enumerate((A, B, C))}
    eng = _engine([_row(A), _row(B), _row(C)], items)
    can_access = MagicMock(return_value=True)
    with patch(f"{SUG}.resolve_accessible_project_ids", return_value=frozenset({1})), \
            patch(f"{SUG}.can_access_expense", can_access):
        out = eng.suggest_pending(realm_id="R1", max_items=1)
    assert out["processed"] == 1
    assert can_access.call_count == 1
    assert eng.coding_item_service.read_by_public_id.call_count == 1


def test_repoint_confirm_under_system_authz_passes_the_real_access_check():
    # REAL assert_accessible (no fixture). The UDF would refuse (_check False); system context bypasses it,
    # so the flow reaches the revision fence and refuses there, not with EntityNotAccessibleError.
    from shared.authz.context import system_authz

    svc = _svc()
    svc.read_by_public_id = MagicMock(return_value=_item(
        expense_id=5, status="written", confirmation_seq=5, confirmation_source="complete_repoint"))
    with patch("shared.access._check", return_value=False), patch(GATE, return_value=None), system_authz():
        out = svc.confirm(public_id=PUBLIC_ID, project_id=10, sub_cost_code_id=20, description="x",
                          was_overridden=False, user_id=17, repoint=True, expected_confirmation_seq=4,
                          lock_held=True)
    assert out["status"] == "repoint_revision_lost"


def test_repoint_confirm_without_system_authz_is_refused_by_the_real_access_check():
    # the same call from a non-admin with no access: the real check refuses before the fence
    svc = _svc()
    svc.read_by_public_id = MagicMock(return_value=_item(
        expense_id=5, status="written", confirmation_seq=5, confirmation_source="complete_repoint"))
    with patch("shared.access._check", return_value=False), patch(GATE, return_value=None):
        with pytest.raises(EntityNotAccessibleError):
            svc.confirm(public_id=PUBLIC_ID, project_id=10, sub_cost_code_id=20, description="x",
                        was_overridden=False, user_id=17, repoint=True, expected_confirmation_seq=4,
                        lock_held=True)


def test_submit_path_confirm_by_a_non_admin_with_access_passes_the_real_check():
    # a legitimate non-admin caller with UDF access: the real assert_accessible passes and the flow
    # continues (an 'enqueued' row is refused by the status guard, which is the proof the check passed)
    svc = _svc()
    svc.read_by_public_id = MagicMock(return_value=_item(expense_id=5, status="enqueued"))
    with patch("shared.access._check", return_value=True), patch(GATE, return_value=None):
        out = svc.confirm(public_id=PUBLIC_ID, project_id=10, sub_cost_code_id=20, description="x",
                          was_overridden=False, user_id=17)
    assert out["status"] == "already_enqueued"
