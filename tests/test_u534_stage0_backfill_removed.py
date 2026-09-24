"""U-534: no script can write `qbo.PhysicalAddress` any more.

ph3a froze the staging WRITE in the service and ph3b deleted the package, but
`scripts/backfill_qbo_identity_reference.py::backfill_address_stage0` still held
a raw `UPDATE qbo.[PhysicalAddress] SET [RealmId] = ?`. That statement was
invisible to the pre-drop guard in `scripts/migrations/u513_drop_qbo_physical_address.sql`,
which only counts rows whose Created/Modified stamps moved — a RealmId-only
UPDATE that leaves those columns alone would not trip it, so the guard could
report "quiet" while a backfill was actively writing.

The branch that reached it (`if spec.key == "address":` in `backfill_entity`) was
already unreachable: U-351 removed the "address" row from REFERENCE_ENTITY_SPECS
and U-353 excludes the only survivor, so `ENTITY_SPECS` is empty. These tests pin
the deletion so the writer cannot return before the table is dropped.

The scan strips comments AND docstrings before matching, because several sync
scripts still *mention* `qbo.PhysicalAddress` in prose explaining why they no
longer touch it — prose must not be able to fail (or satisfy) this test.
"""
from __future__ import annotations

import ast
import io
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DELETED = (
    "backfill_address_stage0",
    "_assert_physical_address_realm_id_column",
    "parse_physical_address_parent_qbo_id",
    "resolve_parent_realm_id",
)


def _code_only(path: Path) -> str:
    """Source with comments and docstrings removed, via a round-trip through AST."""
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _py_scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS.rglob("*.py") if p.name != "__init__.py")


def test_the_scan_is_not_vacuous():
    """Guard: the scanner must actually see a corpus, and must see real code in it."""
    scripts = _py_scripts()
    assert len(scripts) > 20, f"expected a substantial scripts/ corpus, saw {len(scripts)}"

    backfill = SCRIPTS / "backfill_qbo_identity_reference.py"
    assert backfill.exists(), "the file under test is gone; this suite would pass vacuously"
    code = _code_only(backfill)
    # a control the scanner MUST find, proving it reads executable code
    assert "check_all_fanout_overlaps" in code, "scanner failed to see live code"
    # and prose it must NOT find, proving docstrings really are stripped
    assert "ahead of that table being dropped" not in code, "docstrings were not stripped"


@pytest.mark.parametrize("name", DELETED)
def test_the_deleted_symbols_are_gone_from_the_backfill(name):
    code = _code_only(SCRIPTS / "backfill_qbo_identity_reference.py")
    assert name not in code, f"{name} came back into the backfill script"


def test_no_script_writes_the_staging_table():
    offenders = []
    for path in _py_scripts():
        code = _code_only(path)
        if "PhysicalAddress" in code:
            offenders.append(str(path.relative_to(SCRIPTS.parent)))
    assert offenders == [], (
        "these scripts reference qbo.PhysicalAddress in EXECUTABLE code; the "
        f"pre-drop guard cannot see their writes: {offenders}"
    )


def test_the_address_branch_is_unreachable_anyway():
    """Belt and braces: even if the branch returned, no spec key could select it."""
    from integrations.intuit.qbo.base.identity_drift import REFERENCE_ENTITY_SPECS

    assert "address" not in {s.key for s in REFERENCE_ENTITY_SPECS}
