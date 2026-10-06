"""U-631 guard: entity contracts live in code, not sidecar spec files.

Each covered entity must carry a module docstring on business/service.py (Owns,
Invariants, External writes, Sprocs — the first three non-empty) and a matching
-- Contract: header on every sql/dbo*.sql file under its package. The Sprocs
section must exactly match every name= passed to call_procedure in
persistence/*.py, and every listed sproc must be defined in that entity's
sql/*.sql corpus.

To onboard an entity: add its directory name to COVERED_ENTITIES, remove it from
UNDOCUMENTED_ENTITIES, author the docstring + SQL headers, and run this module.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

import pytest

from tests.sproc_text import REPO_ROOT
from tests.sql_corpus import iter_repo_sql_files

COVERED_ENTITIES = [
    "bill",
    "expense",
    "invoice",
    "contract_labor",
    "time_entry",
    "user",
    "user_role",
    "role_module",
]

# Shrink-only ledger: entities with business/service.py not yet in COVERED_ENTITIES.
UNDOCUMENTED_ENTITIES: frozenset[str] = frozenset(
    {
        "address",
        "address_type",
        "admin_audit_log",
        "asset",
        "asset_attachment",
        "attachment",
        "auth",
        "bill_credit",
        "bill_credit_line_item",
        "bill_credit_line_item_attachment",
        "bill_line_item",
        "bill_line_item_attachment",
        "budget",
        "budget_line_item",
        "budget_revision",
        "business_license",
        "business_license_attachment",
        "certificate_of_insurance",
        "company",
        "completion_job",
        "contact",
        "contract",
        "contractors_license",
        "contractors_license_attachment",
        "cost_code",
        "customer",
        "device_token",
        "email_message",
        "employee",
        "employee_labor",
        "employee_labor_line_item",
        "employee_project_rate",
        "expense_coding_item",
        "expense_line_item",
        "expense_line_item_attachment",
        "integration",
        "invoice_attachment",
        "invoice_line_item",
        "invoice_line_item_attachment",
        "module",
        "organization",
        "organization_company",
        "payment_term",
        "project",
        "project_address",
        "ramp_transaction_follow_up",
        "review",
        "review_status",
        "role",
        "sub_cost_code",
        "taxpayer",
        "taxpayer_attachment",
        "user_company",
        "user_module",
        "user_organization",
        "user_project",
        "vendor",
        "vendor_address",
        "vendor_insurance_policy",
        "vendor_project_rate",
        "vendor_type",
        "vendor_type_required_coverage",
    }
)

_HEADINGS = ("Owns", "Invariants", "External writes", "Sprocs")
_NON_EMPTY_SECTIONS = _HEADINGS[:3]  # Sprocs is checked against persistence instead

# Only a call_procedure(...) call's name= kwarg; digits and multi-line calls allowed.
_CALL_PROCEDURE_NAME = re.compile(
    r"""call_procedure\s*\((?:[^()]*?,\s*)?name\s*=\s*["']([A-Za-z0-9_.]+)["']""",
    re.S,
)

# Bare, dbo., and [dbo].[Name] forms; the PROC abbreviation is accepted.
_SCHEMA = r"(?:\[\w+\]|\w+)\s*\.\s*"
_OBJECT_NAME = r"(?:\[(\w+)\]|(\w+))"
_SPROC_PATTERN = re.compile(
    rf"CREATE\s+(?:OR\s+ALTER\s+)?PROC(?:EDURE)?\s+(?:{_SCHEMA})?{_OBJECT_NAME}",
    re.IGNORECASE,
)


def _entity_root(entity: str) -> Path:
    return REPO_ROOT / "entities" / entity


@lru_cache(maxsize=None)
def _module_docstring(entity: str) -> str:
    path = _entity_root(entity) / "business" / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    doc = ast.get_docstring(tree)
    assert doc is not None, f"{entity}: business/service.py has no module docstring"
    return doc


def _heading_index(lines: list[str], heading: str, entity: str) -> int:
    try:
        return lines.index(heading)
    except ValueError:
        pytest.fail(f"{entity}: docstring missing heading {heading!r}")


def _section_lines(entity: str, heading: str) -> list[str]:
    """Body lines under `heading` (two-space indent stripped), up to the next heading."""
    lines = _module_docstring(entity).splitlines()
    body: list[str] = []
    for line in lines[_heading_index(lines, heading, entity) + 1 :]:
        if line in _HEADINGS:
            break
        if line.startswith("  "):
            body.append(line[2:])
        elif line.strip() != "":
            break
    return body


def _docstring_sproc_names(entity: str) -> set[str]:
    return {s for s in (n.strip() for n in _section_lines(entity, "Sprocs")) if s}


def _names_from_persistence_text(text: str) -> set[str]:
    return {m.group(1).removeprefix("dbo.") for m in _CALL_PROCEDURE_NAME.finditer(text)}


def _persistence_sproc_names(entity: str) -> set[str]:
    files = sorted((_entity_root(entity) / "persistence").rglob("*.py"))
    return set().union(*(_names_from_persistence_text(p.read_text(encoding="utf-8")) for p in files))


def _sprocs_from_sql_text(text: str) -> set[str]:
    return {m.group(1) or m.group(2) for m in _SPROC_PATTERN.finditer(text)}


@lru_cache(maxsize=None)
def _sql_file(path: Path) -> tuple[str, frozenset[str]]:
    """One read per .sql file: (text, sprocs it defines), shared by the per-entity and per-file checks."""
    text = path.read_text(encoding="utf-8")
    return text, frozenset(_sprocs_from_sql_text(text))


def _sprocs_defined_in_entity_sql(entity: str) -> set[str]:
    return set().union(*(_sql_file(p)[1] for p in iter_repo_sql_files(_entity_root(entity) / "sql")))


def _parse_sql_header_sprocs(line: str) -> set[str]:
    return {p.strip() for p in line.removeprefix("-- Sprocs:").split(",") if p.strip()}


def _entities_with_service_py() -> set[str]:
    return {p.parent.parent.name for p in (REPO_ROOT / "entities").glob("*/business/service.py")}


@pytest.mark.parametrize("entity", COVERED_ENTITIES)
def test_module_docstring_starts_with_entity_contract(entity: str) -> None:
    doc = _module_docstring(entity)
    assert doc.startswith("Entity contract — "), (
        f"{entity}: module docstring must start with 'Entity contract — '"
    )


@pytest.mark.parametrize("entity", COVERED_ENTITIES)
def test_module_docstring_sections_are_non_empty(entity: str) -> None:
    for heading in _NON_EMPTY_SECTIONS:
        non_blank = [ln for ln in _section_lines(entity, heading) if ln.strip()]
        assert non_blank, f"{entity}: section {heading!r} has no non-blank lines"


@pytest.mark.parametrize("entity", COVERED_ENTITIES)
def test_module_docstring_has_headings_in_order(entity: str) -> None:
    lines = _module_docstring(entity).splitlines()
    positions = [_heading_index(lines, heading, entity) for heading in _HEADINGS]
    assert positions == sorted(positions), (
        f"{entity}: headings must appear in order {_HEADINGS}, got positions {positions}"
    )


@pytest.mark.parametrize("entity", COVERED_ENTITIES)
def test_docstring_sprocs_match_persistence(entity: str) -> None:
    doc_names = _docstring_sproc_names(entity)
    persist_names = _persistence_sproc_names(entity)
    missing = sorted(persist_names - doc_names)
    extra = sorted(doc_names - persist_names)
    assert doc_names == persist_names, (
        f"{entity}: Sprocs section mismatch — missing in docstring: {missing!r}; "
        f"extra in docstring: {extra!r}"
    )


@pytest.mark.parametrize("entity", COVERED_ENTITIES)
def test_docstring_sprocs_defined_in_entity_sql(entity: str) -> None:
    missing = sorted(_docstring_sproc_names(entity) - _sprocs_defined_in_entity_sql(entity))
    assert not missing, (
        f"{entity}: docstring lists sprocs not defined in entities/{entity}/sql/*.sql: "
        f"{missing!r}"
    )


@pytest.mark.parametrize("entity", COVERED_ENTITIES)
def test_dbo_sql_files_have_contract_headers(entity: str) -> None:
    for path in sorted((_entity_root(entity) / "sql").glob("dbo*.sql")):
        rel = path.relative_to(REPO_ROOT)
        text, defined = _sql_file(path)
        lines = text.splitlines()
        assert len(lines) >= 3, f"{rel}: expected at least 3 lines for contract header"
        assert lines[0].startswith("-- Contract: dbo."), (
            f"{rel}: line 1 must start with '-- Contract: dbo.'"
        )
        assert lines[1].startswith("-- Owns:"), f"{rel}: line 2 must start with '-- Owns:'"
        assert lines[2].startswith("-- Invariants:"), (
            f"{rel}: line 3 must start with '-- Invariants:'"
        )
        sprocs_lines = [ln for ln in lines if ln.startswith("-- Sprocs:")]
        assert len(sprocs_lines) == 1, f"{rel}: expected exactly one '-- Sprocs:' line"
        header_sprocs = _parse_sql_header_sprocs(sprocs_lines[0])
        assert header_sprocs == set(defined), (
            f"{rel}: -- Sprocs: {sorted(header_sprocs)!r} != defined in file "
            f"{sorted(defined)!r}"
        )


def test_call_procedure_regex_accepts_digits_and_multiline() -> None:
    text = 'call_procedure(\n    cursor=cursor,\n    name="ReadBillsV2",\n    params={},\n)'
    assert _names_from_persistence_text(text) == {"ReadBillsV2"}


def test_call_procedure_regex_ignores_other_name_kwargs() -> None:
    text = 'logger.bind(name="not_a_sproc")\ncall_procedure(cursor=c, name="ReadX")'
    assert _names_from_persistence_text(text) == {"ReadX"}


def test_sproc_pattern_accepts_proc_abbreviation_and_bracketed() -> None:
    text = "CREATE PROC dbo.A AS\nCREATE OR ALTER PROCEDURE [dbo].[B] AS\nCREATE PROCEDURE C AS"
    assert _sprocs_from_sql_text(text) == {"A", "B", "C"}


def test_covered_and_undocumented_partition_entities_with_service() -> None:
    with_service = _entities_with_service_py()
    covered = set(COVERED_ENTITIES)
    assert covered <= with_service, (
        f"COVERED_ENTITIES not found: {sorted(covered - with_service)!r}"
    )
    # Implied by the equality below; kept for the clearer message on an overlap.
    assert not (covered & UNDOCUMENTED_ENTITIES), (
        "entity cannot be both covered and undocumented"
    )
    undocumented = with_service - covered
    assert undocumented == set(UNDOCUMENTED_ENTITIES), (
        f"UNDOCUMENTED_ENTITIES out of date — missing: "
        f"{sorted(undocumented - UNDOCUMENTED_ENTITIES)!r}; "
        f"stale entries: {sorted(UNDOCUMENTED_ENTITIES - undocumented)!r}"
    )
