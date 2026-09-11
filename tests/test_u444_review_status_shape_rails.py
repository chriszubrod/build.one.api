"""U-444 — the ReviewStatus set is an alphabet, and nothing was guarding it.

`review_status_kind` (U-443) is derived from the four rows in `dbo.ReviewStatus`.
Two of its three kinds keyed on flags — `declined` on IsDeclined, `approved` on
IsFinal — and the third, `submitted`, keyed on POSITION: the row with
MIN(SortOrder) among active non-declined rows. Nothing validated any of it.
`ReviewStatusService.create/update/delete` passed straight through to the repo.

Two live traps that came out of that:

1. `ReadFirstReviewStatus` filtered IsDeclined and IsActive but NOT IsFinal, so
   making the lowest-sorted row final would have made `/submit` auto-approve
   every document. That sproc decides the status every auto-Submit Review is
   CREATED at (bill/business/service.py:486) and whether reviewers get emailed
   (review/business/service.py:127) — not just the badge.

2. Inserting any status at SortOrder <= the current MIN silently re-derived all
   758 stored `submitted` Reviews as `in_review`, retroactively, because the
   kind is computed at read time.

U-444 fixes the cause rather than policing it: `IsInitial BIT` makes all three
kinds flag-derived, so SortOrder stops driving derivation (it still drives
ReadNextReviewStatus — what comes NEXT — so reordering changes the pipeline
going forward without relabelling history). The §4.2 rule that would have
FROZEN the first stage permanently — "refuse any SortOrder at or below the
current MIN" — is therefore not implemented, and is not needed.

What remains is a shape contract on the resulting set, enforced on every
mutation. These tests pin each rail independently; every one of them goes RED
if its rail is removed.
"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from entities.review_status.business.model import ReviewStatus
from entities.review_status.business.service import (
    ReviewStatusService,
    ReviewStatusShapeError,
)


def _status(id, name, sort_order, *, is_final=False, is_declined=False,
            is_active=True, is_initial=False):
    return ReviewStatus(
        id=id, public_id=f"rs-{id}", row_version="AAAA",
        created_datetime=None, modified_datetime=None,
        name=name, description=None, sort_order=sort_order,
        is_final=is_final, is_declined=is_declined, is_active=is_active,
        color=None, is_initial=is_initial,
    )


def _prod_set():
    """The four rows actually in prod on 2026-09-11, post-backfill."""
    return [
        _status(1, "Submitted", 10, is_initial=True),
        _status(2, "In Review", 20),
        _status(3, "Approved", 30, is_final=True),
        _status(4, "Declined", 100, is_declined=True),
    ]


def _service(rows=None, *, references=0):
    rows = _prod_set() if rows is None else rows
    repo = MagicMock()
    repo.read_all.side_effect = lambda: [replace(s) for s in rows]
    # A COPY, like the real repo — the service mutates `existing` in place, and
    # handing it the same object that read_all() returns would let an update
    # edit the set it is about to be validated against.
    repo.read_by_public_id.side_effect = lambda public_id: next(
        (replace(s) for s in rows if s.public_id == public_id), None
    )
    repo.count_references.return_value = references
    repo.create.side_effect = lambda **kw: SimpleNamespace(**kw)
    repo.update_by_id.side_effect = lambda s: s
    repo.delete_by_id.side_effect = lambda id: SimpleNamespace(id=id)
    return ReviewStatusService(repo=repo), repo


# ---------------------------------------------------------------------------
# The set as it stands must be legal — otherwise every test below is vacuous
# ---------------------------------------------------------------------------


def test_the_live_prod_configuration_passes_every_rail():
    """If this fails, the rails would reject prod's own data and the unit is
    unshippable. It is the first thing to check, not an afterthought."""
    ReviewStatusService._assert_shape(_prod_set())


def test_a_plain_extra_stage_is_still_allowed():
    """The rails must not freeze the table. Adding an intermediate stage — the
    thing an admin actually wants to do — stays legal at ANY sort order."""
    svc, repo = _service()
    for sort_order in (5, 15, 25, 999):
        svc.create(name="Owner Review", sort_order=sort_order)
    assert repo.create.call_count == 4


def test_reordering_below_the_initial_row_is_allowed_not_refused():
    """THE design decision, pinned (option B over §4.2's option A).

    §4.2 would have refused any SortOrder <= the current MIN, permanently
    freezing 'Submitted' as the first stage. Because `submitted` now keys on
    IsInitial instead of position, a lower-sorted row is harmless: it changes
    what comes NEXT, and relabels nothing historical.
    """
    svc, repo = _service()
    svc.create(name="Pending", sort_order=1)
    repo.create.assert_called_once()
    assert repo.create.call_args.kwargs["sort_order"] == 1


# ---------------------------------------------------------------------------
# Rail 1 — exactly one active initial
# ---------------------------------------------------------------------------


def test_setting_the_initial_flag_TRANSFERS_it_rather_than_being_refused():
    """Codex P1, 2026-09-11 — this test used to assert the opposite.

    "Exactly one active initial" is unsatisfiable one row at a time: setting the
    new holder reads as two, clearing the old reads as zero, so BOTH edits get
    refused and the flag can never be moved. That makes the rail a brick rather
    than an invariant — and it would have blocked the Pending/Submitted/Ready/
    Billed convergence outright.

    Setting it MOVES it. `_resulting_set` demotes the incumbent in memory and
    `UpdateReviewStatusById` / `CreateReviewStatus` do the same in the SAME
    transaction, so the two can't disagree.
    """
    svc, repo = _service()
    svc.create(name="Pending", sort_order=5, is_initial=True)
    repo.create.assert_called_once()
    assert repo.create.call_args.kwargs["is_initial"] is True


def test_moving_the_initial_flag_to_an_existing_row_is_allowed():
    """The transfer case that matters for the vocabulary rework — driven through
    the real service update, not by hand-building a set and calling
    `_assert_shape` (which is what made the earlier version of this vacuous)."""
    rows = _prod_set() + [_status(9, "Pending", 5)]
    svc, repo = _service(rows)
    svc.update_by_public_id("rs-9", row_version="AAAA", is_initial=True)
    repo.update_by_id.assert_called_once()
    assert repo.update_by_id.call_args.args[0].is_initial is True


def test_the_demote_is_atomic_in_the_sproc_not_a_second_round_trip():
    """The Python set-level check and the SQL write must agree, and the SQL must
    not leave a window where two rows carry the flag."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    base = REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql"
    for name in ("CreateReviewStatus", "UpdateReviewStatusById"):
        body = sproc_body(base, name)
        assert "IF @IsInitial = 1" in body, f"{name} must demote the incumbent"
        assert "SET [IsInitial] = 0" in body, f"{name} must clear the previous holder"
        assert "BEGIN TRANSACTION" in body, f"{name}'s demote must share the write's transaction"


def test_removing_the_only_initial_status_is_refused():
    """Deactivating it leaves zero — `/submit` would have nothing to create a
    Review at, and `get_first_status()` returns None, which
    bill/business/service.py:486 handles by SKIPPING the auto-Submit entirely.
    A bill would silently never enter review."""
    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError, match="initial"):
        svc.update_by_public_id("rs-1", row_version="AAAA", is_active=False)


