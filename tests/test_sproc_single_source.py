"""U-045/U-048 guard: a converted entity's sprocs/UDF must have a single canonical SQL home.

Covers time_entry (U-045) and role_module (U-048) — 2 of 37 entities. The other
35 still carry duplicated base sprocs in migrations (bill=10, contract_labor=10,
user_role=9, …); converting them is future work. **When you convert one, add its
row to ENTITY_BASE_FILES** — coverage is opt-in, so a conversion without a row
leaves a gap that looks covered.

The patterns must keep matching every T-SQL declaration style (bare, dbo., and
the bracketed [dbo].[Name] form the repo uses in ~22 places). A duplicate written
in a style the regex misses passes silently, which is the one failure mode this
guard cannot afford.
"""

import re
from functools import lru_cache
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

TIME_ENTRY_BASE = REPO_ROOT / "entities" / "time_entry" / "sql" / "dbo.time_entry.sql"
ROLE_MODULE_BASE = REPO_ROOT / "entities" / "role_module" / "sql" / "dbo.rolemodule.sql"

ENTITY_BASE_FILES = [
    ("time_entry", TIME_ENTRY_BASE),
    ("role_module", ROLE_MODULE_BASE),
]

# Matches an optional schema ([dbo]. / dbo. / [qbo]. / qbo.) then the object
# name, bracketed or bare. Only the name is captured.
_SCHEMA = r"(?:\[\w+\]|\w+)\s*\.\s*"
_SPROC_NAME = r"(?:\[(\w+)\]|(\w+))"

_SPROC_PATTERN = re.compile(
    rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:{_SCHEMA})?{_SPROC_NAME}",
    re.IGNORECASE,
)
_UDF_PATTERN = re.compile(
    rf"CREATE\s+OR\s+ALTER\s+FUNCTION\s+(?:{_SCHEMA})?"
    r"(?:\[UserCanAccessTimeEntry\]|UserCanAccessTimeEntry\b)",
    re.IGNORECASE,
)


def _sproc_names_from_text(text: str) -> list[str]:
    return [bracketed or bare for bracketed, bare in _SPROC_PATTERN.findall(text)]


@lru_cache(maxsize=1)
def _sql_index() -> tuple[tuple[Path, frozenset[str], bool], ...]:
    """Read every repo .sql file once: (path, sproc names it declares, declares the UDF).

    Cached because each parametrized entity — and the UDF test — would otherwise
    re-walk and re-read all ~372 .sql files (~2.2 MB) from scratch.
    """
    index = []
    for path in REPO_ROOT.rglob("*.sql"):
        if ".venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        index.append((path, frozenset(_sproc_names_from_text(text)), bool(_UDF_PATTERN.search(text))))
    return tuple(index)


@pytest.mark.parametrize("entity_name,base_path", ENTITY_BASE_FILES)
def test_base_sprocs_are_not_redefined_elsewhere(entity_name, base_path):
    index = _sql_index()

    canonical = next((names for path, names, _ in index if path == base_path), frozenset())
    assert canonical, f"expected sproc names in the {entity_name} base file"

    for path, names, _ in index:
        if path == base_path:
            continue
        for name in sorted(names & canonical):
            raise AssertionError(
                f"{name} is redefined in {path.relative_to(REPO_ROOT)}; "
                f"change {base_path.relative_to(REPO_ROOT)} instead"
            )


def test_user_can_access_time_entry_udf_defined_once():
    matches = [path for path, _, has_udf in _sql_index() if has_udf]
    assert matches == [TIME_ENTRY_BASE], (
        "dbo.UserCanAccessTimeEntry must be defined exactly once in "
        f"{TIME_ENTRY_BASE.relative_to(REPO_ROOT)}; found in: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in matches)
    )
