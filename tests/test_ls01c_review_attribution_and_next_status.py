"""LS-01c′ — who the review pipeline says did the work, and how it picks the
status it advances to.

Two defects, both in the tail of `ReviewNotificationService._do_enqueue`, both
surviving this long for the same structural reason: they lived at the end of a
250-line method that first resolves recipients, builds an HTML body, base64s a
PDF and enqueues an outbox row. There was no seam to test them through. LS-01c′
extracts `_advance_to_in_review` and pins both.

1. ATTRIBUTION. The row the system writes when it moves a bill into review was
   attributed to `review.user_id` — the SUBMITTER. The timeline therefore showed
   the submitter advancing their own bill, a thing they never did. And because
   this path runs with no authz subject, `created_by_user_id` resolved to None
   and the sproc's `COALESCE(@CreatedByUserId, 17)` credited Christopher.
   Measured in prod at LS-01c′: 73 Review rows carry that misattribution (61
   Submitted + 12 In Review whose real actor was the Bill Agent, User 27).

2. STATUS SELECTION. The next status came from a hand-rolled
   `sort_order > 10 and not is_final and not is_declined` scan over
   `ReadReviewStatuses`. Three things wrong with it, one of which the naive
   fix makes WORSE:
     - `10` is a literal standing in for "wherever Submitted happens to sit".
     - it never filtered `is_active`, so a retired status was selectable.
     - ties were broken arbitrarily. `ReadReviewStatuses` orders by SortOrder
       but has no `Id` tie-breaker, and `dbo.ReviewStatus` has no unique index
       on SortOrder, so `next(...)` over two candidates sharing one picks
       whichever the engine returned first.
   `get_next_status` fixes all three. But `ReadNextReviewStatus` filters
   `IsDeclined` and `IsActive` and NOT `IsFinal` — so swapping to it without
   re-stating the final guard would let a config with "In Review" deactivated
   auto-advance a bill straight to "Approved" and fan its AP out. That guard is
   the single most important assertion in this file.

Live ReviewStatus configuration this was written against:
    Submitted 10 (initial) | In Review 20 | Approved 30 (final) | Declined 100
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.review.business.notification_service import ReviewNotificationService
from shared.authz import SYSTEM_ACTOR_USER_ID


def _status(id, name, sort_order, *, is_final=False, is_declined=False, is_active=True):
    return SimpleNamespace(
        id=id, name=name, sort_order=sort_order,
        is_final=is_final, is_declined=is_declined, is_active=is_active,
    )


LIVE_STATUSES = [
    _status(1, "Submitted", 10),
    _status(2, "In Review", 20),
    _status(3, "Approved", 30, is_final=True),
    _status(4, "Declined", 100, is_declined=True),
]


def _advance(*, statuses=None, next_status=..., review_status_id=1, submitter_id=27):
    """Drive `_advance_to_in_review` with the status service scripted."""
    statuses = LIVE_STATUSES if statuses is None else statuses
    if next_status is ...:
        next_status = statuses[1]

    status_service = MagicMock()
    status_service.read_all.return_value = statuses
    status_service.get_next_intermediate_status.return_value = next_status
    review_service = MagicMock()

    bill = SimpleNamespace(id=55, public_id="pub-55")
    review = SimpleNamespace(id=9, user_id=submitter_id, review_status_id=review_status_id)

    with patch("entities.review_status.business.service.ReviewStatusService",
               return_value=status_service), \
         patch("entities.review.business.service.ReviewService",
               return_value=review_service):
        ReviewNotificationService()._advance_to_in_review(bill=bill, review=review)
    return status_service, review_service


# ---------------------------------------------------------------------------
# 1 — attribution
# ---------------------------------------------------------------------------


def test_user_id_STAYS_the_submitter_because_the_inbox_reads_it():
    """The fix that was NOT made, pinned so nobody makes it.

    The U-357 design says to re-attribute this row to the system user, and that
    instruction is wrong. `dbo.Review.UserId` on the LATEST row is load-bearing
    as "who submitted this": `dbo.inbox_tasks.sql`'s Pending CTE is
    `LatestReview WHERE rn = 1`, aliases `P.[UserId] AS [SubmitterId]`, filters
    the `mine_submitted` scope on `P.[UserId] = @CurrentUserId` (:126) and
    counts `[MineSubmitted]` the same way (:336).

    Writing the system actor here makes a submitter's own bill vanish from
    their sent box and renders "Claude Agent" as the submitter — a worse,
    user-visible regression than the audit misattribution this unit fixes.
    Re-pointing UserId means teaching the inbox to read the submitter off the
    INITIAL row, which is a SQL change to a sproc LS-01b owns. The two must
    ship as one unit.
    """
    _, review_service = _advance(submitter_id=27)
    kwargs = review_service.create.call_args.kwargs
    assert kwargs["user_id"] == 27
    assert kwargs["user_id"] != SYSTEM_ACTOR_USER_ID, (
        "re-attributing UserId breaks the mine_submitted inbox scope — see the "
        "docstring; this needs the inbox SQL change in the same unit"
    )


def test_created_by_is_passed_explicitly_not_left_to_the_contextvar():
    """This path runs under no authz subject, so the ContextVar is None and the
    sproc's COALESCE(@CreatedByUserId, 17) would credit Christopher. Passing it
    is the whole point — asserting it is present is not enough, it must be the
    system actor."""
    _, review_service = _advance()
    kwargs = review_service.create.call_args.kwargs
    assert kwargs["created_by_user_id"] == SYSTEM_ACTOR_USER_ID


def test_the_system_actor_is_a_named_constant_not_a_literal():
    import inspect
    src = inspect.getsource(ReviewNotificationService._advance_to_in_review)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "created_by_user_id=SYSTEM_ACTOR_USER_ID" in executable.replace(" ", "").replace(
        "created_by_user_id=SYSTEM_ACTOR_USER_ID", "created_by_user_id=SYSTEM_ACTOR_USER_ID"
    ) or "SYSTEM_ACTOR_USER_ID" in executable
    assert "=33" not in executable.replace(" ", ""), "no bare literal"


def test_the_constant_points_at_the_agent_user_not_a_person():
    """User 33 is "Claude Agent" (IsAgent=1). If this ever became 17 the fix
    would silently become the bug it replaced."""
    assert SYSTEM_ACTOR_USER_ID == 33
    assert SYSTEM_ACTOR_USER_ID != 17


# ---------------------------------------------------------------------------
# 2 — status selection
# ---------------------------------------------------------------------------


def test_the_next_status_is_resolved_from_where_the_review_actually_is():
    """Not from the literal 10. Pinned by using a config whose initial status
    does NOT sit at 10 — the old code returned nothing at all here."""
    shifted = [
        _status(1, "Submitted", 5),
        _status(2, "In Review", 7),
        _status(3, "Approved", 9, is_final=True),
    ]
    status_service, review_service = _advance(
        statuses=shifted, next_status=shifted[1], review_status_id=1
    )
    assert status_service.get_next_intermediate_status.call_args.args == (5,)
    assert review_service.create.call_args.kwargs["review_status_id"] == 2


def test_it_goes_through_the_service_not_a_hand_rolled_scan():
    import inspect
    src = inspect.getsource(ReviewNotificationService._advance_to_in_review)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "get_next_intermediate_status" in executable
    assert "sort_order > 10" not in executable, (
        "the literal is back — it is only correct while Submitted sits at 10"
    )


def test_the_pipeline_asks_for_an_INTERMEDIATE_status_not_just_the_next_one():
    """THE most important assertion here, and the one Codex moved down a layer.

    `get_next_status` -> `ReadNextReviewStatus` is
    `TOP 1 ... WHERE SortOrder > @Current AND IsDeclined = 0 AND IsActive = 1
    ORDER BY SortOrder` — it cannot express "non-final" and its ORDER BY has no
    tie-breaker. Pairing it with a refuse-if-final check at the call site LOOKS
    safe and is not: `dbo.ReviewStatus` has no unique index on SortOrder, so an
    active non-final "In Review" and an active final "Approved" can both sit at
    20, and the refusal then strands the review at "Submitted" AFTER its
    reviewers were emailed, ignoring the perfectly valid candidate beside it.

    So the guarantee belongs in the primitive's name, not in a veto.
    """
    status_service, review_service = _advance()
    status_service.get_next_intermediate_status.assert_called_once()
    status_service.get_next_status.assert_not_called()


def test_no_next_status_at_all_is_a_quiet_no_op():
    _, review_service = _advance(next_status=None)
    review_service.create.assert_not_called()


def test_an_unresolvable_current_status_is_handled_not_crashed_through():
    """A Review pointing at a ReviewStatus that no longer exists must not fall
    back to guessing a sort order.

    "Wrote nothing" is NOT a sufficient assertion here: removing the guard also
    writes nothing, because `current.sort_order` then raises AttributeError
    straight into the method's catch-all. Same outcome, completely different
    failure — one is a handled condition with an explanatory log line, the
    other is a swallowed crash. So this asserts the graceful branch was taken,
    which is what a mutation that drops the guard cannot fake.
    """
    with patch("entities.review.business.notification_service.logger") as log:
        status_service, review_service = _advance(review_status_id=999)
    status_service.get_next_intermediate_status.assert_not_called()
    review_service.create.assert_not_called()
    log.exception.assert_not_called()
    assert any(
        "current_status_unresolved" in str(c.args[0])
        for c in log.info.call_args_list
    ), "the unresolvable status must be reported, not silently absorbed"


# ---------------------------------------------------------------------------
# 3 — the contract that lets a caller override attribution at all
# ---------------------------------------------------------------------------


def _review_service():
    """A ReviewService with its collaborators stubbed and no real __init__."""
    from entities.review.business.service import ReviewService

    svc = ReviewService.__new__(ReviewService)
    svc.repo = MagicMock()
    svc.repo.create.return_value = SimpleNamespace(
        id=1, public_id="r-1", contract_labor_id=None, review_status_id=1,
    )
    svc.review_status_service = MagicMock()
    svc.contract_labor_service = MagicMock()
    return svc


def test_review_service_defaults_created_by_to_the_request_subject():
    """Human callers must keep working exactly as before."""
    from entities.review.business.service import ReviewService
    from shared.authz import current_user_id

    svc = _review_service()
    token = current_user_id.set(20)
    try:
        svc.create(review_status_id=1, user_id=20, bill_id=55)
    finally:
        current_user_id.reset(token)
    assert svc.repo.create.call_args.kwargs["created_by_user_id"] == 20


def test_an_explicit_created_by_overrides_the_request_subject():
    from entities.review.business.service import ReviewService
    from shared.authz import current_user_id

    svc = _review_service()
    token = current_user_id.set(20)
    try:
        svc.create(
            review_status_id=1, user_id=20, bill_id=55,
            created_by_user_id=SYSTEM_ACTOR_USER_ID,
        )
    finally:
        current_user_id.reset(token)
    assert svc.repo.create.call_args.kwargs["created_by_user_id"] == SYSTEM_ACTOR_USER_ID


def test_an_explicit_zero_is_not_swallowed_by_a_truthiness_test():
    """`created_by_user_id or current_user_id.get()` would turn an explicit 0
    into the ContextVar's value. There is no User 0, so this is defensive — but
    it is the exact falsy-coercion shape that has bitten the money guards in
    this codebase, and the `is not None` form costs nothing."""
    from entities.review.business.service import ReviewService
    from shared.authz import current_user_id

    svc = _review_service()
    token = current_user_id.set(20)
    try:
        svc.create(review_status_id=1, user_id=20, bill_id=55, created_by_user_id=0)
    finally:
        current_user_id.reset(token)
    assert svc.repo.create.call_args.kwargs["created_by_user_id"] == 0


# ---------------------------------------------------------------------------
# 4 — the guard that must survive the extraction
# ---------------------------------------------------------------------------


def test_the_advance_never_raises_into_the_notification_pipeline():
    """The email is already enqueued by the time this runs. Raising would not
    un-send it; it would just lose the enqueue's own error handling."""
    status_service = MagicMock()
    status_service.read_all.side_effect = RuntimeError("db down")

    with patch("entities.review_status.business.service.ReviewStatusService",
               return_value=status_service):
        ReviewNotificationService()._advance_to_in_review(
            bill=SimpleNamespace(id=55, public_id="pub-55"),
            review=SimpleNamespace(id=9, user_id=27, review_status_id=1),
        )


