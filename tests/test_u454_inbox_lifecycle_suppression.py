"""U-454 — finished work leaves the inbox, and a finished document's review closes.

Two halves of one problem: the task inbox listed documents whose reviews no
longer mean anything, and the review transitions let you act on them.

MEASURED IN PROD when this shipped: of 111 tasks in the admin view, **69 sat on
already-completed documents** — 64 Bills and all 5 Invoices. The inbox was 62%
finished work. And because `build_submit/advance/decline_payload` validated only
the REVIEW's state, never the parent's, every one of those 64 completed Bills
could still be advanced — or *declined*, after its money had already reached
QBO, SharePoint, Excel and Box. U-446b locked EDITS to a completed Bill; it
never covered the review transitions.

REHEARSED AGAINST PROD (rolled back):
    admin 'all' view                111 tasks -> 42
    removed                         69, of which every Bill was IsDraft=0
    list vs badge Total             agree for all 12 (user x admin) combinations

That last line is why the counts sproc needed structural work and not just a
predicate: its Bill and BillCredit arms read straight off `Pending` with no
parent join at all, so filtering only the list would have left the badge
counting 64 documents the list no longer showed.

`IsDraft = 1` is the predicate, not `Status <> 'completed'`: identical on Bill
(IsDraft is PERSISTED COMPUTED over Status since U-446) and the only one that
exists on Expense/BillCredit/Invoice, whose Status columns are LS-03b/c/d and
NOT BUILT.
"""

import inspect
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INBOX_SQL = REPO_ROOT / "entities/review/sql/dbo.inbox_tasks.sql"
ENTITIES = (("Bill", "B"), ("Expense", "E"), ("BillCredit", "BC"), ("Invoice", "I"))


def _executable(path: Path) -> str:
    """SQL with `--` comments stripped — the prose below names the very
    identifiers being asserted, so an un-stripped check would stay green on a
    revert that left the comments behind."""
    return "\n".join(l.split("--")[0] for l in path.read_text().splitlines())


# ---------------------------------------------------------------------------
# 1 — the list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entity,alias", ENTITIES)
def test_every_list_arm_requires_an_open_parent(entity, alias):
    sql = _executable(INBOX_SQL)
    arm = sql.split(f"@EntityType = N'{entity}')", 1)[1].split("UNION ALL", 1)[0]
    assert f"AND {alias}.[IsDraft] = 1" in arm, (
        f"the {entity} arm still lists tasks on finished documents"
    )


def test_the_predicate_is_is_draft_not_a_status_comparison():
    """BillCredit and Invoice have NO Status column — LS-03b/d are
    not built. Expense now has Status (U-467) but this inbox arm stays on
    IsDraft so the four parents share one predicate. A Status comparison
    must not appear in this file."""
    sql = _executable(INBOX_SQL)
    assert "[Status] <> 'completed'" not in sql
    assert "[Status] != 'completed'" not in sql
    assert sql.count("[IsDraft] = 1") == 8, "four list arms + four count arms"


# ---------------------------------------------------------------------------
# 2 — the badge, which is the half that is easy to forget
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entity,alias,fk", [
    ("Bill", "B", "BillId"), ("Expense", "E", "ExpenseId"),
    ("BillCredit", "BC", "BillCreditId"), ("Invoice", "I", "InvoiceId"),
])
def test_every_count_arm_joins_its_parent_and_filters_on_it(entity, alias, fk):
    """The Bill and BillCredit arms had NO parent join — they read straight off
    `Pending`. Filtering only the list would have left the badge counting the
    64 finished Bills the list no longer shows."""
    sql = _executable(INBOX_SQL)
    assert f"INNER JOIN dbo.[{entity}] {alias} ON {alias}.[Id] = P.[{fk}]" in sql
    assert f"WHERE P.[{fk}] IS NOT NULL AND {alias}.[IsDraft] = 1" in sql


def test_no_count_arm_reads_bare_from_pending():
    """The shape that made the divergence possible in the first place."""
    sql = _executable(INBOX_SQL)
    bare = re.findall(r"FROM Pending P WHERE P\.\[\w+\] IS NOT NULL\s*$", sql, re.M)
    assert bare == [], f"a count arm still has no parent join: {bare}"


