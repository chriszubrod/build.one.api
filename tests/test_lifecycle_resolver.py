"""U-357 Expense-first — pure lifecycle resolver (LS-00c slice).

Keys ONLY on ReviewStatus flags + position, never on the admin Name.
Expense list/GET attach uses the same helpers.
"""

from types import SimpleNamespace

from shared.lifecycle.resolver import (
    attach_lifecycle,
    resolve_document_status,
    review_kind_from_flags,
)


def test_declined_wins_over_final_and_sort_order():
    assert (
        review_kind_from_flags(
            is_declined=True,
            is_final=True,
            sort_order=10,
            first_sort_order=10,
        )
        == "declined"
    )


def test_final_non_declined_is_approved():
    assert (
        review_kind_from_flags(
            is_declined=False,
            is_final=True,
            sort_order=30,
            first_sort_order=10,
        )
        == "approved"
    )


def test_first_sort_order_is_submitted():
    assert (
        review_kind_from_flags(
            is_declined=False,
            is_final=False,
            sort_order=10,
            first_sort_order=10,
        )
        == "submitted"
    )


def test_intermediate_sort_order_is_in_review():
    assert (
        review_kind_from_flags(
            is_declined=False,
            is_final=False,
            sort_order=20,
            first_sort_order=10,
        )
        == "in_review"
    )


def test_missing_first_sort_order_collapses_to_in_review():
    """Don't guess `submitted` when we cannot compare to MIN(active)."""
    assert (
        review_kind_from_flags(
            is_declined=False,
            is_final=False,
            sort_order=10,
            first_sort_order=None,
        )
        == "in_review"
    )


def test_name_is_ignored():
    """Admin can rename 'Submitted' to anything; flags still decide kind."""
    review = SimpleNamespace(
        status_name="Waiting on AP",
        status_is_declined=False,
        status_is_final=False,
        status_sort_order=10,
    )
    payload = attach_lifecycle(
        {},
        is_draft=True,
        review=review,
        first_sort_order=10,
    )
    assert payload["review_status"] == "Waiting on AP"
    assert payload["review_status_kind"] == "submitted"
    assert payload["status"] == "submitted"


def test_no_review_is_draft_with_kind_none():
    payload = attach_lifecycle(
        {"is_draft": True},
        is_draft=True,
        review=None,
        first_sort_order=10,
    )
    assert payload["status"] == "draft"
    assert payload["review_status"] is None
    assert payload["review_status_kind"] == "none"
    assert payload["review_status_is_final"] is None
    assert payload["review_status_is_declined"] is None


def test_finalized_without_review_is_completed():
    payload = attach_lifecycle(
        {},
        is_draft=False,
        review=None,
        first_sort_order=10,
    )
    assert payload["status"] == "completed"
    assert payload["review_status_kind"] == "none"


def test_completed_dominates_open_review():
    """QBO-pulled expenses are born IsDraft=0; an open review is stale, not a reopen."""
    review = SimpleNamespace(
        status_name="Submitted",
        status_is_declined=False,
        status_is_final=False,
        status_sort_order=10,
    )
    payload = attach_lifecycle(
        {},
        is_draft=False,
        review=review,
        first_sort_order=10,
    )
    assert payload["status"] == "completed"
    assert payload["review_status_kind"] == "submitted"
    assert payload["review_status"] == "Submitted"


def test_draft_with_approved_review_rests_at_approved():
    review = SimpleNamespace(
        status_name="Approved",
        status_is_declined=False,
        status_is_final=True,
        status_sort_order=30,
    )
    payload = attach_lifecycle(
        {},
        is_draft=True,
        review=review,
        first_sort_order=10,
    )
    assert payload["status"] == "approved"
    assert payload["review_status_kind"] == "approved"
    assert payload["review_status_is_final"] is True
    assert payload["review_status_is_declined"] is False


def test_draft_with_declined_review_rests_at_declined():
    review = SimpleNamespace(
        status_name="Declined",
        status_is_declined=True,
        status_is_final=False,
        status_sort_order=100,
    )
    payload = attach_lifecycle(
        {},
        is_draft=True,
        review=review,
        first_sort_order=10,
    )
    assert payload["status"] == "declined"
    assert payload["review_status_kind"] == "declined"
    assert payload["review_status_is_declined"] is True


def test_none_is_draft_is_not_completed():
    """Unset IsDraft must not collapse to completed (None is falsy)."""
    assert resolve_document_status(is_draft=None, review_kind=None) == "draft"
    assert resolve_document_status(is_draft=True, review_kind=None) == "draft"
    assert resolve_document_status(is_draft=False, review_kind="submitted") == "completed"


def test_flags_are_echoed_RAW_not_coerced_to_false():
    """Codex P3, 2026-09-11. `ReviewStatus.IsFinal`/`IsDeclined` are BIT NOT
    NULL, so None is unreachable from today's sproc — but the Bill list has
    always echoed these straight off the row, and `bool()`-ing them here would
    silently convert a hypothetical "unknown" into a confident False for every
    caller. `kind` already treats None as falsy, so raw costs nothing.
    """
    review = SimpleNamespace(
        status_name="Submitted",
        status_sort_order=10,
        status_is_final=None,
        status_is_declined=None,
    )
    payload = attach_lifecycle({}, is_draft=True, review=review, first_sort_order=10)
    assert payload["review_status_is_final"] is None
    assert payload["review_status_is_declined"] is None
    # ...and the kind is still derived correctly from the None flags
    assert payload["review_status_kind"] == "submitted"
    assert payload["status"] == "submitted"
