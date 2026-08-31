"""U-345 guard: entity base SQL files that reference CreatedByUserId are self-contained.

Every ``entities/*/sql/dbo.*.sql`` file (discovered via glob — not a hand-maintained list)
that references ``CreatedByUserId`` must either declare the column in a ``CREATE TABLE`` block
or carry an idempotent ``ALTER TABLE ... ADD`` guard paired with a ``sys.columns`` check.
Guard blocks must be GO-isolated (batch-trap class) — checked by reusing this repo's own
GO-batch-splitting primitive (tests/test_sync_sql_batch_boundaries.py) rather than a
hand-rolled line-adjacency heuristic.
"""

import re
from pathlib import Path

import pytest

from tests.test_sync_sql_batch_boundaries import _PROCEDURE_PATTERN, _split_sql_batches
from tests.test_update_read_column_parity import _strip_sql_comments

REPO_ROOT = Path(__file__).resolve().parents[1]

_CREATE_TABLE_START = re.compile(
    r"CREATE\s+TABLE\s+(?:\[dbo\]\.)?(?:\[(\w+)\]|(\w+))",
    re.IGNORECASE,
)
_CREATED_BY_COL_DEF = re.compile(r"\[CreatedByUserId\]\s+BIGINT", re.IGNORECASE)
_ADD_CREATED_BY = re.compile(r"ADD\s+\[CreatedByUserId\]", re.IGNORECASE)
_ADD_MODIFIED_BY = re.compile(r"ADD\s+\[ModifiedByUserId\]", re.IGNORECASE)


def _entity_base_sql_files() -> list[Path]:
    # Glob entity base files only — ``sql/migrations/`` lives outside this pattern.
    return sorted(REPO_ROOT.glob("entities/*/sql/dbo.*.sql"))


def _extract_create_table_blocks(stripped: str) -> list[str]:
    """Return the parenthesized column-list bodies of every CREATE TABLE.

    Expects comment-stripped text.
    """
    blocks: list[str] = []
    for match in _CREATE_TABLE_START.finditer(stripped):
        open_paren = stripped.find("(", match.end() - 1)
        if open_paren == -1:
            continue
        depth = 0
        for idx in range(open_paren, len(stripped)):
            ch = stripped[idx]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    blocks.append(stripped[open_paren + 1 : idx])
                    break
    return blocks


def _has_alter_add_guard(stripped: str) -> bool:
    """Expects comment-stripped text."""
    return (
        "sys.columns" in stripped
        and "CreatedByUserId" in stripped
        and _ADD_CREATED_BY.search(stripped) is not None
    )


def test_every_referencing_base_file_is_self_contained_for_created_by_user_id():
    offenders: list[str] = []
    for path in _entity_base_sql_files():
        stripped = _strip_sql_comments(path.read_text(encoding="utf-8"))
        if "CreatedByUserId" not in stripped:
            continue
        blocks = _extract_create_table_blocks(stripped)
        has_column_def = any(_CREATED_BY_COL_DEF.search(block) for block in blocks)
        if has_column_def or _has_alter_add_guard(stripped):
            continue
        if not blocks:
            # Sproc-only file (no CREATE TABLE of its own) — e.g.
            # entities/bill/sql/dbo.bill_create_source_email.sql, whose CreateBill
            # sproc inserts into dbo.Bill, but dbo.Bill's CREATE TABLE (and its
            # guard) live in dbo.bill.sql. This test only checks a file against
            # itself, not across files, so a sproc-only file can never be
            # "compliant" by this test's own definition — it is exempted here
            # rather than flagged, on the assumption its target table's guard is
            # checked where that table is actually defined. This is a real,
            # narrow gap: a NEW sproc-only file whose target table's guard is
            # missing/wrong elsewhere would not be caught by this test.
            continue
        offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "Base SQL files reference CreatedByUserId but lack a CREATE TABLE column "
        f"definition or idempotent ALTER-ADD guard: {offenders}"
    )