# ---------------------------------------------------------------------------
# 3 — the review transitions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("builder", [
    "build_submit_payload", "build_advance_payload", "build_decline_payload",
])
def test_every_transition_builder_refuses_a_finished_parent(builder):
    from entities.review.business.service import ReviewService

    src = inspect.getsource(getattr(ReviewService, builder))
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "_assert_parent_open(" in executable, (
        f"{builder} still validates only the review's state, not the parent's — "
        "a completed document's review stays actionable"
    )


def test_the_guard_reads_the_parent_already_in_hand():
    """`_resolve_parent_id` had the whole row and threw it away. The guard must
    not pay for a second fetch per transition."""
    from entities.review.business.service import ReviewService

    for builder in ("build_submit_payload", "build_advance_payload", "build_decline_payload"):
        src = inspect.getsource(getattr(ReviewService, builder))
        executable = "\n".join(l.split("#")[0] for l in src.splitlines())
        assert "_resolve_parent(" in executable
        assert "_resolve_parent_id(" not in executable, (
            "that would re-read the parent the guard already resolved"
        )


def test_contract_labor_is_guarded_by_translation_not_by_exemption():
    """SUPERSEDED within the unit — the first cut EXCLUDED ContractLabor.

    The reasoning was that CL speaks pending_review / ready / billed, so
    `is_terminal` would compare 'billed' against 'completed', return False and
    fail OPEN — a guard that looks applied and is not. True, but the conclusion
    was wrong: a different vocabulary is a reason to write a different
    predicate, not a reason to leave the entity unguarded. CL has public
    submit/advance/decline routes and 1,193 rows sitting at 'billed'.

    Both terminal spellings are accepted because
    `2026_07_02_unify_labor_status_vocab.sql` rewrites 'billed' to 'completed'
    at the LS-04 cutover — so the guard survives that migration untouched.
    """
    from entities.review.business.service import ReviewService

    assert ReviewService._CONTRACT_LABOR_TERMINAL == ("billed", "completed")

    src = inspect.getsource(ReviewService._assert_parent_open)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "_LIFECYCLE_GUARDED_PARENTS" not in executable, (
        "no parent type is skipped any more"
    )
    assert "_CONTRACT_LABOR_TERMINAL" in executable
    assert "is_draft = None" in executable, (
        "ContractLabor has no IsDraft column — passing one would make "
        "`is_terminal` fall back to a column that does not exist"
    )


def test_the_guard_reads_both_lifecycle_shapes():
    """Bill and Expense have `status`; BillCredit/Invoice have only `is_draft`;
    ContractLabor has only `status`, in its own vocabulary. Reading just one of
    the two would make the guard a silent no-op on most of the fleet."""
    from entities.review.business.service import ReviewService

    src = inspect.getsource(ReviewService._assert_parent_open)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert 'getattr(parent, "is_draft", None)' in executable
    assert 'getattr(parent, "status", None)' in executable


# ---------------------------------------------------------------------------
# 4 — how the refusal reaches the client
# ---------------------------------------------------------------------------


def test_a_locked_parent_is_422_not_500():
    """`StatusLockedError` subclasses PermissionError, so before U-454 wired a
    handler it sailed past both `except` clauses in `_do_action` and surfaced
    as an unhandled 500."""
    import entities.review.api.router as review_router

    src = inspect.getsource(review_router._do_action)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "except StatusLockedError" in executable
    assert "raise_workflow_error" in executable


def test_the_refusal_is_not_folded_into_the_409_arm():
    """Installed iOS routes 409 to its reload-and-retry CONFLICT path, so a
    completed parent would make the client spin on a refusal that will never
    change. 409 means "try again"; this means "never again".

    Ordering is NOT what guarantees that, and an earlier version of this
    docstring claimed it was. The two exception types are unrelated —
    `StatusLockedError` derives from `PermissionError`, `ReviewTransitionError`
    from `Exception` — so neither clause can ever swallow the other whatever
    order they sit in. What actually matters is that a distinct clause EXISTS
    and routes through `raise_workflow_error`; the ordering assertion below is
    kept only as a cheap tripwire against someone merging the two arms.
    """
    import entities.review.api.router as review_router
    from entities.review.business.service import ReviewTransitionError
    from shared.lifecycle.terminal_lock import StatusLockedError

    assert not issubclass(StatusLockedError, ReviewTransitionError)
    assert not issubclass(ReviewTransitionError, StatusLockedError)

    src = inspect.getsource(review_router._do_action)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "except StatusLockedError" in executable
    assert executable.index("except StatusLockedError") < executable.index(
        "except ReviewTransitionError"
    )