def test_unsetting_the_initial_flag_is_refused_when_it_leaves_none():
    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError, match="initial"):
        svc.update_by_public_id("rs-1", row_version="AAAA", is_initial=False)


def test_the_initial_status_may_not_also_be_final():
    """The exact trap `ReadFirstReviewStatus` used to leave open: it filtered
    IsDeclined and IsActive but NOT IsFinal, so an initial-and-final row made
    every /submit auto-approve."""
    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError, match="final or declined"):
        svc.update_by_public_id("rs-1", row_version="AAAA", is_final=True)


def test_the_initial_status_may_not_also_be_declined():
    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError, match="final or declined"):
        svc.update_by_public_id("rs-1", row_version="AAAA", is_declined=True)


# ---------------------------------------------------------------------------
# Rail 2 — exactly one active final
# ---------------------------------------------------------------------------


def test_setting_final_TRANSFERS_it_like_the_initial_flag():
    """Codex P1, 2026-09-11 — this test used to assert a refusal.

    The first cut gave transfer semantics to IsInitial alone, which left
    'Approved' and 'Declined' permanently unreplaceable: setting a new one reads
    as two, clearing the old reads as zero. All three singleton roles now move
    the same way, in the same transaction.
    """
    svc, repo = _service()
    svc.create(name="Signed Off", sort_order=40, is_final=True)
    repo.create.assert_called_once()
    assert repo.create.call_args.kwargs["is_final"] is True


