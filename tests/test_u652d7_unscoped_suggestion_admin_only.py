"""U-652d7 (Chris 2026-11-14): a CodingSuggestion with no expense AND no project is admin / system only.

Before, `assert_accessible` fell through to `assert_can_access_project(None)`, which allows, so any EXPENSES
`can_update` holder could flag or confirm one of these rows (71 on prod 2026-11-14). Every test names the mutant
it kills. No DB connection: the context vars decide, and the UDF check is patched to fail if reached."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.access import EntityNotAccessibleError, assert_admin_or_system_context
from shared.authz import current_is_system_context
from shared.authz.context import system_authz
from tests.conftest import actor_context

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
    token = current_is_system_context.set(False)  # pinned: refusal tests must not depend on test order
    try:
        with actor_context(42, False):
            yield
    finally:
        current_is_system_context.reset(token)


@contextmanager
def system_context_only():
    """System context WITHOUT the admin flag, so the context arm is tested on its own (system_authz() sets
    both, which would let a mutant dropping the context arm survive)."""
    token = current_is_system_context.set(True)
    try:
        yield
    finally:
        current_is_system_context.reset(token)


# ---- the shared helper ------------------------------------------------------------------------------------

def test_assert_admin_or_system_context_refuses_a_non_admin_user(non_admin_user):
    # kills: a helper that returns unconditionally
    with pytest.raises(EntityNotAccessibleError):
        assert_admin_or_system_context("CodingSuggestion", 7)


def test_assert_admin_or_system_context_allows_a_system_admin(non_admin_user):
    # kills: dropping the admin bypass
    with actor_context(42, True):
        assert_admin_or_system_context("CodingSuggestion", 7)


def test_assert_admin_or_system_context_allows_a_system_context(non_admin_user):
    # kills: dropping the system-context arm (admin flag off here, so the admin arm cannot save it)
    with system_context_only():
        assert_admin_or_system_context("CodingSuggestion", 7)


def test_assert_admin_or_system_context_allows_system_authz(non_admin_user):
    # the real boundary drains and the sweeper use
    with system_authz():
        assert_admin_or_system_context("CodingSuggestion", 7)


def test_assert_admin_or_system_context_refuses_an_anonymous_caller():
    # kills: treating a missing actor as system (the 2026-05-12 fail-closed rule)
    with actor_context(None, False), pytest.raises(EntityNotAccessibleError):
        assert_admin_or_system_context("CodingSuggestion", None)


# ---- CodingSuggestionService.assert_accessible ------------------------------------------------------------

def test_no_expense_no_project_is_refused_for_a_non_admin(non_admin_user):
    # kills: the old fall-through to assert_can_access_project(None)
    with patch("shared.access._check", side_effect=AssertionError("no UDF call expected")):
        with pytest.raises(EntityNotAccessibleError) as exc:
            _svc().assert_accessible(_item())
    assert exc.value.entity == "CodingSuggestion"


def test_no_expense_no_project_is_allowed_for_a_system_admin(non_admin_user):
    with actor_context(42, True):
        _svc().assert_accessible(_item())


def test_no_expense_no_project_is_allowed_in_system_context(non_admin_user):
    # kills: routing the no-scope branch through the admin-only bypass (system context would then be refused)
    with system_context_only():
        _svc().assert_accessible(_item())


def test_no_expense_with_a_project_still_checks_that_project(non_admin_user):
    # kills: applying the admin-only rule to every no-expense row (a project-scoped row stays user-reachable)
    with patch(f"{SVC}.assert_can_access_project") as acp, patch(f"{SVC}.assert_admin_or_system_context") as asc:
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
            patch(f"{SVC}.assert_admin_or_system_context") as asc:
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
