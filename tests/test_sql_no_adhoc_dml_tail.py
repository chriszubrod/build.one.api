"""U-216 guard: routine-defining SQL files must not contain bare column-zero DML.

scripts/run_sql.py splits on GO and executes every batch on every apply, so any
bare UPDATE/INSERT/DELETE/MERGE at column zero in a routine-defining file re-fires
on each re-apply. Legitimate DML indented inside CREATE/ALTER PROCEDURE|FUNCTION|
TRIGGER|VIEW bodies is allowed. One-shot scripts with bare DML but no routine
definitions are out of scope.

Path exclusions (_SKIP_SEGMENTS: dev, migrations, .venv) are independent of the
routine-definition rule: migrations legitimately pair a sproc redefinition with a
one-shot backfill and apply once, whereas base entity SQL files are re-applied
routinely — so migrations are excluded by path alone, not by this guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.sql_corpus import DML_KEYWORDS, iter_repo_sql_files

REPO_ROOT = Path(__file__).resolve().parents[1]

ROUTINE_DEF_RE = re.compile(
    r"CREATE\s+(?:OR\s+ALTER\s+)?(?:PROCEDURE|PROC|FUNCTION|TRIGGER|VIEW)\b",
    re.IGNORECASE,
)
_SKIP_SEGMENTS = frozenset({"dev", "migrations", ".venv"})

# Permanent regression fixture: U-216 ad-hoc tail removed from qbo.bill.sql (pre-fix).
_U216_REGRESSION_SQL = """
CREATE OR ALTER PROCEDURE DeleteQboBillLineById
(
    @Id BIGINT
)
AS
BEGIN
    DELETE FROM [qbo].[BillLine] WHERE [Id] = @Id;
    COMMIT TRANSACTION;
END;
GO





SELECT BillableStatus, COUNT(*) as cnt 
FROM qbo.BillLine 
GROUP BY BillableStatus
ORDER BY cnt DESC;

-- Update IsBillable in dbo.BillLineItem based on qbo.BillLine.BillableStatus
-- "Billable" or "HasBeenBilled" = 1 (True), "NotBillable" = 0 (False)

UPDATE bli
SET bli.[IsBillable] = CASE 
    WHEN bl.[BillableStatus] IN ('Billable', 'HasBeenBilled') THEN 1
    WHEN bl.[BillableStatus] = 'NotBillable' THEN 0
    ELSE bli.[IsBillable]  -- Keep existing if NULL
END
FROM dbo.[BillLineItem] bli
INNER JOIN qbo.[BillLineItemBillLine] map ON map.[BillLineItemId] = bli.[Id]
INNER JOIN qbo.[BillLine] bl ON bl.[Id] = map.[QboBillLineId]
WHERE bl.[BillableStatus] IS NOT NULL;
"""

_NEGATIVE_ROUTINE_WITH_INTERNAL_DML = """
CREATE OR ALTER PROCEDURE UpsertExample
(
    @Id BIGINT
)
AS
BEGIN
    UPDATE dbo.Example SET Name = 'x' WHERE Id = @Id;
END;
GO

CREATE OR ALTER PROCEDURE FinalBatchNoTrailingGo
(
    @Id BIGINT
)
AS
BEGIN
    DELETE FROM dbo.Example WHERE Id = @Id;
END;
"""

_NEGATIVE_ONESHOT_DML_SCRIPT = """
-- one-shot backfill, no routine definitions
UPDATE dbo.Example SET IsActive = 1 WHERE IsActive IS NULL;

INSERT INTO dbo.Example (Name) VALUES ('seed');