def test_removing_the_only_final_status_is_refused():
    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError, match="final"):
        svc.update_by_public_id("rs-3", row_version="AAAA", is_active=False)


# ---------------------------------------------------------------------------
# Rail 3 — exactly one active declined
# ---------------------------------------------------------------------------


def test_setting_declined_TRANSFERS_it_too():
    """Two active declined rows were previously a RUNTIME failure: ReviewService's
    decline path raises 'Multiple declined review statuses are configured' at the
    moment a reviewer clicks Decline. Transferring keeps it at exactly one, so
    that failure mode cannot be reached by an admin edit at all."""
    svc, repo = _service()
    svc.create(name="Rejected", sort_order=101, is_declined=True)
    repo.create.assert_called_once()
    assert repo.create.call_args.kwargs["is_declined"] is True


def test_all_three_roles_transfer_in_the_sprocs_not_just_the_initial_one():
    from tests.sproc_text import REPO_ROOT, sproc_body

    base = REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql"
    for name in ("CreateReviewStatus", "UpdateReviewStatusById"):
        body = sproc_body(base, name)
        for flag in ("IsInitial", "IsFinal", "IsDeclined"):
            assert f"IF @{flag} = 1" in body, f"{name} does not transfer {flag}"
            assert f"SET [{flag}] = 0" in body, f"{name} does not demote the previous {flag}"


def test_removing_the_only_declined_status_is_refused():
    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError, match="declined"):
        svc.update_by_public_id("rs-4", row_version="AAAA", is_active=False)


# ---------------------------------------------------------------------------
# Rail 4 — contradictory flags
# ---------------------------------------------------------------------------


def test_final_and_declined_together_is_refused():
    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError, match="both final and declined"):
        svc.create(name="Rejected-Final", sort_order=90, is_final=True, is_declined=True)


def test_a_contradictory_row_is_refused_even_while_inactive():
    """Flag contradictions are checked across the WHOLE set, not just active
    rows: an inactive row is one edit away from being active, and the rejection
    should land on the edit that introduced the contradiction."""
    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError, match="both final and declined"):
        svc.create(name="Dormant", sort_order=90, is_final=True,
                   is_declined=True, is_active=False)


# ---------------------------------------------------------------------------
# Rail 5 — don't strand live documents
# ---------------------------------------------------------------------------


def test_deactivating_a_referenced_status_is_refused():
    """'In Review' carries 352 Reviews in prod. Deactivating it drops it out of
    ReadNextReviewStatus while every one of those Reviews still points at it —
    documents stranded mid-pipeline with no way forward."""
    svc, _ = _service(references=352)
    with pytest.raises(ReviewStatusShapeError, match="352 review"):
        svc.update_by_public_id("rs-2", row_version="AAAA", is_active=False)


def test_deleting_a_referenced_status_is_refused_before_the_fk_sees_it():
    """The FK refuses this too, as a 547 carrying a constraint name. Checking
    first turns it into a sentence an admin can act on."""
    svc, repo = _service(references=352)
    with pytest.raises(ReviewStatusShapeError, match="review"):
        svc.delete_by_public_id("rs-2")
    repo.delete_by_id.assert_not_called()


def test_an_unreferenced_row_can_still_be_deactivated():
    """The rail must not become 'nothing is ever editable'. A freshly added
    stage nobody has used yet is removable."""
    rows = _prod_set() + [_status(9, "Unused", 25)]
    svc, repo = _service(rows, references=0)
    svc.update_by_public_id("rs-9", row_version="AAAA", is_active=False)
    repo.update_by_id.assert_called_once()


