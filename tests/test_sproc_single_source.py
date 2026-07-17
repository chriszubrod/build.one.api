"""U-045/U-048/U-051 guard: canonical SQL homes for sprocs and access UDFs.

Two guards, different shapes:

* **Sprocs** — an entity's base SQL file is the one home for its sprocs. Covers
  time_entry (U-045) and role_module (U-048) — 2 of 37 entities. The other 35
  still carry duplicated base sprocs in migrations (bill=10, contract_labor=10,
  user_role=9, …); converting them is future work. **When you convert one, add
  its row to ENTITY_BASE_FILES** — coverage is opt-in, so a conversion without a
  row leaves a gap that looks covered.

* **Access UDFs** (U-051) — the whole `dbo.UserCanAccess*` family lives in
  `shared/sql/dbo.access_udfs.sql`, NOT in any entity base file. Each one is
  WITH SCHEMABINDING across more than one entity package, so an entity-local home
  would create a from-scratch build cycle. Guarded here by name, plus a catch-all
  so a sixth access UDF cannot silently escape the guarded set.

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
BILL_LINE_ITEM_BASE = REPO_ROOT / "entities" / "bill_line_item" / "sql" / "dbo.bill_line_item.sql"
EXPENSE_LINE_ITEM_BASE = REPO_ROOT / "entities" / "expense_line_item" / "sql" / "dbo.expense_line_item.sql"
ACCESS_UDF_HOME = REPO_ROOT / "shared" / "sql" / "dbo.access_udfs.sql"

# U-074: CreateBillLineItem + CreateExpenseLineItem reconciled to their entity base
# (@Quantity DECIMAL(18,4) + @CreatedByUserId threading - the union of the base
# DECIMAL, step2 DECIMAL+threading, and gap2 INT+threading copies). The two
# migration copies were neutralized to pointer stubs so each base is the sole home.
SINGLE_SOURCE_SPROCS = [
    ("CreateBillLineItem", BILL_LINE_ITEM_BASE),
    ("CreateExpenseLineItem", EXPENSE_LINE_ITEM_BASE),
]

# U-061: the four Create sprocs whose bodies had drifted BEHIND their canonical
# entity base files were neutralized to pointer-stub comments in
# gap2_core_threading.sql (re-running the migration reverted prod — CreateProject
# dropped @Notes, CreateExpense dropped @SourceEmailMessageId, CreateInvoiceLineItem
# dropped @EmployeeLaborLineItemId, CreateBill undid the DueDate=@BillDate mirror).
# Full single-source conversion of these entities is the 36-entity campaign; this
# narrow guard just keeps this one file from re-acquiring a drifted body.
GAP2_CORE_THREADING = REPO_ROOT / "scripts" / "migrations" / "gap2_core_threading.sql"
GAP2_NEUTRALIZED_SPROCS = frozenset(
    {"CreateProject", "CreateBill", "CreateExpense", "CreateInvoiceLineItem"}
)

ENTITY_BASE_FILES = [
    ("time_entry", TIME_ENTRY_BASE),
    ("role_module", ROLE_MODULE_BASE),
]

ACCESS_UDFS = [
    "UserCanAccessTimeEntry",
    "UserCanAccessProject",
    "UserCanAccessBill",
    "UserCanAccessBillCredit",
    "UserCanAccessExpense",
]

# Matches an optional schema ([dbo]. / dbo. / [qbo]. / qbo.) then the object
# name, bracketed or bare. Only the name is captured.
_SCHEMA = r"(?:\[\w+\]|\w+)\s*\.\s*"
_OBJECT_NAME = r"(?:\[(\w+)\]|(\w+))"

_SPROC_PATTERN = re.compile(
    rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:{_SCHEMA})?{_OBJECT_NAME}",
    re.IGNORECASE,
)
_UDF_PATTERN = re.compile(
    rf"CREATE\s+OR\s+ALTER\s+FUNCTION\s+(?:{_SCHEMA})?{_OBJECT_NAME}",
    re.IGNORECASE,
)


def _names_from_text(pattern: re.Pattern, text: str) -> frozenset[str]:
    """Object names `pattern` declares in `text`, unwrapping the bracketed/bare capture pair."""
    return frozenset(bracketed or bare for bracketed, bare in pattern.findall(text))


@lru_cache(maxsize=1)
def _sql_index() -> tuple[tuple[Path, frozenset[str], frozenset[str]], ...]:
    """Read every repo .sql file once: (path, sproc names, UDF names it declares).

    Cached because each parametrized entity — and each access-UDF test — would
    otherwise re-walk and re-read all ~372 .sql files (~2.2 MB) from scratch.
    """
    index = []
    for path in REPO_ROOT.rglob("*.sql"):
        if ".venv" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        index.append(
            (path, _names_from_text(_SPROC_PATTERN, text), _names_from_text(_UDF_PATTERN, text))
        )
    return tuple(index)


@pytest.mark.parametrize("sproc_name,home_path", SINGLE_SOURCE_SPROCS)
def test_sproc_defined_once_in_entity_base(sproc_name, home_path):
    """U-074: each reconciled line-item Create sproc is defined exactly once, in
    its entity base file. A body re-added to ANY .sql file fails here."""
    matches = [path for path, sprocs, _ in _sql_index() if sproc_name in sprocs]
    assert matches == [home_path], (
        f"{sproc_name} must be defined exactly once in "
        f"{home_path.relative_to(REPO_ROOT)}; found in: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in matches)
        + f". Change {home_path.relative_to(REPO_ROOT)} instead."
    )


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


@pytest.mark.parametrize("udf_name", ACCESS_UDFS)
def test_access_udf_defined_once_in_shared_home(udf_name):
    matches = [path for path, _, udf_names in _sql_index() if udf_name in udf_names]
    assert matches == [ACCESS_UDF_HOME], (
        f"dbo.{udf_name} must be defined exactly once in "
        f"{ACCESS_UDF_HOME.relative_to(REPO_ROOT)}; found in: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in matches)
        + f". Change {ACCESS_UDF_HOME.relative_to(REPO_ROOT)} instead."
    )


def test_unlisted_user_can_access_udfs_fail():
    """Catch-all: ACCESS_UDFS is opt-in, so a sixth access UDF added to an entity
    file would otherwise escape the guard entirely instead of failing loudly."""
    expected = frozenset(ACCESS_UDFS)
    offenders = [
        f"{name} in {path.relative_to(REPO_ROOT)}"
        for path, _, udf_names in _sql_index()
        for name in sorted(udf_names)
        if name.startswith("UserCanAccess") and name not in expected
    ]
    assert not offenders, (
        "UserCanAccess* UDF(s) found outside the guarded set — add to ACCESS_UDFS "
        "and home in shared/sql/dbo.access_udfs.sql, or remove the duplicate:\n"
        + "\n".join(offenders)
    )


def test_gap2_neutralized_sprocs_stay_stubbed():
    """U-061 recurrence guard: the four drift-neutralized Create sprocs must stay
    pointer-stub comments in gap2_core_threading.sql — never re-acquire a
    CREATE OR ALTER body there. A re-added body is what reverted prod."""
    names = next(
        (sprocs for path, sprocs, _ in _sql_index() if path == GAP2_CORE_THREADING),
        None,
    )
    assert names is not None, (
        f"expected {GAP2_CORE_THREADING.relative_to(REPO_ROOT)} in the SQL index"
    )
    offenders = sorted(names & GAP2_NEUTRALIZED_SPROCS)
    assert not offenders, (
        "gap2_core_threading.sql re-acquired a CREATE OR ALTER body for "
        + ", ".join(offenders)
        + " — these were neutralized to base-canonical pointer stubs in U-061 "
        "because their bodies had drifted behind their entity base files. "
        "Change the entity base file instead; do not re-add a body here."
    )
