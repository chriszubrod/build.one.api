"""U-357 — the pure lifecycle resolver. Bill is the first caller (U-443).

Keys ONLY on ReviewStatus FLAGS, never on the admin-editable Name.

U-444 removed the last position-dependency. `submitted` used to mean
`sort_order == MIN(active non-declined sort_order)`, resolved per request and
threaded in as `first_sort_order`. That had two failure modes nothing guarded:
two rows sharing a SortOrder both derived `submitted`, and inserting any row at
or below the current MIN retroactively relabelled every stored `submitted` as
`in_review`, because the kind is computed at read time. It now keys on
`IsInitial`, like the other two — and losing the parameter removed a DB
round-trip per request from every caller.
"""

from types import SimpleNamespace

from shared.lifecycle.resolver import (
    attach_lifecycle,
    resolve_document_status,
    review_kind_from_flags,
)


def test_declined_wins_over_everything():
    """Checked first: a declined row that is somehow also final and initial is
    still `declined`. The shape rails (U-444) refuse to create such a row, but
    the resolver must not depend on them — it reads historical rows too."""
    assert (
        review_kind_from_flags(is_declined=True, is_final=True, is_initial=True)
        == "declined"
    )


def test_final_non_declined_is_approved():
    assert (
        review_kind_from_flags(is_declined=False, is_final=True, is_initial=False)
        == "approved"
    )


def test_the_initial_flag_is_submitted():
    assert (
        review_kind_from_flags(is_declined=False, is_final=False, is_initial=True)
        == "submitted"
    )


def test_sort_order_no_longer_participates_at_all():
    """THE U-444 assertion. The resolver cannot see SortOrder any more, so
    reordering statuses — or two rows sharing a SortOrder — cannot change any
    document's kind. Before U-444 this function took `sort_order` and
    `first_sort_order` and compared them.
    """
    import inspect

    params = set(inspect.signature(review_kind_from_flags).parameters)
    assert params == {"is_declined", "is_final", "is_initial"}, (
        f"resolver signature regained a position parameter: {sorted(params)}"
    )


def test_a_non_initial_intermediate_stage_is_in_review():
    """Any admin-added stage between initial and final collapses here,
    whatever it is called and wherever it sorts."""
    assert (
        review_kind_from_flags(is_declined=False, is_final=False, is_initial=False)
        == "in_review"
    )


def test_an_absent_initial_flag_collapses_to_in_review():
    """A row whose flag did not map (an older payload, a repo that missed the
    column) must not be guessed into `submitted`. `in_review` keeps the document
    in a reviewer's queue, which is the safe direction to be wrong in."""
    assert (
        review_kind_from_flags(is_declined=False, is_final=False, is_initial=None)
        == "in_review"
    )


def test_name_is_ignored():
    """Admin can rename 'Submitted' to anything; flags still decide kind."""
    review = SimpleNamespace(
        status_name="Waiting on AP",
        status_is_declined=False,
        status_is_final=False,
        status_is_initial=True,
    )
    payload = attach_lifecycle(
        {},
        is_draft=True,
        review=review,
    )
    assert payload["review_status"] == "Waiting on AP"
    assert payload["review_status_kind"] == "submitted"
    assert payload["status"] == "submitted"


def test_no_review_is_draft_with_kind_none():
    payload = attach_lifecycle(
        {"is_draft": True},
        is_draft=True,
        review=None,
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
    )
    assert payload["status"] == "completed"
    assert payload["review_status_kind"] == "none"


def test_completed_dominates_open_review():
    """QBO-pulled expenses are born IsDraft=0; an open review is stale, not a reopen."""
    review = SimpleNamespace(
        status_name="Submitted",
        status_is_declined=False,
        status_is_final=False,
        status_is_initial=True,
    )
    payload = attach_lifecycle(
        {},
        is_draft=False,
        review=review,
    )
    assert payload["status"] == "completed"
    assert payload["review_status_kind"] == "submitted"
    assert payload["review_status"] == "Submitted"


def test_draft_with_approved_review_rests_at_approved():
    review = SimpleNamespace(
        status_name="Approved",
        status_is_declined=False,
        status_is_final=True,
        status_is_initial=False,
    )
    payload = attach_lifecycle(
        {},
        is_draft=True,
        review=review,
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
        status_is_initial=False,
    )
    payload = attach_lifecycle(
        {},
        is_draft=True,
        review=review,
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
        status_is_initial=True,
        status_is_final=None,
        status_is_declined=None,
    )
    payload = attach_lifecycle({}, is_draft=True, review=review)
    assert payload["review_status_is_final"] is None
    assert payload["review_status_is_declined"] is None
    # ...and the kind is still derived correctly from the None flags
    assert payload["review_status_kind"] == "submitted"
    assert payload["status"] == "submitted"