DELETE FROM dbo.Example WHERE Name = 'temp';
"""


def _file_defines_routine(content: str) -> bool:
    return bool(ROUTINE_DEF_RE.search(content))


def _is_bare_top_level_dml_line(line: str) -> bool:
    # Column-zero only: DML indented inside procedure/control-flow bodies is allowed;
    # idempotent IF NOT EXISTS ... BEGIN ... INSERT ... END guards (e.g. dbo.time_entry.sql
    # module seed) are also indented and must not be flagged. Both U-216 ad-hoc tails were
    # written at column zero.
    if not line or line[0].isspace():
        return False
    comment_at = line.find("--")
    effective = line[:comment_at] if comment_at >= 0 else line
    effective = effective.strip()
    if not effective:
        return False
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", effective)
    if not match:
        return False
    return match.group(1).upper() in DML_KEYWORDS


def find_adhoc_dml_violations(content: str, *, rel_path: str) -> list[str]:
    if not _file_defines_routine(content):
        return []
    violations: list[str] = []
    for line_no, line in enumerate(content.splitlines(), start=1):
        if not _is_bare_top_level_dml_line(line):
            continue
        violations.append(f"{rel_path} (line {line_no}): {line.strip()}")
    return violations


def _iter_guarded_sql_files() -> list[Path]:
    guarded: list[Path] = []
    for path in iter_repo_sql_files(REPO_ROOT, skip_dir_names=_SKIP_SEGMENTS):
        if _file_defines_routine(path.read_text(encoding="utf-8")):
            guarded.append(path)
    return guarded


_GUARDED_SQL_FILES = _iter_guarded_sql_files()


def test_guarded_sql_files_non_empty():
    assert _GUARDED_SQL_FILES, "routine-defining SQL scan found zero files — guard is vacuous"
    rel_paths = {p.relative_to(REPO_ROOT).as_posix() for p in _GUARDED_SQL_FILES}
    assert "integrations/intuit/qbo/bill/sql/qbo.bill.sql" in rel_paths


def test_regression_fixture_flags_diagnostic_select_then_update():
    violations = find_adhoc_dml_violations(_U216_REGRESSION_SQL, rel_path="fixture/u216_regression.sql")
    assert violations, "expected bare UPDATE after diagnostic SELECT to be flagged"
    assert any("UPDATE bli" in v for v in violations)


_U048_BATCH_TRAP_SQL = """
CREATE OR ALTER PROCEDURE UpdateThing
(
    @Id BIGINT
)
AS
BEGIN
    UPDATE dbo.Thing SET Name = 'x' WHERE Id = @Id;
END;
UPDATE bli SET bli.[IsBillable] = 1
FROM dbo.[BillLineItem] bli
INNER JOIN qbo.[BillLineItemBillLine] map ON map.[BillLineItemId] = bli.[Id];
"""


def test_flags_adhoc_dml_swallowed_into_procedure_batch_when_go_missing():
    """U-048 batch trap: without GO, ad-hoc column-zero DML shares the procedure batch."""
    violations = find_adhoc_dml_violations(
        _U048_BATCH_TRAP_SQL,
        rel_path="fixture/u048_batch_trap.sql",
    )
    assert violations, "expected column-zero UPDATE after procedure END (no GO) to be flagged"
    assert any("UPDATE bli" in v for v in violations)


def test_negative_dml_inside_procedure_not_flagged():
    violations = find_adhoc_dml_violations(
        _NEGATIVE_ROUTINE_WITH_INTERNAL_DML,
        rel_path="fixture/routine_internal_dml.sql",
    )
    assert not violations


def test_negative_oneshot_script_not_flagged():
    violations = find_adhoc_dml_violations(
        _NEGATIVE_ONESHOT_DML_SCRIPT,
        rel_path="fixture/oneshot_backfill.sql",
    )
    assert not violations


@pytest.mark.parametrize(
    "sql_path",
    _GUARDED_SQL_FILES,
    ids=lambda p: p.relative_to(REPO_ROOT).as_posix(),
)
def test_sql_file_has_no_adhoc_dml_tail(sql_path: Path):
    content = sql_path.read_text(encoding="utf-8")
    rel = sql_path.relative_to(REPO_ROOT).as_posix()
    violations = find_adhoc_dml_violations(content, rel_path=rel)
    assert not violations, "Bare top-level DML in routine-defining file:\n" + "\n".join(violations)