def test_the_message_prefix_actually_resolves_to_422():
    """The status comes from a message-PREFIX registry, not from the exception
    type, so a reworded message silently becomes a 500."""
    from shared.api.responses import _WORKFLOW_STATUS_BY_PREFIX
    from shared.lifecycle.terminal_lock import StatusLockedError

    msg = str(StatusLockedError("its review cannot be declined once completed"))
    matched = [row for row in _WORKFLOW_STATUS_BY_PREFIX if msg.startswith(row[0])]
    assert matched, (
        "the message no longer matches any prefix — it would fall through to "
        "the generic 400, which iOS treats as terminal but which tells the user "
        "nothing about WHY. (An earlier version of this comment said 500; "
        "`raise_workflow_error`'s fallback is 400.)"
    )
    assert matched[0][1] == 422


# ---------------------------------------------------------------------------
# 5 — the path this unit does NOT cover, and why that is fine
# ---------------------------------------------------------------------------


def test_the_email_reply_path_keeps_its_own_finished_parent_gate():
    """`apply_reviewer_decision` deliberately BYPASSES the payload builders — a
    PM's emailed approval IS the approval, so it jumps straight to the terminal
    status rather than walking the one-step-per-reply chain. That means
    U-454's guard does not cover it.

    Verified rather than assumed: it carries its own gate, predating this unit,
    refusing when the Bill is no longer a draft. This pins that gate, because
    removing it would open exactly the hole U-454 closed everywhere else — and
    the email reply is how reviewers actually approve in practice.
    """
    from entities.bill.business.service import BillService

    src = inspect.getsource(BillService.apply_reviewer_decision)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "if not bool(bill.is_draft):" in executable
    assert "is no longer a draft" in executable


