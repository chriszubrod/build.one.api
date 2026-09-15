"""U-455 — a review's kind is frozen at insert, not re-derived from live config.

`review_kind_from_flags` resolved a row's kind from the ReviewStatus flags AT
READ TIME, which makes stored history a function of current configuration.
`UpdateReviewStatus` clears `[IsInitial]` from every other status when the
initial role is transferred (`dbo.review_status.sql`:
`UPDATE … SET [IsInitial] = 0 WHERE [IsInitial] = 1 AND [Id] <> @Id`), so an
admin moving that flag rewrites the past.

U-444 fixed this class once already, moving the boundary from POSITION
(SortOrder) to a FLAG. That removed one failure mode and left the other: a flag
is still current config, and history must not be.

MEASURED AGAINST PROD (rolled back), transferring the initial role from
"Submitted" to "In Review":

    shipped (live flag)   8 of 42 tasks lose their submitter ENTIRELY — it
                          becomes NULL, because those bills have no row at the
                          relocated flag's status. They stay in `all` and drop
                          out of `mine_submitted` for everyone.
    U-455 (frozen kind)   0 changed, 0 nulled.

Note the shape: the task COUNT is unchanged either way. An earlier framing of
this bug said "sent-box rows disappear"; the mechanism is NULL-ing the
submitter, not removing the row, and it hits exactly the subset with no
intermediate row. A count-based check cannot see it.

⏳ THE BACKFILL WAS ONLY DERIVABLE WHILE THE FLAGS WERE UNMOVED. It reproduces
what the read path computes today — proven at cutover over all 1,700 live rows,
zero mismatches. Once a flag moves, the true history is gone; that is why this
column was added when it was rather than when it was next convenient.
"""

import inspect
import re
from pathlib import Path

import pytest

from shared.lifecycle import REVIEW_STATUS_KINDS, review_kind_from_flags

REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEW_SQL = REPO_ROOT / "entities/review/sql/dbo.review.sql"
INBOX_SQL = REPO_ROOT / "entities/review/sql/dbo.inbox_tasks.sql"

ROW_KINDS = ("submitted", "in_review", "approved", "declined")


def _executable(path: Path) -> str:
    return "\n".join(l.split("--")[0] for l in path.read_text().splitlines())


# ---------------------------------------------------------------------------
# 1 — the SQL and the Python must agree, exactly, forever
# ---------------------------------------------------------------------------


def _sql_kind_case() -> str:
    """The CASE expression `CreateReview` uses to stamp the kind."""
    sql = _executable(REVIEW_SQL)
    block = sql[sql.index("SELECT @ReviewKind ="):]
    return block[:block.index("FROM dbo.[ReviewStatus]")]


def _sql_kind_mapping() -> list:
    """The CASE's (flag, value) pairs, in order, as the SQL actually maps them.

    Parsing the MAPPING rather than the flag ORDER (Codex P2): the previous
    version asserted only that `IsDeclined` appeared before `IsFinal` before
    `IsInitial`, so rewriting a branch to return the WRONG kind —
    `WHEN rs.[IsDeclined] = 1 THEN N'approved'` — sailed through.
    """
    case = _sql_kind_case()
    return re.findall(r"WHEN\s+rs\.\[(\w+)\]\s*=\s*1\s+THEN\s+N'(\w+)'", case)


def test_the_sql_maps_each_flag_to_the_kind_python_would_return():
    """The precedence lives in two places — the sproc stamps it, the resolver
    falls back to it — and they must never diverge."""
    mapping = _sql_kind_mapping()
    assert mapping == [
        ("IsDeclined", "declined"),
        ("IsFinal", "approved"),
        ("IsInitial", "submitted"),
    ], f"SQL maps flags to kinds as {mapping}"

    # and each pair is what the Python returns when that flag is the winner
    assert review_kind_from_flags(is_declined=True, is_final=True, is_initial=True) == "declined"
    assert review_kind_from_flags(is_declined=False, is_final=True, is_initial=True) == "approved"
    assert review_kind_from_flags(is_declined=False, is_final=False, is_initial=True) == "submitted"