def test_bill_created_by_user_id_guard_lives_with_the_bill_table():
    """Explicit regression pin for the cross-file case the generic scan can't see.

    dbo.bill.sql owns CREATE TABLE Bill but no sproc in that file references
    CreatedByUserId — the only consuming sproc (CreateBill) lives in
    dbo.bill_create_source_email.sql. That means once dbo.bill.sql's guard is in
    place, removing it makes the file stop "referencing" CreatedByUserId at all
    (its only reference IS the guard), so the generic
    test_every_referencing_base_file_is_self_contained_for_created_by_user_id scan
    above cannot detect that specific regression — it would just look unreferenced,
    not non-compliant. This test checks dbo.bill.sql directly, independent of that
    reference-detection dance, so removing the guard is still caught.
    """
    stripped = _strip_sql_comments(
        (REPO_ROOT / "entities/bill/sql/dbo.bill.sql").read_text(encoding="utf-8")
    )
    assert _has_alter_add_guard(stripped), (
        "entities/bill/sql/dbo.bill.sql must carry the CreatedByUserId ALTER-ADD guard "
        "(Bill's CREATE TABLE lives here; the consuming CreateBill sproc lives in "
        "dbo.bill_create_source_email.sql, so this file won't otherwise 'reference' "
        "the column and the generic self-contained scan can't catch its removal)"
    )


def _guard_isolation_violations(text: str, add_pattern: re.Pattern[str]) -> list[str]:
    """Batches (per tests.test_sync_sql_batch_boundaries._split_sql_batches) that
    contain an ADD [CreatedBy*/ModifiedBy*] guard AND also a CREATE (OR ALTER)
    PROCEDURE — proof the guard was NOT GO-isolated on both sides (either
    swallowed into a preceding sproc's batch, or itself swallowing a following
    one). Returns a short snippet per offending batch for the assertion message.
    """
    violations: list[str] = []
    for batch in _split_sql_batches(text):
        if add_pattern.search(batch) and _PROCEDURE_PATTERN.search(batch):
            first_line = next(
                (line.strip() for line in batch.splitlines() if line.strip()), "<empty>"
            )
            violations.append(first_line[:80])
    return violations


@pytest.mark.parametrize(
    "add_pattern",
    [_ADD_CREATED_BY, _ADD_MODIFIED_BY],
    ids=["CreatedByUserId", "ModifiedByUserId"],
)
def test_alter_add_guard_blocks_are_go_terminated(add_pattern: re.Pattern[str]):
    violations: list[str] = []
    for path in _entity_base_sql_files():
        text = path.read_text(encoding="utf-8")
        if not add_pattern.search(_strip_sql_comments(text)):
            continue
        for snippet in _guard_isolation_violations(text, add_pattern):
            violations.append(f"{path.relative_to(REPO_ROOT)}: {snippet!r}")
    assert not violations, (
        "ALTER-ADD guard blocks must be GO-isolated from any CREATE PROCEDURE "
        "sharing the same batch (the T-SQL batch-trap class): " + "; ".join(violations)
    )


def test_go_termination_check_rejects_a_guard_swallowed_into_a_preceding_batch():
    """A guard with no GO before it lands in the same batch as the preceding
    sproc — _guard_isolation_violations must catch that."""
    sql = """
GO
CREATE OR ALTER PROCEDURE SomeEarlierProc
AS
BEGIN
    SELECT 1;
END;

IF OBJECT_ID('dbo.Example', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.Example') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[Example] ADD [CreatedByUserId] BIGINT NOT NULL;
END

CREATE OR ALTER PROCEDURE SomeLaterProc
AS
BEGIN
    SELECT 2;
END;
GO
"""
    violations = _guard_isolation_violations(sql, _ADD_CREATED_BY)
    assert violations, "expected the swallowed guard to be flagged"
