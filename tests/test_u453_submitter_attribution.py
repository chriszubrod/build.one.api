"""U-453 — the review timeline stops claiming the submitter moved their own bill.

`ReviewTimeline` (build.one.web `BillEdit.tsx:575`) renders the actor of every
Review row. The row the system writes when it moves a bill into review carried
`review.user_id` — the SUBMITTER — so the timeline showed a human performing a
transition the pipeline performed.

LS-01c′ tried to fix that and REVERTED, correctly: `dbo.inbox_tasks.sql` read
"who submitted this" off the LATEST review row (`Pending` is
`LatestReview WHERE rn = 1`, aliasing `P.[UserId] AS [SubmitterId]`), so
attributing the advance to the system actor would have dropped every in_review
bill out of its submitter's `mine_submitted` scope and rendered "Claude Agent"
as the submitter. The U-357 design instructs the re-attribution and is wrong as
written; Codex caught it before it shipped.

U-453 earns the change by fixing the inbox FIRST. Both halves ship together
because neither is correct alone — that is the invariant this file guards from
both sides.

REHEARSED AGAINST PROD (rolled back, 2026-09-14), which is the only way to see
the interaction, because the two halves live in different languages:

    NEW sproc + a system-owned advance -> mine_submitted 43, probe bill PRESENT,
                                          SubmitterId 17 (Christopher)
    OLD sproc + the same advance       -> mine_submitted 42, probe bill VANISHED

Applying the SQL alone is a no-op on today's data (43/106/2 rows identical
before and after), because every latest row currently IS the submitter. That is
what makes the deploy safe in either order.
"""

import re
from pathlib import Path

import pytest

from shared.authz import SYSTEM_ACTOR_USERNAME

REPO_ROOT = Path(__file__).resolve().parents[1]
INBOX_SQL = REPO_ROOT / "entities/review/sql/dbo.inbox_tasks.sql"


def _executable(path: Path) -> str:
    """SQL with `--` comments stripped.

    Every structural assertion below runs against THIS, not the raw text. The
    prose in this file names the very identifiers being asserted, so an
    un-stripped check would stay green on a revert that left the comments —
    the exact false-confidence shape that recurred through U-446b/c.
    """
    return "\n".join(l.split("--")[0] for l in path.read_text().splitlines())


# ---------------------------------------------------------------------------
# 1 — the inbox resolves the submitter from a submission
# ---------------------------------------------------------------------------


def test_both_inbox_sprocs_resolve_the_submitter_from_the_initial_row():
    sql = _executable(INBOX_SQL)
    assert sql.count("Submitter AS (") == 2, (
        "ReadInboxTasks and ReadInboxTaskCounts must BOTH resolve it — the list "
        "and the badge count diverging is the bug this sproc exists to prevent"
    )
    assert sql.count("WHERE [StatusIsInitial] = 1") == 2, (
        "the submitter is the actor on a row at the INITIAL status, not on "
        "whatever row is newest"
    )


def test_the_latest_row_actor_is_no_longer_read_as_the_submitter():
    """THE defect. `P.[UserId]` is a SUBSTRING of `UP.[UserId]` (the
    UserProject join), so this strips those first — a naive search would
    report a false positive on all 20 of them."""
    sql = _executable(INBOX_SQL).replace("UP.[UserId]", "")
    leftovers = re.findall(r"P\.\[User(?:Id|Firstname|Lastname)\]", sql)
    assert leftovers == [], (
        f"the latest row's actor is still being read as the submitter: {leftovers}"
    )


def test_the_userproject_joins_were_not_collateral_damage():
    """The other side of that substring trap: rewriting `P.[UserId]` without
    anchoring would have turned 20 row-scope predicates into nonsense, silently
    widening or narrowing who can see which tasks."""
    sql = _executable(INBOX_SQL)
    assert sql.count("UP.[UserId] = @CurrentUserId") == 20


def test_mine_submitted_and_its_count_both_key_on_the_submitter():
    sql = _executable(INBOX_SQL)
    assert sql.count("(@Scope = N'mine_submitted' AND P.[SubmitterId] = @CurrentUserId)") == 4, (
        "one per entity arm: Bill, Expense, BillCredit, Invoice"
    )
    assert sql.count("CASE WHEN P.[SubmitterId] = @CurrentUserId THEN 1 ELSE 0 END") == 4


def test_the_submitter_is_the_MOST_RECENT_submission():
    """A declined document is edited in place and resubmitted, so a parent can
    carry several initial rows. The live submission is the last one — its
    author is who is waiting on an answer."""
    sql = _executable(INBOX_SQL)
    submitter_blocks = re.findall(
        r"Submitter AS \((.*?)\n    \),", sql, re.S
    )
    assert len(submitter_blocks) == 2
    for block in submitter_blocks:
        assert "ORDER BY [CreatedDatetime] DESC, [Id] DESC" in block, (
            "DESC, and tie-broken on Id — two rows can share a timestamp"
        )


def test_the_submitter_predicate_selects_initial_rows_not_their_absence():
    """The reviewer's fair criticism of this file, answered where it can be.

    These are STRING assertions over SQL text — a DB-integration harness for
    sprocs does not exist in this repo (CLAUDE.md: "DB-integration tests
    (sprocs/pyodbc) are a separate future unit"), so the semantic proof for
    this unit is the prod rehearsal recorded in the module docstring, not
    these. What a string assertion CAN still catch is the inversion: a
    `Submitter` CTE filtering `= 0` would select every NON-initial row, making
    the submitter whoever last reviewed it — the same class of bug, silently.
    """
    sql = _executable(INBOX_SQL)
    assert "WHERE [StatusIsInitial] = 0" not in sql
    assert sql.count("WHERE [StatusIsInitial] = 1") == 2
    # and the flag must be the ONLY thing selecting the submitter row — an
    # added SortOrder predicate would reintroduce the positional keying U-444
    # removed.
    for block in re.findall(r"Submitter AS \((.*?)\n    \),", sql, re.S):
        assert "SortOrder" not in block, (
            "submitter selection must key on the IsInitial flag, not position"
        )


