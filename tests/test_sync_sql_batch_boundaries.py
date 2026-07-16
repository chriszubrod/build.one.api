"""U-054 guard: sync SQL batches terminate procedures before trailing DDL.

T-SQL runs a CREATE PROCEDURE body to the end of its batch, not to its
matching END. A statement placed after a procedure's closing END with no GO
before it is silently compiled into that procedure's body. This mirrors
scripts/run_sql.py batch splitting and tracks BEGIN/END nesting from the
procedure body AS through depth returning to zero to locate where the body
actually closes — then fails on any trailing non-comment statement in the
batch (including swallowed IF NOT EXISTS / ALTER TABLE blocks). Lines that
are only ``--`` comments are skipped; inline ``--`` comments are stripped
before classifying BEGIN/END openers and closers.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SQL = REPO_ROOT / "integrations" / "sync" / "sql" / "dbo.sync.sql"

_PROCEDURE_PATTERN = re.compile(
    r"CREATE\s+(?:OR\s+ALTER\s+)?PROCEDURE\s+(?:\[\w+\]|\w+\s*\.\s*)?(?:\[(\w+)\]|(\w+))",
    re.IGNORECASE,
)


def _split_sql_batches(sql_content: str) -> list[str]:
    """Mirror scripts/run_sql.py: split on lines where strip().upper() == 'GO'."""
    batches: list[str] = []
    current_batch: list[str] = []
    for line in sql_content.split("\n"):
        if line.strip().upper() == "GO":
            if current_batch:
                batches.append("\n".join(current_batch))
                current_batch = []
        else:
            current_batch.append(line)
    if current_batch:
        batches.append("\n".join(current_batch))
    return batches


def _procedure_name(batch: str) -> str | None:
    match = _PROCEDURE_PATTERN.search(batch)
    if match is None:
        return None
    return match.group(1) or match.group(2)


def _find_procedure_as_line_index(lines: list[str]) -> int | None:
    """Return the line index of the procedure body-opening AS."""
    seen_procedure = False
    for index, line in enumerate(lines):
        if not seen_procedure:
            if _PROCEDURE_PATTERN.search(line):
                seen_procedure = True
            continue

        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue

        upper = stripped.upper()
        if upper == "AS" or upper == "AS;" or upper.startswith("AS "):
            return index
    return None


def _strip_inline_sql_comment(line: str) -> str:
    comment_start = line.find("--")
    if comment_start >= 0:
        line = line[:comment_start]
    return line.strip()


def _normalize_block_token(stripped: str) -> str:
    return _strip_inline_sql_comment(stripped).upper().rstrip(";").strip()


def _is_block_opener(stripped: str) -> bool:
    normalized = _normalize_block_token(stripped)
    if normalized.startswith("BEGIN TRANSACTION") or normalized.startswith("BEGIN TRAN"):
        return False
    if normalized == "BEGIN":
        return True
    if normalized.startswith("BEGIN TRY"):
        return True
    if normalized.startswith("BEGIN CATCH"):
        return True
    return False


def _is_block_closer(stripped: str) -> bool:
    normalized = _normalize_block_token(stripped)
    if normalized == "END":
        return True
    if normalized.startswith("END TRY"):
        return True
    if normalized.startswith("END CATCH"):
        return True
    return False


def _block_depth_change(stripped: str, *, is_as_line: bool) -> int:
    if is_as_line:
        as_line = _normalize_block_token(stripped)
        if as_line == "AS":
            return 0
        if as_line.startswith("AS "):
            stripped = as_line[3:].strip()
        else:
            return 0

    if _is_block_opener(stripped):
        return 1
    if _is_block_closer(stripped):
        return -1
    return 0


def _procedure_body_close_line_index(batch: str) -> int | None:
    """Return the line index where procedure body nesting depth returns to 0."""
    lines = batch.splitlines()
    as_index = _find_procedure_as_line_index(lines)
    if as_index is None:
        return None

    depth = 0
    saw_begin = False
    close_index: int | None = None

    for index in range(as_index, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("--"):
            continue

        depth += _block_depth_change(stripped, is_as_line=(index == as_index))
        if depth > 0:
            saw_begin = True
        if saw_begin and depth == 0:
            close_index = index
            break

    if not saw_begin:
        return None
    return close_index


def _first_offending_trailing_statement(batch: str, after_line_index: int) -> str | None:
    """First non-comment line after the procedure body close line."""
    for line in batch.splitlines()[after_line_index + 1 :]:
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        return stripped
    return None


def test_no_statement_swallowed_into_procedure_body():
    sql_content = SYNC_SQL.read_text(encoding="utf-8")
    violations: list[tuple[str, str]] = []

    for batch in _split_sql_batches(sql_content):
        if not batch.strip():
            continue

        proc_name = _procedure_name(batch)
        if proc_name is None:
            continue

        close_index = _procedure_body_close_line_index(batch)
        if close_index is None:
            violations.append(
                (proc_name, "cannot determine procedure body boundary"),
            )
            continue

        trailing = _first_offending_trailing_statement(batch, close_index)
        if trailing is not None:
            violations.append((proc_name, trailing))

    assert violations == [], (
        "Each CREATE PROCEDURE batch in "
        f"{SYNC_SQL.relative_to(REPO_ROOT)} must close its body before any "
        "trailing statement (mirror scripts/run_sql.py GO boundaries). Offenders:\n"
        + "\n".join(
            f"  {name}: first trailing statement after procedure END — {stmt!r}"
            for name, stmt in violations
        )
    )