def test_reactivating_is_never_blocked_by_references():
    """Only a transition INTO inactive strands anything. Turning a status back
    ON is always safe, however many rows point at it."""
    rows = _prod_set() + [_status(9, "Paused", 25, is_active=False)]
    svc, repo = _service(rows, references=999)
    svc.update_by_public_id("rs-9", row_version="AAAA", is_active=True)
    repo.update_by_id.assert_called_once()
    repo.count_references.assert_not_called()


def test_editing_an_already_inactive_row_does_not_consult_references():
    rows = _prod_set() + [_status(9, "Paused", 25, is_active=False)]
    svc, repo = _service(rows, references=999)
    svc.update_by_public_id("rs-9", row_version="AAAA", name="Paused (renamed)")
    repo.count_references.assert_not_called()


# ---------------------------------------------------------------------------
# The rails are evaluated on the RESULTING set, not the current one
# ---------------------------------------------------------------------------


def test_an_update_is_judged_on_what_the_set_WOULD_be():
    """A rename that leaves the roles alone must pass, and a flag change must be
    judged against the set INCLUDING that change — not the current one, where
    the edited row still carries its old values.

    Driven through the service so the assertion covers the wiring, not just
    `_assert_shape` in isolation (Codex P1: the earlier version hand-built an
    already-valid set and proved nothing about any code path)."""
    svc, repo = _service()
    svc.update_by_public_id("rs-2", row_version="AAAA", name="Owner Review")
    repo.update_by_id.assert_called_once()
    assert repo.update_by_id.call_args.args[0].name == "Owner Review"


def test_a_delete_is_judged_on_the_set_without_the_row():
    """Deleting the only final status must fail on the shape, not only on the
    FK — an unreferenced final row would otherwise slip through."""
    rows = _prod_set() + [_status(9, "Spare", 25)]
    svc, repo = _service(rows, references=0)
    with pytest.raises(ReviewStatusShapeError, match="final"):
        svc.delete_by_public_id("rs-3")
    repo.delete_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# The 422 contract
# ---------------------------------------------------------------------------


def test_shape_errors_carry_the_prefix_the_router_maps_on():
    """ProcessEngine folds the exception into `"error": str(e)`, so the message
    prefix is the ONLY structure that survives to the router. If the service
    and `shared/api/responses.py` ever disagree on it, every shape rejection
    silently degrades from 422 `review_status_shape` to a bare 400."""
    from shared.api.responses import REVIEW_STATUS_SHAPE_PREFIX

    svc, _ = _service()
    with pytest.raises(ReviewStatusShapeError) as exc:
        svc.create(name="Impossible", sort_order=40, is_final=True, is_declined=True)
    assert str(exc.value).startswith(REVIEW_STATUS_SHAPE_PREFIX)


def test_the_router_maps_that_prefix_to_422_with_a_machine_code():
    from shared.api.errors import ApiError, ErrorCode
    from shared.api.responses import REVIEW_STATUS_SHAPE_PREFIX, raise_workflow_error

    msg = REVIEW_STATUS_SHAPE_PREFIX + "exactly one active status must be final; found 2."
    with pytest.raises(ApiError) as exc:
        raise_workflow_error(msg, "Failed to create review status")
    assert exc.value.status_code == 422
    assert exc.value.error_code == ErrorCode.REVIEW_STATUS_SHAPE


def test_a_shape_message_containing_already_exists_is_not_hijacked_to_409():
    """409 is what iOS maps to its optimistic-concurrency flow, which DISCARDS
    the queued local edit. A shape message names rows, so it can contain
    arbitrary admin-chosen text — the prefix rule must be checked BEFORE the
    'already exists' -> 409 substring rule."""
    from shared.api.errors import ApiError
    from shared.api.responses import REVIEW_STATUS_SHAPE_PREFIX, raise_workflow_error

    msg = REVIEW_STATUS_SHAPE_PREFIX + "'Already Exists' cannot be both final and declined."
    with pytest.raises(ApiError) as exc:
        raise_workflow_error(msg, "Failed")
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# The sproc contract, pinned against the .sql file
# ---------------------------------------------------------------------------