def test_pending_does_not_carry_the_latest_rows_actor_at_all():
    """Removing it beats renaming it. `Pending` used to `SELECT L.*`, which
    carried [UserId]/[UserFirstname]/[UserLastname] alongside the new
    [Submitter*] columns — and the latest actor is now usually the system
    actor, so picking the wrong one would read as "submitted by Claude Agent"
    rather than as an error."""
    sql = _executable(INBOX_SQL)
    pendings = re.findall(r"Pending AS \((.*?)\n    \),", sql, re.S)
    assert len(pendings) == 2
    for block in pendings:
        assert "L.*" not in block, "explicit projection, not SELECT *"
        for leaked in ("L.[UserId]", "L.[UserFirstname]", "L.[UserLastname]"):
            assert leaked not in block, f"{leaked} is back in Pending"


def test_a_parent_with_no_initial_row_still_appears_in_the_inbox():
    """LEFT JOIN, not INNER.

    Re-flagging [IsInitial] onto a different status after rows exist can leave
    a document whose review rows are all non-initial. It keeps its place in the
    `mine` and `all` scopes with a NULL submitter rather than vanishing from
    the inbox entirely.

    Precise about what this does NOT save: such a document still drops out of
    `mine_submitted` for everyone, because `NULL = @CurrentUserId` is never
    true. That is the honest answer — nobody's submission is on file — and it
    beats the row disappearing from every scope. (The original docstring here
    claimed it "still appears in the inbox" full stop, and said "an admin
    reordering statuses" causes it; both were wrong, and the independent
    reviewer caught them.)
    """
    sql = _executable(INBOX_SQL)
    assert sql.count("LEFT JOIN Submitter S") == 2
    assert "INNER JOIN Submitter" not in sql


# ---------------------------------------------------------------------------
# 2 — the partition key, and the trap that was armed in it
# ---------------------------------------------------------------------------


def test_the_partition_key_covers_every_review_parent():
    """`dbo.Review` has five parent FKs. ContractLabor had no branch, so all
    652 CL rows keyed to NULL, shared ONE partition, and exactly one survived
    `rn = 1`. Harmless only because the Rows/Tagged arms have no ContractLabor
    arm — a trap armed for whoever adds one."""
    sql = _executable(INBOX_SQL)
    for prefix, column in (("B", "BillId"), ("E", "ExpenseId"), ("C", "BillCreditId"),
                           ("I", "InvoiceId"), ("L", "ContractLaborId")):
        assert sql.count(f"CONCAT(N'{prefix}', r.[{column}])") == 2, (
            f"{column} is missing from a partition key — its rows would collapse "
            "into the NULL partition and all but one would disappear"
        )


def test_the_partition_key_is_defined_once_per_sproc():
    """It used to be written inline inside each ROW_NUMBER. Two copies of the
    same CASE is how the ContractLabor branch came to be missing from one place
    and not another."""
    sql = _executable(INBOX_SQL)
    assert sql.count("END AS [ParentKey]") == 2
    assert sql.count("PARTITION BY [ParentKey]") == 4, (
        "two window functions per sproc, both keyed off the single definition"
    )


# ---------------------------------------------------------------------------
# 3 — the Python half
# ---------------------------------------------------------------------------


def test_the_auto_advance_is_attributed_to_the_system_actor():
    import inspect

    from entities.review.business.notification_service import ReviewNotificationService

    src = inspect.getsource(ReviewNotificationService._advance_to_in_review)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "user_id=actor" in executable
    assert "user_id=review.user_id" not in executable
    assert "system_actor_user_id()" in executable, (
        "resolved by username at runtime — a hard-coded id is correct in prod "
        "and potentially a HUMAN in any other database (Codex P1)"
    )
    assert SYSTEM_ACTOR_USERNAME == "claude_agent"


# ---------------------------------------------------------------------------
# 4 — the halves are joined at the hip
# ---------------------------------------------------------------------------


def test_neither_half_can_ship_without_the_other():
    """The unit's whole premise, asserted as one statement.

    Python writing the system actor while the SQL reads the latest row = every
    in_review bill leaves its submitter's sent box (proved against prod: 43 ->
    42, probe bill gone). SQL reading the initial row while Python writes the
    submitter = merely redundant, but it silently un-guards the first case. So
    a revert of EITHER half must fail, and it fails here.
    """
    import inspect

    from entities.review.business.notification_service import ReviewNotificationService

    py = "\n".join(
        l.split("#")[0]
        for l in inspect.getsource(ReviewNotificationService._advance_to_in_review).splitlines()
    )
    sql = _executable(INBOX_SQL)

    python_writes_system_actor = "user_id=actor" in py
    sql_reads_initial_row = sql.count("Submitter AS (") == 2

    assert python_writes_system_actor == sql_reads_initial_row, (
        "one half of U-453 has been reverted without the other. Python writing "
        "the system actor needs the SQL that resolves the submitter from the "
        "initial row; without it, every in_review bill silently drops out of "
        "its submitter's mine_submitted scope."
    )
    assert python_writes_system_actor, "both halves must be present, not both absent"
