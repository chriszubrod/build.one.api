"""U-045/U-048/U-051/U-062/U-087 guard: canonical SQL homes for sprocs, access
UDFs, and the shared human-only review predicate.

Three guard shapes:

* **Sprocs** — an entity's base SQL file is the one home for its sprocs. Covers
  time_entry (U-045), role_module (U-048), and the three review recipient
  resolvers (U-062) — 2 of 37 entities fully converted plus a narrow review
  resolver set. The other 35 still carry duplicated base sprocs in migrations
  (bill=10, contract_labor=10, user_role=9, …); converting them is future
  work. **When you convert one, add its row to ENTITY_BASE_FILES or
  SINGLE_SOURCE_SPROCS** — coverage is opt-in, so a conversion without a row
  leaves a gap that looks covered.

* **Access UDFs** (U-051) — the whole `dbo.UserCanAccess*` family lives in
  `shared/sql/dbo.access_udfs.sql`, NOT in any entity base file. Each one is
  WITH SCHEMABINDING across more than one entity package, so an entity-local home
  would create a from-scratch build cycle. Guarded here by name, plus a catch-all
  so a sixth access UDF cannot silently escape the guarded set.

* **Domain predicate UDFs** (U-087) — a non-schemabound UDF with a single-file
  consumer set (today `dbo.IsHumanReviewUser`, the human-only review filter used
  only by the three resolvers) is homed in its entity base file, NOT shared/sql.
  This is the deliberate counterpoint to the access-UDF family: no SCHEMABINDING,
  no cross-entity build cycle, so entity-local is correct. Both its single-source
  home (SINGLE_SOURCE_UDFS) and the filter it carries are guarded below.

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

COMPLETION_JOB_BASE = REPO_ROOT / "entities" / "completion_job" / "sql" / "dbo.completion_job.sql"
REVIEW_BASE = REPO_ROOT / "entities" / "review" / "sql" / "dbo.review.sql"

# U-074: CreateBillLineItem + CreateExpenseLineItem reconciled to their entity base
# (@Quantity DECIMAL(18,4) + @CreatedByUserId threading - the union of the base
# DECIMAL, step2 DECIMAL+threading, and gap2 INT+threading copies). The two
# migration copies were neutralized to pointer stubs so each base is the sole home.
# U-062/U-087: the three review-notification recipient resolvers homed in the
# review base file (dbo.review.sql), their bodies neutralized to pointer stubs in
# migrations 001/004/006/007/008. The human-only filter they shared was folded
# (U-087) into dbo.IsHumanReviewUser (homed in the same file, tabled in
# SINGLE_SOURCE_UDFS); it is additionally guarded by
# test_review_resolvers_keep_persona_agent_filter +
# test_review_resolver_enumeration_is_complete below.
SINGLE_SOURCE_SPROCS = [
    ("CreateBillLineItem", BILL_LINE_ITEM_BASE),
    ("CreateExpenseLineItem", EXPENSE_LINE_ITEM_BASE),
    ("ResolveReviewRecipientsByBillId", REVIEW_BASE),
    ("ResolveReviewRecipientsByContractLaborId", REVIEW_BASE),
    ("ResolveContractLaborReviewRecipientsPerProject", REVIEW_BASE),
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
    ("completion_job", COMPLETION_JOB_BASE),
]

ACCESS_UDFS = [
    "UserCanAccessTimeEntry",
    "UserCanAccessProject",
    "UserCanAccessBill",
    "UserCanAccessBillCredit",
    "UserCanAccessExpense",
]

# (udf_name, canonical_home) for every single-sourced UDF. The access-UDF family
# stays in its shared home; U-087's dbo.IsHumanReviewUser is non-schemabound with
# a single-file consumer set, so it is homed in the review entity base file (no
# build cycle). test_udf_defined_once_in_canonical_home enforces both.
SINGLE_SOURCE_UDFS = [(name, ACCESS_UDF_HOME) for name in ACCESS_UDFS] + [
    ("IsHumanReviewUser", REVIEW_BASE),
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

_REVIEW_RESOLVER_NAMES = (
    "ResolveReviewRecipientsByBillId",
    "ResolveReviewRecipientsByContractLaborId",
    "ResolveContractLaborReviewRecipientsPerProject",
)

# Completeness catch-all for the enumeration above: any sproc in the review base
# whose name matches the resolver convention must be listed in
# _REVIEW_RESOLVER_NAMES, so a new resolver can't escape the human-only filter
# guard (mirrors test_unlisted_user_can_access_udfs_fail for the access-UDF set).
_REVIEW_RESOLVER_NAME_PATTERN = re.compile(r"Resolve.*Review.*Recipient", re.IGNORECASE)

# Anchors a CREATE OR ALTER <kind> body to the GO batch boundary — sturdier than
# stopping at the first `END;`, which a future nested BEGIN…END; or CASE…END;
# could truncate. Reuses _OBJECT_NAME so the bracketed [dbo].[Name] house style is
# handled (per the module docstring).
def _body_pattern(keyword: str) -> re.Pattern:
    return re.compile(
        rf"CREATE\s+OR\s+ALTER\s+{keyword}\s+(?:{_SCHEMA})?{_OBJECT_NAME}.*?(?=\nGO)",
        re.IGNORECASE | re.DOTALL,
    )


_SPROC_BODY_PATTERN = _body_pattern("PROCEDURE")
_FUNCTION_BODY_PATTERN = _body_pattern("FUNCTION")

# U-087: the human-only filter now lives once in dbo.IsHumanReviewUser (a scalar
# UDF correlating on its @UserId param), and each resolver references it. These
# patterns mirror the removed inline ones but bind @UserId, and a call pattern
# confirms each resolver applies the shared predicate on up.[UserId].
_UDF_AGENT_FILTER_PATTERN = re.compile(
    r"NOT\s+EXISTS\s*\(\s*"
    r"SELECT\s+1\s+FROM\s+dbo\.\[User\]\s+u\s+"
    r"WHERE\s+u\.\[Id\]\s*=\s*@UserId\s+"
    r"AND\s+u\.\[IsAgent\]\s*=\s*1",
    re.IGNORECASE | re.DOTALL,
)

_UDF_PERSONA_FILTER_PATTERN = re.compile(
    r"NOT\s+EXISTS\s*\(\s*"
    r"SELECT\s+1\s+FROM\s+dbo\.\[Auth\]\s+a\s+"
    r"WHERE\s+a\.\[UserId\]\s*=\s*@UserId\s+"
    r"AND\s+LEFT\s*\(\s*LTRIM\s*\(\s*a\.\[Username\]\s*\)\s*,\s*8\s*\)\s*=\s*N?'persona_'",
    re.IGNORECASE | re.DOTALL,
)

_UDF_CALL_PATTERN = re.compile(
    r"dbo\.IsHumanReviewUser\s*\(\s*up\.\[UserId\]\s*\)\s*=\s*1",
    re.IGNORECASE,
)

_LINE_COMMENT = re.compile(r"--[^\n]*")


def _names_from_text(pattern: re.Pattern, text: str) -> frozenset[str]:
    """Object names `pattern` declares in `text`, unwrapping the bracketed/bare capture pair."""
    return frozenset(bracketed or bare for bracketed, bare in pattern.findall(text))


def _extract_object_body(text: str, pattern: re.Pattern, name_wanted: str) -> str:
    """Return the CREATE OR ALTER … block for one named object (sproc or UDF)."""
    for match in pattern.finditer(text):
        name = match.group(1) or match.group(2)  # bracketed | bare
        if name and name.casefold() == name_wanted.casefold():
            return match.group(0)
    raise AssertionError(
        f"expected dbo.{name_wanted} in {REVIEW_BASE.relative_to(REPO_ROOT)}"
    )


@lru_cache(maxsize=1)
def _review_base_text() -> str:
    return REVIEW_BASE.read_text(encoding="utf-8")


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


@pytest.mark.parametrize("udf_name,home_path", SINGLE_SOURCE_UDFS)
def test_udf_defined_once_in_canonical_home(udf_name, home_path):
    """Each single-sourced UDF is defined exactly once, in its canonical home —
    the shared access-UDF file for the UserCanAccess* family, the review entity
    base file for the U-087 human-only predicate."""
    matches = [path for path, _, udf_names in _sql_index() if udf_name in udf_names]
    assert matches == [home_path], (
        f"dbo.{udf_name} must be defined exactly once in "
        f"{home_path.relative_to(REPO_ROOT)}; found in: "
        + ", ".join(str(p.relative_to(REPO_ROOT)) for p in matches)
        + f". Change {home_path.relative_to(REPO_ROOT)} instead."
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


def test_review_resolvers_keep_persona_agent_filter():
    """U-062/U-087: the human-only filter (IsAgent + persona_ prefix) must
    survive as a single shared predicate. Post-U-087 the two NOT EXISTS blocks
    live once in dbo.IsHumanReviewUser, and every resolver references it — so a
    future resolver cannot silently drop the exclusion."""
    text = _review_base_text()

    # 1. The filter itself lives in the shared UDF body (correlated on @UserId).
    #    Strip line comments so a commented-out block can't satisfy the guard.
    udf_body = _LINE_COMMENT.sub(
        "", _extract_object_body(text, _FUNCTION_BODY_PATTERN, "IsHumanReviewUser")
    )
    assert _UDF_AGENT_FILTER_PATTERN.search(udf_body), (
        "dbo.IsHumanReviewUser is missing the NOT EXISTS dbo.[User] IsAgent = 1 "
        "exclusion — the human-only filter must live in the shared predicate."
    )
    assert _UDF_PERSONA_FILTER_PATTERN.search(udf_body), (
        "dbo.IsHumanReviewUser is missing the LEFT(LTRIM(a.[Username]), 8) = "
        "N'persona_' exclusion — the human-only filter must live in the shared "
        "predicate."
    )

    # 2. Every resolver references the shared predicate on up.[UserId].
    for sproc_name in _REVIEW_RESOLVER_NAMES:
        body = _LINE_COMMENT.sub(
            "", _extract_object_body(text, _SPROC_BODY_PATTERN, sproc_name)
        )
        assert _UDF_CALL_PATTERN.search(body), (
            f"dbo.{sproc_name} no longer references "
            "dbo.IsHumanReviewUser(up.[UserId]) = 1 — every review resolver must "
            "apply the U-087 shared human-only predicate."
        )


def test_review_resolver_enumeration_is_complete():
    """U-087: _REVIEW_RESOLVER_NAMES must list exactly the review recipient
    resolvers in the base file, so the filter-reference guard above cannot be
    silently bypassed by adding (or renaming) a resolver."""
    declared = {
        name
        for name in _names_from_text(_SPROC_PATTERN, _review_base_text())
        if _REVIEW_RESOLVER_NAME_PATTERN.search(name)
    }
    drift = declared ^ set(_REVIEW_RESOLVER_NAMES)
    assert not drift, (
        "review recipient resolver set drifted from _REVIEW_RESOLVER_NAMES: "
        + ", ".join(sorted(drift))
        + " — update _REVIEW_RESOLVER_NAMES so "
        "test_review_resolvers_keep_persona_agent_filter checks every resolver "
        "applies dbo.IsHumanReviewUser."
    )