def test_read_first_keys_on_the_flag_and_excludes_final():
    """The whole point of the unit, pinned at the SQL.

    Reverting `ReadFirstReviewStatus` to the position rule is the one change
    that would re-break everything above while leaving the Python rails intact
    and the suite otherwise green.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    base = REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql"
    body = sproc_body(base, "ReadFirstReviewStatus")

    assert "[IsInitial] = 1" in body, (
        "ReadFirstReviewStatus must key on the IsInitial FLAG — keying on "
        "MIN(SortOrder) is the position-dependency U-444 removed"
    )
    assert "[IsFinal] = 0" in body, (
        "must exclude final rows: the position version did not, so an "
        "initial-and-final row made /submit auto-approve"
    )
    assert "[IsActive] = 1" in body and "[IsDeclined] = 0" in body


def test_create_sproc_still_declares_created_by_user_id():
    """U-444 step 0. This base file was STALE against prod: live
    `CreateReviewStatus` carried `@CreatedByUserId` (added by
    scripts/migrations/gap2_adjacent_threading.sql, 2026-05-07) and the base
    file did not. Re-applying the base as it stood would have DROPPED the param
    from prod and broken every review-status create, because
    ReviewStatusRepository.create() passes it.

    Verified against sys.parameters before the port; pinned here so the base
    cannot drift back.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql"
    params = sproc_params(base, "CreateReviewStatus")
    body = sproc_body(base, "CreateReviewStatus")

    assert "@CreatedByUserId" in params
    assert "COALESCE(@CreatedByUserId, 17)" in body, (
        "the scheduler/system fallback must survive the port"
    )
    assert "@IsInitial" in params


def test_update_sproc_preserves_is_initial_when_not_supplied():
    """`UpdateReviewStatusById` SETs every column unconditionally, so a caller
    predating the param — an old container mid-deploy — would drive a NOT NULL
    column to NULL without the CASE WHEN guard."""
    from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params

    base = REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql"
    assert "@IsInitial BIT = NULL" in sproc_params(base, "UpdateReviewStatusById")
    assert (
        "[IsInitial] = CASE WHEN @IsInitial IS NULL THEN [IsInitial] ELSE @IsInitial END"
        in sproc_body(base, "UpdateReviewStatusById")
    )


