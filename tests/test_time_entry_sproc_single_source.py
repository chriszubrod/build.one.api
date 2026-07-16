"""U-045 guard: time_entry sprocs/UDF must have a single canonical SQL home.

Scope is deliberately limited to the time_entry entity. Thirty-six other
entities still carry duplicated base sprocs in migrations (bill=10,
contract_labor=10, user_role=9, …); converting them is future work.
"""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TIME_ENTRY_BASE = REPO_ROOT / "entities" / "time_entry" / "sql" / "dbo.time_entry.sql"

_SPROC_PATTERN = re.compile(
    r"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?(\w+)",
    re.IGNORECASE,
)
_UDF_MARKER = "CREATE OR ALTER FUNCTION dbo.UserCanAccessTimeEntry"


def _sproc_names_from_base() -> set[str]:
    text = TIME_ENTRY_BASE.read_text(encoding="utf-8")
    return set(_SPROC_PATTERN.findall(text))


def _sql_files_excluding_base() -> list[Path]:
    return [
        path
        for path in REPO_ROOT.rglob("*.sql")
        if ".venv" not in path.parts and path != TIME_ENTRY_BASE
    ]


def test_time_entry_base_sprocs_are_not_redefined_elsewhere():
    canonical = _sproc_names_from_base()
    assert canonical, "expected sproc names in the time_entry base file"

    for sql_path in _sql_files_excluding_base():
        text = sql_path.read_text(encoding="utf-8")
        for name in _SPROC_PATTERN.findall(text):
            assert name not in canonical, (
                f"{name} is redefined in {sql_path.relative_to(REPO_ROOT)}; "
                "change entities/time_entry/sql/dbo.time_entry.sql instead"
            )


def test_user_can_access_time_entry_udf_defined_once():
    matches = [
        path
        for path in REPO_ROOT.rglob("*.sql")
        if ".venv" not in path.parts and _UDF_MARKER in path.read_text(encoding="utf-8")
    ]
    assert matches == [TIME_ENTRY_BASE], (
        "dbo.UserCanAccessTimeEntry must be defined exactly once in "
        f"{TIME_ENTRY_BASE.relative_to(REPO_ROOT)}; found in: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in matches)
    )