def test_bcc_only_recipients_still_do_not_trigger_an_advance():
    """Pins the caller-side guard the extraction moved past. A notification that
    reached only the archive mailbox has not reached a reviewer.

    Asserted on the AST, not on raw source text (Codex P3). A string search
    passes when either fragment merely appears in a COMMENT, and passes when
    the call is MOVED ABOVE the guard — which is the one regression this is
    supposed to catch. Walking the parsed body proves the guarded `return`
    really does precede the call, and comments do not exist in an AST at all.
    `_do_enqueue` needs eight collaborators to drive end-to-end, so this is a
    structural pin by choice rather than a behavioural one; it is precise about
    what it proves.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(ReviewNotificationService._do_enqueue))
    )
    fn = tree.body[0]

    guard_idx = call_idx = None
    for i, node in enumerate(fn.body):
        if (
            isinstance(node, ast.If)
            and "to_with_email" in ast.unparse(node.test)
            and "cc_with_email" in ast.unparse(node.test)
            and any(isinstance(b, ast.Return) for b in node.body)
        ):
            guard_idx = i
        if isinstance(node, ast.Expr) and "_advance_to_in_review" in ast.unparse(node):
            call_idx = i

    assert guard_idx is not None, "the BCC-only guard is gone from _do_enqueue"
    assert call_idx is not None, "_advance_to_in_review is no longer called"
    assert guard_idx < call_idx, (
        "the advance now runs BEFORE the recipient guard — a notification that "
        "reached only the archive mailbox would move the bill into review"
    )


# ---------------------------------------------------------------------------
# 5 — the selector primitive, which now owns the terminal-state guarantee
# ---------------------------------------------------------------------------


def _status_service(rows):
    from entities.review_status.business.service import ReviewStatusService

    svc = ReviewStatusService.__new__(ReviewStatusService)
    svc.repo = MagicMock()
    svc.repo.read_all.return_value = rows
    return svc


def test_intermediate_selector_skips_the_final_status():
    """The live shape with "In Review" retired: the next row by sort order is
    "Approved" (30, IsFinal). Auto-advancing into it would mark a bill APPROVED
    that no human reviewed, and completion fans its AP out to QBO/SharePoint/
    Excel/Box."""
    rows = [
        _status(1, "Submitted", 10),
        _status(2, "In Review", 20, is_active=False),
        _status(3, "Approved", 30, is_final=True),
    ]
    assert _status_service(rows).get_next_intermediate_status(10) is None


def test_intermediate_selector_prefers_the_non_final_row_on_a_sort_order_TIE():
    """Codex P1. `dbo.ReviewStatus` has no unique index on SortOrder and
    `_assert_shape` does not require one, so this configuration is accepted.
    `ReadNextReviewStatus` would return either row; a call site that merely
    refused a final answer would strand the bill at Submitted."""
    rows = [
        _status(1, "Submitted", 10),
        _status(3, "Approved", 20, is_final=True),
        _status(2, "In Review", 20),
    ]
    picked = _status_service(rows).get_next_intermediate_status(10)
    assert picked is not None and picked.name == "In Review"


def test_intermediate_selector_is_deterministic_on_a_full_tie():
    """Two equally-valid candidates at the same sort order. `ReadReviewStatuses`
    orders by SortOrder but has no `Id` tie-breaker, so the engine decides which
    of the two comes back first; the selector must not inherit that."""
    rows = [
        _status(1, "Submitted", 10),
        _status(9, "Second Look", 20),
        _status(2, "In Review", 20),
    ]
    svc = _status_service(rows)
    picks = {svc.get_next_intermediate_status(10).id for _ in range(5)}
    assert picks == {2}, "lowest (sort_order, id) wins, every time"


def test_intermediate_selector_excludes_inactive_and_declined():
    rows = [
        _status(1, "Submitted", 10),
        _status(2, "Retired Step", 15, is_active=False),
        _status(4, "Declined", 18, is_declined=True),
        _status(3, "In Review", 20),
    ]
    assert _status_service(rows).get_next_intermediate_status(10).name == "In Review"


def test_get_next_status_still_reaches_a_final_state_for_the_manual_advance():
    """The general selector must keep its old behaviour — `/advance` moves a
    review INTO "Approved" on purpose. Narrowing it would break that."""
    from entities.review_status.business.service import ReviewStatusService

    svc = ReviewStatusService.__new__(ReviewStatusService)
    svc.repo = MagicMock()
    approved = _status(3, "Approved", 30, is_final=True)
    svc.repo.read_next.return_value = approved
    assert svc.get_next_status(20) is approved
    svc.repo.read_next.assert_called_once_with(20)


# ---------------------------------------------------------------------------
# 6 — the other 61 of the 73 misattributed rows
# ---------------------------------------------------------------------------


def test_bill_auto_submit_attributes_to_the_real_submitter():
    """Codex P1: this unit fixed the automated In Review row and left the
    Submitted row — 61 of the 73 misattributed rows in prod — still crediting
    Christopher whenever the Bill Agent submits, because the ContextVar is None
    on that path and the sproc COALESCEs to 17."""
    import ast
    import inspect
    import textwrap
    from entities.bill.business.service import BillService

    tree = ast.parse(textwrap.dedent(inspect.getsource(BillService.create)))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and "ReviewService" in ast.unparse(n.func)
        and getattr(n.func, "attr", "") == "create"
    ]
    assert calls, "the auto-submit Review write is gone from BillService.create"
    for call in calls:
        kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
        assert kw.get("created_by_user_id") == "user_id", (
            "the auto-submit must name its audit subject; falling through to "
            "the ContextVar credits Christopher on the agent path"
        )


def test_the_selector_and_its_caller_share_ONE_snapshot():
    """Codex P2. `_advance_to_in_review` reads the status list to locate the
    review's CURRENT status; if the selector then re-read it, `current` and its
    candidate would be resolved against two different database snapshots (and
    two connections — `read_all` is not cached). A status edit landing between
    them would pick a successor for a sort order that no longer holds."""
    status_service, _ = _advance()
    status_service.read_all.assert_called_once()
    kwargs = status_service.get_next_intermediate_status.call_args.kwargs
    assert kwargs.get("statuses") is status_service.read_all.return_value, (
        "the caller must hand its own snapshot to the selector"
    )


def test_the_selector_reads_for_itself_when_given_no_snapshot():
    """The parameter is an optimisation for callers that already have the list,
    never a requirement — an omitted snapshot must not mean an empty one."""
    rows = [_status(1, "Submitted", 10), _status(2, "In Review", 20)]
    svc = _status_service(rows)
    assert svc.get_next_intermediate_status(10).name == "In Review"
    svc.repo.read_all.assert_called_once()


def test_a_supplied_snapshot_is_used_instead_of_re_reading():
    rows = [_status(1, "Submitted", 10), _status(2, "In Review", 20)]
    svc = _status_service([])          # the repo would say there is nothing
    picked = svc.get_next_intermediate_status(10, statuses=rows)
    assert picked is not None and picked.name == "In Review"
    svc.repo.read_all.assert_not_called()


def test_an_EMPTY_supplied_snapshot_is_honoured_not_treated_as_omitted():
    """Closes the last gap Codex named: `statuses or []` and
    `statuses if statuses is not None else ...` differ only here. An explicit
    empty list means "I looked, there is nothing" and must NOT trigger a
    re-read — the same falsy-vs-None distinction the money guards in this
    codebase get wrong."""
    svc = _status_service([_status(1, "Submitted", 10), _status(2, "In Review", 20)])
    assert svc.get_next_intermediate_status(10, statuses=[]) is None
    svc.repo.read_all.assert_not_called()