def test_every_review_write_path_is_now_guarded_at_the_sproc():
    """Codex P2 replaced an inventory that gave false assurance.

    The old version grepped only the literal `ReviewService().create(` and
    concluded the write paths were accounted for. They were not: it missed
    `ReviewService.create`'s own `self.repo.create`, and it missed the email
    decision path's DIRECT `ReviewRepository().create(...)` in
    `BillService.apply_reviewer_decision` — which bypasses the service entirely.

    Enumerating call sites was the wrong idea anyway. The guard now lives in
    `CreateReview` itself, which every one of those paths must go through, so
    what matters is that the sproc refuses — not how many callers there are.
    """
    from tests.sproc_text import REPO_ROOT as SPROC_ROOT, sproc_body

    body = sproc_body(SPROC_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    executable = "\n".join(l.split("--")[0] for l in body.splitlines())
    guard, insert = executable.index("UPDLOCK"), executable.index("INSERT INTO")
    assert guard < insert, (
        "the parent check must run BEFORE the INSERT, not after — otherwise the "
        "row is already written when the refusal fires"
    )
    assert "STATUS_LOCKED:" in executable[:insert]
    assert executable.count("UPDLOCK, HOLDLOCK") == 5, (
        "one locked read per parent: Bill, Expense, BillCredit, Invoice AND "
        "ContractLabor — every type dbo.Review can point at"
    )


def test_the_direct_email_decision_writer_goes_through_the_same_sproc():
    """`apply_reviewer_decision` writes via `ReviewRepository().create(...)`,
    skipping `ReviewService` and therefore skipping the service-layer guard. It
    still reaches `CreateReview`, so the sproc-level lock covers it — which is
    the whole reason the guard belongs there rather than in the service."""
    import inspect as _i

    from entities.bill.business.service import BillService

    src = _i.getsource(BillService.apply_reviewer_decision)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "ReviewRepository().create(" in executable


def test_the_sproc_refusal_is_translated_not_flattened_into_a_500():
    """Without `reraise_if_sproc_status_locked`, the RAISERROR reaches
    `map_database_error` and surfaces as a generic 500 — indistinguishable from
    a real database fault and routed nowhere near the `status_locked` contract."""
    import inspect as _i

    from entities.review.persistence.repo import ReviewRepository

    src = _i.getsource(ReviewRepository.create)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "reraise_if_sproc_status_locked(" in executable
    assert executable.index("reraise_if_sproc_status_locked(") < executable.index(
        "raise map_database_error(error)"
    ), "it must run BEFORE map_database_error flattens the token"


def _create_review_sql() -> str:
    from tests.sproc_text import REPO_ROOT as SPROC_ROOT, sproc_body

    body = sproc_body(SPROC_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    return "\n".join(l.split("--")[0] for l in body.splitlines())


def test_the_exemption_flag_appears_exactly_once_and_only_in_the_refusal():
    """LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY — asserted structurally.

    U-446c's own scan looks for `IF @AllowTerminalParent = 0` without an ` AND `
    on the same line, on the theory that a compound condition must be testing a
    value already computed under the lock. That heuristic is defeatable:
    `IF @AllowTerminalParent = 0 AND @BillId IS NOT NULL` around the locked read
    satisfies it and still gates the lock on the exemption — which is precisely
    the bug, since an exempt writer then takes no lock and serializes against
    nothing.

    The flag appearing exactly twice — the declaration and the refusal — is not
    defeatable that way.
    """
    sql = _create_review_sql()
    occurrences = sql.count("@AllowTerminalParent")
    assert occurrences == 2, (
        f"expected the parameter declaration and the refusal condition, found "
        f"{occurrences}; a third means the exemption gates something else, most "
        "likely the lock itself"
    )
    assert "IF @AllowTerminalParent = 0 AND @LockedFinished > 0" in sql
    for line in sql.splitlines():
        if "UPDLOCK" in line:
            assert "@AllowTerminalParent" not in line


def test_the_refusal_actually_tests_the_locked_result():
    """`IF 1 = 0` keeps the lock, the token, and the ordering — and refuses
    nothing. Position assertions alone cannot see that."""
    sql = _create_review_sql()
    assert "IF @AllowTerminalParent = 0 AND @LockedFinished > 0" in sql
    assert sql.count("@LockedFinished = COUNT(*)") == 5, "one per parent type"


def test_the_sproc_commits_before_raising_and_never_rolls_back():
    """pyodbc runs autocommit-off, so a ROLLBACK inside a sproc zeroes the
    implicit OUTER transaction and SQL Server raises error 266 —
    "Transaction count after EXECUTE…" — instead of the refusal. The caller
    then sees a database fault, and `reraise_if_sproc_status_locked` never sees
    its token."""
    sql = _create_review_sql()
    assert "ROLLBACK" not in sql.upper()
    assert "COMMIT TRANSACTION;\n        RAISERROR('STATUS_LOCKED:" in sql


def test_the_guard_counts_FINISHED_parents_not_open_ones():
    """The two predicates in this unit are mirror images, and swapping them is
    silent. The inbox filters `IsDraft = 1` — KEEP the open ones. The guard
    counts `IsDraft = 0` — REFUSE when a finished one is found. Flip the guard
    and it inverts completely, while the lock, token, ordering and shape all
    stay exactly as they were."""
    sql = _create_review_sql()
    assert sql.count("AND [IsDraft] = 0;") == 4, (
        "the four IsDraft entities; ContractLabor uses its own Status predicate"
    )
    assert "AND [IsDraft] = 1;" not in sql, (
        "that is the INBOX's predicate (keep open), not the guard's (refuse "
        "finished) — they are mirror images and this one is inverted"
    )
    assert "AND [Status] IN ('billed', 'completed');" in sql, (
        "ContractLabor's terminal states, both spellings"
    )


def test_nothing_passes_the_exemption_today():
    """SUPERSEDED within the unit — the first cut wired
    `allow_terminal_parent=is_exempt()` by analogy with the other terminal-lock
    guards.

    That was a bypass with no beneficiary (Codex P2): every `system_authz()`
    context would have been allowed to write a review onto a completed
    document, and NO outbox, scheduler or CLI path needs to. The one direct
    writer, `BillService.apply_reviewer_decision`, refuses completed Bills
    itself. The sproc keeps its `@AllowTerminalParent` parameter so a future
    caller can opt in explicitly and by name — but a capability nothing uses
    must not be wired on by default, least of all on the guard whose entire
    purpose is to refuse.
    """
    from entities.review.business.service import ReviewService

    src = inspect.getsource(ReviewService.create)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "allow_terminal_parent" not in executable, (
        "nothing may pass the exemption until a concrete caller needs it"
    )
    assert "is_exempt" not in executable


def test_the_sproc_still_offers_the_exemption_for_a_future_named_caller():
    """Removing the wiring is not the same as removing the capability. The
    parameter stays, fail-closed, so the escape hatch exists the day something
    genuinely needs it — without anyone having to redesign the guard."""
    sql = _create_review_sql()
    assert "@AllowTerminalParent BIT = 0" in sql
    assert "IF @AllowTerminalParent = 0 AND @LockedFinished > 0" in sql