@pytest.mark.parametrize("is_declined", [True, False])
@pytest.mark.parametrize("is_final", [True, False])
@pytest.mark.parametrize("is_initial", [True, False])
def test_python_and_the_sql_precedence_agree_on_all_eight_combinations(
    is_declined, is_final, is_initial
):
    """Eight boolean combinations; the NULL case is separate below. (An earlier
    docstring claimed sixteen — the flags are BIT NOT NULL, so eight plus the
    defensive NULL case is the real space.)"""
    winner = next(
        (kind for flag, kind in _sql_kind_mapping()
         if {"IsDeclined": is_declined, "IsFinal": is_final, "IsInitial": is_initial}[flag]),
        "in_review",
    )
    assert review_kind_from_flags(
        is_declined=is_declined, is_final=is_final, is_initial=is_initial
    ) == winner, "the SQL CASE and the Python resolver disagree on this combination"


def test_none_flags_fall_through_to_in_review_in_both():
    """The 16th case: a status with nothing set. SQL's ELSE and Python's final
    return must both land on `in_review`."""
    assert review_kind_from_flags(is_declined=None, is_final=None, is_initial=None) == "in_review"
    assert "ELSE N'in_review'" in _sql_kind_case()


def test_the_stamped_vocabulary_is_the_row_level_subset():
    """`none` is REVIEW_STATUS_KINDS' answer for a document with NO review row,
    so it cannot be the kind OF a row. The CHECK must not admit it."""
    sql = _executable(REVIEW_SQL)
    assert "[ReviewKind] IN (N'submitted', N'in_review', N'approved', N'declined')" in sql
    assert "CHECK ([ReviewKind] IS NOT NULL" in sql, (
        "a CHECK evaluates `NULL IN (...)` as UNKNOWN, which PASSES — so the "
        "constraint must reject NULL explicitly, not lean on the column"
    )
    assert set(ROW_KINDS) < set(REVIEW_STATUS_KINDS)
    assert "none" in REVIEW_STATUS_KINDS and "none" not in ROW_KINDS


# ---------------------------------------------------------------------------
# 2 — the column, and the order the schema steps must run in
# ---------------------------------------------------------------------------


def test_the_column_is_added_nullable_backfilled_then_made_not_null():
    """Order is the whole safety of this migration. Adding it NOT NULL with a
    DEFAULT would stamp every existing row with a single wrong kind before the
    backfill could reach them — and `in_review` on 764 submissions is exactly
    the corruption this unit exists to prevent."""
    sql = _executable(REVIEW_SQL)
    add = sql.index("ADD [ReviewKind] NVARCHAR(20) NULL")
    backfill = sql.index("SET r.[ReviewKind] =")
    notnull = sql.index("ALTER COLUMN [ReviewKind] NVARCHAR(20) NOT NULL")
    check = sql.index("CK_Review_ReviewKind")
    assert add < backfill < notnull < check
    assert "DEFAULT" not in sql[add:backfill], "no DEFAULT — there is no safe one"


def test_every_schema_step_is_guarded_so_re_running_the_file_is_a_no_op():
    """Base files are re-applied routinely; each step must be idempotent."""
    sql = _executable(REVIEW_SQL)
    assert "COL_LENGTH('dbo.Review', 'ReviewKind') IS NULL" in sql
    assert "WHERE r.[ReviewKind] IS NULL" in sql, "the backfill must not re-stamp"
    assert "AND is_nullable = 1" in sql, "the NOT NULL step must not re-run"
    # The NOT NULL step re-sweeps and then FAILS LOUDLY rather than skipping.
    # Skipping is what made the window silent: the deploy reported success and
    # left rows deriving from live config (Codex P1).
    assert sql.count("WHERE r.[ReviewKind] IS NULL;") == 2, (
        "backfill runs twice — once up front, once immediately before the "
        "NOT NULL, to catch rows the old writer inserted in between"
    )
    assert "RAISERROR('U-455: [Review].[ReviewKind] still has NULL rows" in sql, (
        "a migration that cannot establish its invariant must fail, not shrug"
    )
    assert "WHERE name = 'CK_Review_ReviewKind'" in sql


def test_the_backfill_derives_from_the_same_flags_the_read_path_used():
    """That equivalence is what makes the cutover a no-op: the stored value
    reproduces the derived one for every existing row."""
    sql = _executable(REVIEW_SQL)
    backfill = sql[sql.index("SET r.[ReviewKind] ="):sql.index("WHERE r.[ReviewKind] IS NULL")]
    for flag in ("IsDeclined", "IsFinal", "IsInitial"):
        assert flag in backfill