def test_the_backfill_is_guarded_and_derivation_based():
    """Two properties that make the cutover safe.

    Guarded: a re-apply of this base file (they get re-run routinely) must
    never move an admin's chosen initial row back.

    Derivation-based: it selects the row the PRE-U-444 ReadFirstReviewStatus
    would have returned, rather than a hardcoded id — which is what makes the
    cutover a provable no-op instead of a claim.
    """
    from tests.sproc_text import REPO_ROOT

    sql = (REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql").read_text()
    backfill = sql.split("IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [IsInitial] = 1)")
    assert len(backfill) == 2, "the backfill must be guarded on 'no initial row exists yet'"
    block = backfill[1].split("GO")[0]
    assert "[IsDeclined] = 0" in block and "[IsActive] = 1" in block
    assert "ORDER BY [SortOrder] ASC" in block
    assert "[Id] = 1" not in block, "must not hardcode the id"


def test_every_sproc_batch_is_go_terminated():
    """The T-SQL batch trap (CLAUDE.md, found in dbo.rolemodule.sql): a
    procedure body runs to the end of the BATCH, not to its matching END, so an
    un-terminated sproc silently swallows whatever is appended after it — both
    mutating that sproc on re-apply and meaning the appended statement never
    runs. U-444 appends a new sproc to this file, so it is now load-bearing.
    """
    from tests.sproc_text import REPO_ROOT

    lines = (REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql").read_text().splitlines()
    open_sproc = None
    for line in lines:
        s = line.strip()
        if s.upper().startswith("CREATE OR ALTER PROCEDURE"):
            assert open_sproc is None, f"{open_sproc} was not GO-terminated before {s}"
            open_sproc = s.split()[-1]
        elif s.upper() == "GO":
            open_sproc = None
    assert open_sproc is None, f"{open_sproc} is the last batch and has no trailing GO"


# ---------------------------------------------------------------------------
# The SQL-side guarantees the Python rails cannot make
# ---------------------------------------------------------------------------


def test_the_strand_check_is_re_run_inside_the_update_transaction():
    """Codex P1, 2026-09-11 — the service's rail alone is a TOCTOU.

    `count_references` runs on its own connection. A Review inserted between
    that check and the UPDATE does not change the ReviewStatus row's RowVersion,
    so optimistic concurrency cannot see it and the service would deactivate on
    stale evidence — producing exactly the stranded state the rail exists to
    prevent. Re-running it under UPDLOCK+HOLDLOCK inside the write's own
    transaction closes the window.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql",
                      "UpdateReviewStatusById")
    # The hints must be on the CHILD predicates (Codex P1). Locking only
    # dbo.[ReviewStatus] leaves the Review insert free to land between the
    # EXISTS and the UPDATE, which is precisely the race being closed.
    assert "FROM dbo.[Review] WITH (UPDLOCK, HOLDLOCK)" in body
    assert "FROM dbo.[ReviewEntry] WITH (UPDLOCK, HOLDLOCK)" in body
    assert "@IsActive = 0 AND @WasActive = 1" in body, (
        "the guard must fire only on a transition INTO inactive"
    )
    # pyodbc runs autocommit-off: an in-sproc ROLLBACK zeroes the outer
    # transaction and raises 266 (CLAUDE.md, 2026-06-11). Checked against the
    # EXECUTABLE text — the comment above the guard says the word too.
    executable = "\n".join(
        line.split("--")[0] for line in body.splitlines()
    ).upper()
    assert "ROLLBACK" not in executable, "signal by COMMIT-then-RAISERROR, never ROLLBACK"
    assert "COMMIT TRANSACTION;\n        RAISERROR" in body


def test_a_fresh_database_comes_up_with_an_initial_status():
    """Codex P1, 2026-09-11 — the fresh-build hole.

    The base file's backfill runs BEFORE the seed, so on an empty table it sets
    nothing. If the seed then inserted all four rows at the column's DEFAULT of
    0, `ReadFirstReviewStatus` would return no row, BillService's auto-Submit
    would silently skip creating any Review, and the new rails would refuse
    every API repair — a brand-new database permanently unable to submit
    anything for review, with no error anywhere.
    """
    from tests.sproc_text import REPO_ROOT

    seed = (REPO_ROOT / "entities/review_status/sql/seed_review_statuses.sql").read_text()
    submitted = seed[seed.index("'Submitted'"):seed.index("'In Review'")]
    assert "[IsInitial]" in submitted and "1, 1, '#2196F3'" in submitted, (
        "the seeded 'Submitted' row must carry IsInitial = 1"
    )
    # ...and a belt-and-braces promotion for databases seeded before U-444
    assert "IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [IsInitial] = 1)" in seed


def test_the_created_by_fk_cannot_abort_a_from_scratch_build():
    """Codex P1, 2026-09-11. Unlike a procedure body — whose table names resolve
    lazily — `ALTER TABLE ... ADD CONSTRAINT ... REFERENCES` resolves
    immediately. Unguarded, it aborts this entire file on a build where
    dbo.[User] does not exist yet."""
    from tests.sproc_text import REPO_ROOT

    sql = (REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql").read_text()
    i = sql.index("FK_ReviewStatus_CreatedByUser")
    guard = sql[max(0, i - 600):i]
    assert "OBJECT_ID('dbo.[User]', 'U') IS NOT NULL" in guard


def test_the_backfill_and_the_new_predicate_cannot_disagree():
    """Codex P1, 2026-09-11 — the cutover-is-a-no-op claim was only true for
    prod's actual data.

    The new `ReadFirstReviewStatus` excludes IsFinal; the position version did
    not. A config whose lowest active non-declined row happened to be FINAL
    would have been marked initial by the backfill and then rejected by the new
    predicate — the sproc returns nothing and auto-submit stops, silently. Both
    now carry the same three predicates.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    base = REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql"
    sql = base.read_text()
    def executable(text):
        """Comments only — the prose around both of these NAMES the clauses, so
        an un-stripped check passes on a body that no longer applies them."""
        return "\n".join(line.split("--")[0] for line in text.splitlines())

    backfill = executable(
        sql.split("IF NOT EXISTS (SELECT 1 FROM dbo.[ReviewStatus] WHERE [IsInitial] = 1)")[1].split("GO")[0]
    )
    predicate = executable(sproc_body(base, "ReadFirstReviewStatus"))

    for clause in ("[IsDeclined] = 0", "[IsActive] = 1", "[IsFinal] = 0"):
        assert clause in backfill, f"backfill is missing {clause}"
        assert clause in predicate, f"ReadFirstReviewStatus is missing {clause}"



def test_the_flag_reaches_the_resolver_from_the_view_through_the_repo():
    """N2/N3 — the only thing linking the SQL projection to the Python model is
    a `getattr(row, "StatusIsInitial", False)` default, so dropping the column
    from `vw_Review` degrades EVERY review to `in_review` in total silence:
    no error, no missing key, just a wrong answer everywhere at once.

    Pin both ends. The view must project it, and the repo must actually read it
    off the row rather than defaulting.
    """
    from types import SimpleNamespace

    from tests.sproc_text import REPO_ROOT

    review_sql = (REPO_ROOT / "entities/review/sql/dbo.review.sql").read_text()
    assert "rs.[IsInitial]  AS [StatusIsInitial]" in review_sql, (
        "vw_Review must project IsInitial — review_status_kind cannot derive "
        "`submitted` without it"
    )
    # ReadCurrentReviewsByBillIds re-lists the columns explicitly instead of
    # SELECT *, so it needs the column named there too.
    assert "[StatusIsDeclined], [StatusIsInitial], [StatusColor]" in review_sql

    from entities.review.persistence.repo import ReviewRepository

    row = SimpleNamespace(
        Id=1, PublicId="p", RowVersion=b"\x00" * 8,
        CreatedDatetime=None, ModifiedDatetime=None,
        ReviewStatusId=1, UserId=1, Comments=None,
        BillId=7, ExpenseId=None, BillCreditId=None, InvoiceId=None,
        ContractLaborId=None, EmailMessageId=None,
        StatusName="Submitted", StatusSortOrder=10,
        StatusIsFinal=False, StatusIsDeclined=False, StatusIsInitial=True,
        StatusColor=None, UserFirstname=None, UserLastname=None,
    )
    review = ReviewRepository()._from_db(row)
    assert review.status_is_initial is True, (
        "the repo must map StatusIsInitial off the row, not default it"
    )

    from shared.lifecycle.resolver import attach_lifecycle

    payload = attach_lifecycle({}, is_draft=True, review=review)
    assert payload["review_status_kind"] == "submitted"


def test_the_sql_backstop_surfaces_as_the_SAME_422_as_the_python_rail():
    """The in-transaction guard is only useful if the client can act on it.

    It fires as a RAISERROR, and `map_database_error` wraps that text — so the
    message no longer STARTS with the prefix `raise_workflow_error` keys on, and
    a genuine race would surface as a generic 400 while the identical rejection
    caught one line earlier gives a 422. The service re-raises it as the real
    exception so the two layers are indistinguishable to the caller.
    """
    from shared.database import DatabaseOperationError

    rows = _prod_set() + [_status(9, "Unused", 25)]
    svc, repo = _service(rows, references=0)  # rail sees zero — the TOCTOU case
    repo.update_by_id.side_effect = DatabaseOperationError(
        "Database operation failed: [42000] [Microsoft][ODBC Driver 17] "
        "Review status configuration is invalid: cannot deactivate a status "
        "that reviews still reference. (50000)"
    )
    with pytest.raises(ReviewStatusShapeError) as exc:
        svc.update_by_public_id("rs-9", row_version="AAAA", is_active=False)
    assert "cannot deactivate" in str(exc.value)

    from shared.api.responses import REVIEW_STATUS_SHAPE_PREFIX, raise_workflow_error
    from shared.api.errors import ApiError

    with pytest.raises(ApiError) as api:
        raise_workflow_error(str(exc.value), "Failed")
    assert api.value.status_code == 422


def test_an_unrelated_database_error_is_not_swallowed_into_a_shape_error():
    """The re-raise must be narrow: a genuine FK/timeout/connection failure has
    to keep its own type and its own status mapping."""
    from shared.database import DatabaseOperationError

    rows = _prod_set() + [_status(9, "Unused", 25)]
    svc, repo = _service(rows, references=0)
    repo.update_by_id.side_effect = DatabaseOperationError("Database operation timed out")
    with pytest.raises(DatabaseOperationError):
        svc.update_by_public_id("rs-9", row_version="AAAA", is_active=False)



def test_the_row_version_gate_runs_BEFORE_any_demotion():
    """Codex P0, 2026-09-11 — order is the whole fix.

    The demotions touch OTHER rows; the final UPDATE is row-version-qualified.
    With the demotions first, a STALE move — admin A transferring the initial
    flag to a status admin B just edited — cleared the incumbent, matched zero
    target rows, and COMMITTED. Result: zero initial statuses.
    `ReadFirstReviewStatus` then returns nothing, and BillService's auto-Submit
    handles "no first status" by logging a warning and SKIPPING the Review — so
    every subsequent bill quietly stops entering review, with nothing raised
    anywhere.

    Asserted as an ordering property because that is what the bug was.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql",
                      "UpdateReviewStatusById")
    gate = body.index("WHERE [Id] = @Id AND [RowVersion] = @RowVersion")
    first_demote = min(
        body.index(f"IF @{flag} = 1") for flag in ("IsInitial", "IsFinal", "IsDeclined")
    )
    assert gate < first_demote, (
        "a demotion runs before the row version is confirmed — a stale update "
        "will strip a singleton role and commit"
    )
    assert "@WasActive IS NULL" in body, "a row-version miss must bail, not fall through"


def test_the_deploy_window_cannot_orphan_the_initial_status():
    """Codex P1, 2026-09-11. The deploy is SQL-first, so for a couple of minutes
    an OLD container writes through these sprocs with no Python rails at all.

    Most of what it could do is recoverable by a later admin edit. Exactly one
    thing is not: marking the INITIAL row final or declined. The new
    `ReadFirstReviewStatus` filters those out, so the set ends up with no first
    status and submissions stop silently. That single-row test is cheap enough
    to enforce in SQL, so it is.
    """
    from tests.sproc_text import REPO_ROOT, sproc_body

    body = sproc_body(REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql",
                      "UpdateReviewStatusById")
    assert "(@IsFinal = 1 OR @IsDeclined = 1)" in body
    assert "the initial status cannot also be final or declined" in body


def test_the_reference_count_survives_ReviewEntry_being_dropped():
    """Codex P2. ReviewEntry is decommissioned. Deferred name resolution lets
    the sproc CREATE against a database that never had it, but the first EXEC
    would fail with "Invalid object name" — taking out the deactivate rail on
    every fresh build."""
    from tests.sproc_text import REPO_ROOT, sproc_body

    base = REPO_ROOT / "entities/review_status/sql/dbo.review_status.sql"
    count_body = sproc_body(base, "CountReviewStatusReferencesById")
    assert "OBJECT_ID('dbo.ReviewEntry', 'U') IS NOT NULL" in count_body
    assert "sp_executesql" in count_body, (
        "a static reference inside a guarded branch still fails to compile at "
        "execution time — the guarded leg must be dynamic"
    )
    assert "OBJECT_ID('dbo.ReviewEntry', 'U') IS NOT NULL" in sproc_body(
        base, "UpdateReviewStatusById"
    )
