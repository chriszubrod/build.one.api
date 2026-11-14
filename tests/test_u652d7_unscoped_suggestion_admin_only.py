"""U-652d7 (Chris 2026-11-14): a CodingSuggestion with no expense AND no project is admin / system only.

Before, `assert_accessible` fell through to `assert_can_access_project(None)`, which allows, so any EXPENSES
`can_update` holder could flag or confirm one of these rows (71 on prod 2026-11-14). Every test names the mutant
it kills. No DB connection: the context vars decide, and the UDF check is patched to fail if reached."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.access import EntityNotAccessibleError, assert_system_caller
from shared.authz import current_is_system_admin, current_is_system_context, current_user_id
from shared.authz.context import system_authz

SVC = "entities.coding_suggestion.business.service"


def _item(**extra):
    base = dict(id=7, public_id="77777777-7777-7777-7777-777777777777", expense_id=None,
                confirmed_project_id=None, suggested_project_id=None)
    base.update(extra)
    return SimpleNamespace(**base)


def _svc():
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.repo = MagicMock()
    return svc


@pytest.fixture
def non_admin_user():
    tokens = (current_user_id.set(42), current_is_system_admin.set(False), current_is_system_context.set(False))
    try:
        yield
    finally:
        current_is_system_context.reset(tokens[2])
        current_is_system_admin.reset(tokens[1])
        current_user_id.reset(tokens[0])


# ---- the shared helper ------------------------------------------------------------------------------------

def test_assert_system_caller_refuses_a_non_admin_user(non_admin_user):
    # kills: a helper that returns unconditionally
    with pytest.raises(EntityNotAccessibleError):
        assert_system_caller("CodingSuggestion", 7)


def test_assert_system_caller_allows_a_system_admin(non_admin_user):
    # kills: dropping the admin bypass
    token = current_is_system_admin.set(True)
    try:
        assert_system_caller("CodingSuggestion", 7)
    finally:
        current_is_system_admin.reset(token)


def test_assert_system_caller_allows_a_system_context(non_admin_user):
    # kills: dropping the system-context bypass (outbox drains / sweeper run under system_authz())
    with system_authz():
        assert_system_caller("CodingSuggestion", 7)


def test_assert_system_caller_refuses_an_anonymous_caller():
    # kills: treating a missing actor as system (the 2026-05-12 fail-closed rule)
    tokens = (current_user_id.set(None), current_is_system_admin.set(False), current_is_system_context.set(False))
    try:
        with pytest.raises(EntityNotAccessibleError):
            assert_system_caller("CodingSuggestion", None)
    finally:
        current_is_system_context.reset(tokens[2])
        current_is_system_admin.reset(tokens[1])
        current_user_id.reset(tokens[0])


# ---- CodingSuggestionService.assert_accessible ------------------------------------------------------------

def test_no_expense_no_project_is_refused_for_a_non_admin(non_admin_user):
    # kills: the old fall-through to assert_can_access_project(None)
    with patch("shared.access._check", side_effect=AssertionError("no UDF call expected")):
        with pytest.raises(EntityNotAccessibleError) as exc:
            _svc().assert_accessible(_item())
    assert exc.value.entity == "CodingSuggestion"


def test_no_expense_no_project_is_allowed_for_a_system_admin(non_admin_user):
    token = current_is_system_admin.set(True)
    try:
        _svc().assert_accessible(_item())
    finally:
        current_is_system_admin.reset(token)


def test_no_expense_no_project_is_allowed_in_system_context(non_admin_user):
    # kills: routing the no-scope branch through the admin-only bypass (system context would then be refused)
    with system_authz():
        _svc().assert_accessible(_item())


def test_no_expense_with_a_project_still_checks_that_project(non_admin_user):
    # kills: applying the admin-only rule to every no-expense row (a project-scoped row stays user-reachable)
    with patch(f"{SVC}.assert_can_access_project") as acp, patch(f"{SVC}.assert_system_caller") as asc:
        _svc().assert_accessible(_item(suggested_project_id=9))
    acp.assert_called_once_with(9)
    asc.assert_not_called()


def test_confirmed_project_wins_over_suggested(non_admin_user):
    # kills: reading suggested before confirmed (mirrors COALESCE(Confirmed, Suggested))
    with patch(f"{SVC}.assert_can_access_project") as acp:
        _svc().assert_accessible(_item(confirmed_project_id=3, suggested_project_id=9))
    acp.assert_called_once_with(3)


def test_an_expense_row_checks_only_the_expense(non_admin_user):
    # kills: also running the project / no-scope branch after the expense check
    with patch(f"{SVC}.assert_can_access_expense") as ace, patch(f"{SVC}.assert_can_access_project") as acp, \
            patch(f"{SVC}.assert_system_caller") as asc:
        _svc().assert_accessible(_item(expense_id=5))
    ace.assert_called_once_with(5)
    acp.assert_not_called()
    asc.assert_not_called()


def test_record_flag_refuses_before_writing(non_admin_user):
    # kills: record_flag skipping assert_accessible for a no-scope row
    svc = _svc()
    svc.repo.read_by_public_id.return_value = _item()
    with pytest.raises(EntityNotAccessibleError):
        svc.record_flag(public_id="77777777-7777-7777-7777-777777777777", reason="x")
    svc.repo.record_flag.assert_not_called()


def test_record_suggestion_refuses_before_writing(non_admin_user):
    svc = _svc()
    svc.repo.read_by_public_id.return_value = _item()
    with pytest.raises(EntityNotAccessibleError):
        svc.record_suggestion(public_id="77777777-7777-7777-7777-777777777777", suggested_project_id=None)
    svc.repo.record_suggestion.assert_not_called()