# ---------------------------------------------------------------------------
# 3 — the writer
# ---------------------------------------------------------------------------


def test_create_review_stamps_the_kind_rather_than_leaving_it_to_a_default():
    from tests.sproc_text import REPO_ROOT as SPROC_ROOT, sproc_body

    body = sproc_body(SPROC_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    executable = "\n".join(l.split("--")[0] for l in body.splitlines())
    assert "[ReviewKind]" in executable
    assert "@ReviewKind" in executable
    stamp = executable.index("SELECT @ReviewKind =")
    insert = executable.index("INSERT INTO dbo.[Review]")
    assert stamp < insert, "resolve before inserting, not after"


def test_an_unknown_review_status_is_named_rather_than_left_to_the_fk():
    """`@ReviewKind` would be NULL and the NOT NULL column would reject the row
    with a constraint violation that names a column, not the cause."""
    from tests.sproc_text import REPO_ROOT as SPROC_ROOT, sproc_body

    body = sproc_body(SPROC_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    executable = "\n".join(l.split("--")[0] for l in body.splitlines())
    assert "IF @ReviewKind IS NULL" in executable
    assert "does not exist" in executable
    # COMMIT-then-RAISERROR, never ROLLBACK (pyodbc autocommit-off -> error 266)
    guard = executable[executable.index("IF @ReviewKind IS NULL"):]
    assert guard.index("COMMIT TRANSACTION;") < guard.index("RAISERROR")


# ---------------------------------------------------------------------------
# 4 — the readers
# ---------------------------------------------------------------------------


def test_the_read_path_prefers_the_frozen_kind():
    from shared.lifecycle import resolver

    src = inspect.getsource(resolver.attach_lifecycle)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert 'getattr(review, "review_kind", None) or review_kind_from_flags(' in executable, (
        "stored first, derived only as a fallback"
    )


def test_the_fallback_still_exists_and_is_not_dead_code():
    """`attach_lifecycle` is pure logic and takes whatever object a caller
    hands it. A partially-populated Review must still resolve rather than
    reporting None — and parity at cutover means the fallback returns the same
    answer it always did."""
    from types import SimpleNamespace

    from shared.lifecycle import attach_lifecycle

    payload = attach_lifecycle(
        {}, review=SimpleNamespace(
            status_is_declined=False, status_is_final=False, status_is_initial=True,
            status_name="Submitted",
        ), stored_status=None, is_draft=True,
    )
    assert payload["review_status_kind"] == "submitted"


def test_a_frozen_kind_wins_over_the_live_flags():
    """The point of the unit, at the read path. A row stamped `submitted` stays
    `submitted` even when the status it points at no longer carries IsInitial."""
    from types import SimpleNamespace

    from shared.lifecycle import attach_lifecycle

    payload = attach_lifecycle(
        {}, review=SimpleNamespace(
            review_kind="submitted",
            # the flags have since moved on — this row must not follow them
            status_is_declined=False, status_is_final=False, status_is_initial=False,
            status_name="Submitted",
        ), stored_status=None, is_draft=True,
    )
    assert payload["review_status_kind"] == "submitted", (
        "the live flags say in_review; the frozen value says submitted, and it wins"
    )


def test_the_inbox_resolves_the_submitter_from_the_frozen_kind():
    sql = _executable(INBOX_SQL)
    assert sql.count("WHERE [ReviewKind] = N'submitted'") == 2, (
        "both sprocs — the list and the badge count"
    )
    assert "WHERE [StatusIsInitial] = 1" not in sql, (
        "that is the mutable basis U-455 replaced"
    )


def test_the_model_and_the_view_both_carry_it():
    import dataclasses

    from entities.review.business.model import Review

    assert "review_kind" in {f.name for f in dataclasses.fields(Review)}
    assert "r.[ReviewKind]," in _executable(REVIEW_SQL), "vw_Review must expose it"

    src = inspect.getsource(
        __import__("entities.review.persistence.repo", fromlist=["ReviewRepository"])
    )
    assert 'review_kind=getattr(row, "ReviewKind", None)' in src


def test_the_status_flags_are_still_exposed_for_their_legitimate_uses():
    """`StatusIsFinal` / `StatusIsDeclined` remain the CURRENT shape, which the
    inbox's Pending filter and the status admin UI genuinely want. Only the
    KIND of a row is frozen."""
    sql = _executable(REVIEW_SQL)
    for flag in ("StatusIsFinal", "StatusIsDeclined", "StatusIsInitial"):
        assert f"AS [{flag}]" in sql


def test_no_vw_review_reader_silently_drops_the_frozen_column():
    """Codex P1 — and the failure mode is silence.

    `ReadCurrentReviewsByBillIds` is the ONE reader with an explicit column
    list; every other one is `SELECT *` and picks the column up for free.
    Omitting it there mapped to `None` in the repo, so the resolver fell back to
    live flags — the Bill LIST re-deriving history while every single GET used
    the frozen value. The unit inert on its busiest consumer, with nothing to
    notice.

    Scanned generically so the next explicit projection is covered the day it
    is written.
    """
    import re as _re

    sql = REVIEW_SQL.read_text()
    offenders = []
    for m in _re.finditer(
        r"CREATE OR ALTER PROCEDURE\s+(?:dbo\.)?(\w+)(.*?)(?=\nGO\b)", sql, _re.S
    ):
        name, body = m.group(1), m.group(2)
        executable = "\n".join(l.split("--")[0] for l in body.splitlines())
        if "vw_Review" not in executable:
            continue
        # `SELECT *` / `SELECT TOP 1 *` carry every column automatically
        if _re.search(r"SELECT\s+(TOP\s*\(?\s*\d*\s*\)?\s+)?\*", executable):
            continue
        if "[ReviewKind]" not in executable:
            offenders.append(name)
    assert offenders == [], (
        f"these readers project explicitly and drop [ReviewKind]: {offenders} — "
        "their callers will silently fall back to deriving from live flags"
    )


def test_the_bill_mirror_reuses_the_stamped_kind_rather_than_re_reading():
    """Two reads of dbo.ReviewStatus in one sproc are two RCSI snapshots: a role
    transfer landing between them could stamp the Review `submitted` while
    setting the Bill to `in_review` (Codex P1). One read, one answer."""
    from tests.sproc_text import REPO_ROOT as SPROC_ROOT, sproc_body

    body = sproc_body(SPROC_ROOT / "entities/review/sql/dbo.review.sql", "CreateReview")
    executable = "\n".join(l.split("--")[0] for l in body.splitlines())
    mirror = executable[executable.index("UPDATE b"):]
    assert "SET b.[Status] = @ReviewKind" in mirror
    for flag in ("IsDeclined", "IsFinal", "IsInitial"):
        assert flag not in mirror, (
            f"the mirror re-reads {flag} — it must reuse the value already stamped"
        )
    assert executable.count("FROM dbo.[ReviewStatus]") == 1, (
        "exactly one read of the status row in the whole sproc"
    )


def test_all_three_copies_of_the_precedence_agree():
    """The CASE appears THREE times — the initial backfill, the re-sweep before
    NOT NULL, and the stamp in `CreateReview` — and a mutation that changed only
    the backfills survived a test that read only the stamp.

    Duplication is the hazard: SQL has no way to share the expression across an
    ALTER-time backfill and a runtime sproc, so the copies must be asserted
    IDENTICAL rather than each asserted correct. A backfill that maps
    differently from the writer would corrupt exactly the history this unit
    exists to preserve, and only a prod rehearsal would notice.
    """
    import re as _re

    sql = _executable(REVIEW_SQL)
    cases = _re.findall(
        r"CASE\s+WHEN rs\.\[IsDeclined\].*?ELSE N'in_review'\s+END", sql, _re.S
    )
    assert len(cases) == 3, (
        f"expected three copies (backfill, re-sweep, stamp); found {len(cases)} — "
        "if a copy was added or removed, this test needs to know why"
    )

    mappings = [
        _re.findall(r"WHEN\s+rs\.\[(\w+)\]\s*=\s*1\s+THEN\s+N'(\w+)'", c)
        for c in cases
    ]
    expected = [("IsDeclined", "declined"), ("IsFinal", "approved"), ("IsInitial", "submitted")]
    for i, m in enumerate(mappings):
        assert m == expected, f"copy {i} maps flags as {m}, not {expected}"


def test_the_pending_filter_still_uses_the_LIVE_flags_deliberately():
    """Pinned as a DECISION, not an oversight (Codex P1, booked).

    "Is this review still pending" is a question about the workflow as
    configured NOW: if an admin makes a status non-final, reviews resting there
    arguably SHOULD requeue, and freezing it would mean a reconfigured workflow
    never reaches its own documents. So U-455's "history must not be current
    config" rule applies to a row's KIND and not (yet) to its pending-ness.

    Silently switching these to the frozen kind would change live behaviour on
    a question nobody has answered — so it fails here until somebody does.
    """
    sql = _executable(INBOX_SQL)
    assert sql.count("[StatusIsFinal]    = 0") + sql.count("[StatusIsFinal] = 0") >= 2
    assert "[ReviewKind] NOT IN" not in sql, (
        "freezing the pending filter is a product decision that has not been "
        "made — see the note above the Pending CTE"
    )


def test_the_backfill_locks_the_status_table_before_reading_its_flags():
    """Codex P1 — the race that leaves no trace.

    The backfill derives HISTORY from the status's CURRENT flags. If a role
    transfer (`UpdateReviewStatusById`) commits between the column-add and the
    backfill's snapshot, every pre-existing row is stamped from the NEW flags —
    permanently, and with no NULL left for the re-sweep or the RAISERROR to
    catch. The migration reports success having written exactly the corruption
    it exists to prevent.

    `TABLOCKX, HOLDLOCK` makes a concurrent transfer either commit BEFORE the
    migration starts (already-lost history, which no migration can fix) or wait
    until it finishes.
    """
    sql = _executable(REVIEW_SQL)
    assert sql.count("FROM dbo.[ReviewStatus] WITH (TABLOCKX, HOLDLOCK)") == 3, (
        "step 0's fence, plus a re-assertion in each backfill block"
    )

    # and it must be taken BEFORE the flags are read, in each block
    import re as _re
    for block in _re.findall(r"BEGIN\n(.*?)\nEND\nGO", sql, _re.S):
        if "SET r.[ReviewKind] =" not in block:
            continue
        lock = block.index("TABLOCKX, HOLDLOCK")
        read = block.index("rs.[IsDeclined]")
        assert lock < read, (
            "the lock is taken after the flags are read — the window is still open"
        )


def test_the_fence_is_taken_BEFORE_the_column_is_added():
    """Codex, third pass. Locking at the first backfill was not early enough.

    The window opens the moment the column is added: a role transfer committing
    between the ADD and the backfill's snapshot stamps every pre-existing row
    from the NEW flags, permanently, with no NULL left behind for the re-sweep
    or the RAISERROR to catch. The fence has to precede the ADD.
    """
    sql = _executable(REVIEW_SQL)
    fence = sql.index("FROM dbo.[ReviewStatus] WITH (TABLOCKX, HOLDLOCK)")
    add = sql.index("ADD [ReviewKind] NVARCHAR(20) NULL")
    assert fence < add, (
        "the ReviewStatus fence must be acquired before the column-add, not at "
        "the first backfill — otherwise the window between them stays open"
    )


def test_the_transaction_dependency_is_asserted_not_assumed():
    """SUPERSEDED — this test previously claimed the file was
    "runner-independent" because both backfills take the lock. That was WRONG,
    and Codex caught it: `HOLDLOCK` lasts only for the enclosing transaction, so
    under an autocommitting runner the locking SELECT and the UPDATE after it
    are separate transactions and the fence is released in between. Taking it
    twice does not help.

    What makes it safe is that `scripts/run_sql.py` uses one connection with
    autocommit off — @@TRANCOUNT is 1 for the whole file. So the file now
    ASSERTS that and refuses to proceed otherwise, rather than backfilling
    unfenced and reporting success.
    """
    sql = _executable(REVIEW_SQL)
    assert "IF @@TRANCOUNT = 0" in sql
    guard = sql.index("IF @@TRANCOUNT = 0")
    fence = sql.index("FROM dbo.[ReviewStatus] WITH (TABLOCKX, HOLDLOCK)")
    add = sql.index("ADD [ReviewKind] NVARCHAR(20) NULL")
    assert guard < fence < add, "assert, then fence, then add the column"
    assert "must be applied inside a transaction" in sql, (
        "and the error must say what to do about it"
    )
