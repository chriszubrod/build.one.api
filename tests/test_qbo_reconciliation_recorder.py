"""Pure-logic tests for qbo.ReconciliationIssue recorder field-width guards."""
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import REPO_ROOT, iter_prod_python_sources
from integrations.intuit.qbo.base.reconciliation_recorder import (
    _ACTION_DEFAULT,
    _FIELD_LIMITS,
    record_mapping_issue,
)

_SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from sync_helper import _QBO_SYNC_ENTITY_META  # noqa: E402

_RECONCILIATION_ISSUE_SQL = (
    REPO_ROOT
    / "integrations"
    / "intuit"
    / "qbo"
    / "reconciliation"
    / "sql"
    / "qbo.reconciliation_issue.sql"
)
_SQL_COLUMN_WIDTH_RE = re.compile(
    r"\[(DriftType|EntityType|Severity|Action)\]\s+NVARCHAR\((\d+)\)",
    re.IGNORECASE,
)
# Production call sites as of U-218a — guard must discover at least this many writers.
# record_mapping_issue (5) + _record_reconciliation_issue (4) + direct repo.create (4).
_MIN_CALL_SITES = 13
_KNOWN_DRIFT_TYPES = frozenset(
    {
        "duplicate_qbo_item",
        "duplicate_qbo_customer",
        "duplicate_qbo_vendor",
        "orphaned_item_scc_mapping",
        "orphaned_item_cost_code_mapping",
        "orphan_billcredit_header",
        "orphaned_vc_billcredit_mapping",
        "orphaned_cust_project_mapping",
        "orphaned_purch_expense_mapping",
        "orphaned_bill_bill_mapping",
        "orphaned_vendor_vendor_mapping",
        "pull_delete_reconcile",
    }
)
_SKIP_FILES = frozenset({"reconciliation_recorder.py"})
_DEFAULT_KWARGS = {
    "severity": "critical",
    "action": _ACTION_DEFAULT,
}
# WatermarkRun._record_bound_forced_advance (scripts/sync_helper.py, U-228) passes
# entity_type=<a local var derived from self.entity via _QBO_SYNC_ENTITY_META, falling back to
# self.entity unchanged> — not a literal, since WatermarkRun is shared across all pull scripts.
# Derived from the real registry (not hand-typed) so the two can't drift; the width check uses
# the longest RAW key as the worst case, which also covers every mapped display label (all
# shorter than "reimburse_charge", 16 chars) since the fallback path is unbounded.
_QBO_SYNC_ENTITY_NAMES = frozenset(_QBO_SYNC_ENTITY_META)


@dataclass(frozen=True)
class _DiscoveredWriteCall:
    drift_type: str
    entity_type: str
    severity: str
    action: str
    check_entity_type: bool = True


def _parse_sql_column_widths() -> Dict[str, int]:
    text = _RECONCILIATION_ISSUE_SQL.read_text(encoding="utf-8")
    matches = _SQL_COLUMN_WIDTH_RE.findall(text)
    assert matches, (
        f"No NVARCHAR column widths matched in {_RECONCILIATION_ISSUE_SQL}; "
        f"regex may be stale"
    )
    key_map = {
        "drifttype": "drift_type",
        "entitytype": "entity_type",
        "severity": "severity",
        "action": "action",
    }
    widths: Dict[str, int] = {}
    for column_name, width in matches:
        key = key_map[column_name.lower()]
        widths[key] = int(width)
    assert set(widths) == set(_FIELD_LIMITS), (
        f"SQL width keys {set(widths)} != recorder keys {set(_FIELD_LIMITS)}"
    )
    return widths


def test_recorder_field_limits_match_sql_column_widths():
    """Recorder clamps must track qbo.ReconciliationIssue NVARCHAR sizes."""
    sql_widths = _parse_sql_column_widths()
    assert sql_widths == _FIELD_LIMITS


def _string_literal(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _extract_call_kwargs(call: ast.Call) -> Dict[str, Optional[str]]:
    """Extract string-literal keyword args; None means absent (use default where applicable)."""
    kwargs: Dict[str, Optional[str]] = {
        "drift_type": None,
        "entity_type": None,
        "severity": None,
        "action": None,
    }
    for kw in call.keywords:
        if kw.arg in kwargs:
            kwargs[kw.arg] = _string_literal(kw.value)
    return kwargs


def _receiver_suggests_reconciliation_issue_repo(receiver: ast.AST) -> bool:
    if isinstance(receiver, ast.Attribute):
        if receiver.attr == "reconciliation_repo":
            return True
    if isinstance(receiver, ast.Name):
        lowered = receiver.id.lower()
        if "reconciliation" in lowered and "repo" in lowered:
            return True
    if isinstance(receiver, ast.Call):
        callee = receiver.func
        if isinstance(callee, ast.Name) and callee.id == "ReconciliationIssueRepository":
            return True
        if isinstance(callee, ast.Attribute) and callee.attr == "ReconciliationIssueRepository":
            return True
    return False


def _classify_reconciliation_issue_write_call(node: ast.AST) -> Optional[str]:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id == "record_mapping_issue":
        return "record_mapping_issue"
    if isinstance(func, ast.Attribute) and func.attr == "record_mapping_issue":
        return "record_mapping_issue"
    if isinstance(func, ast.Attribute) and func.attr == "_record_reconciliation_issue":
        return "_record_reconciliation_issue"
    if isinstance(func, ast.Attribute) and func.attr == "create":
        if _receiver_suggests_reconciliation_issue_repo(func.value):
            return "reconciliation_repo_create"
    return None


def _walk_reconciliation_issue_write_calls(tree: ast.AST) -> List[Tuple[ast.Call, str]]:
    calls: List[Tuple[ast.Call, str]] = []
    for node in ast.walk(tree):
        kind = _classify_reconciliation_issue_write_call(node)
        if kind is not None:
            calls.append((node, kind))
    return calls


def _resolve_entity_type_for_call(rel_path: str, kind: str, raw: Dict[str, Optional[str]]) -> Optional[str]:
    if raw["entity_type"] is not None:
        return raw["entity_type"]
    if kind == "_record_reconciliation_issue":
        if "customer/connector/project" in rel_path.replace("\\", "/"):
            return "Project"
        if "vendor/connector/vendor" in rel_path.replace("\\", "/"):
            return "Vendor"
    if kind == "record_mapping_issue" and rel_path.replace("\\", "/") == "scripts/sync_helper.py":
        return max(_QBO_SYNC_ENTITY_NAMES, key=len)
    return None


def discover_reconciliation_issue_write_literals(
    root: Optional[Path] = None,
) -> List[Tuple[str, int, _DiscoveredWriteCall]]:
    """
    AST-scan production Python for qbo.ReconciliationIssue writer call sites.

    Matches record_mapping_issue(...), _record_reconciliation_issue(...), and direct
    ReconciliationIssueRepository.create(...) (receiver name suggests a reconciliation
    issue repo).

    Returns (relative_path, lineno, resolved call metadata) for each call where drift_type
    is a string literal (required for the width guard).
    """
    repo_root = root or REPO_ROOT
    discoveries: List[Tuple[str, int, _DiscoveredWriteCall]] = []

    for path in iter_prod_python_sources(repo_root, skip_files=_SKIP_FILES):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            raise SyntaxError(f"Failed to parse {path}: {exc}") from exc

        rel = str(path.relative_to(repo_root))
        for call, kind in _walk_reconciliation_issue_write_calls(tree):
            raw = _extract_call_kwargs(call)
            if raw["drift_type"] is None:
                if kind == "reconciliation_repo_create":
                    # Scaffold methods pass drift_type as a parameter — covered by
                    # _record_reconciliation_issue call sites instead.
                    continue
                raise AssertionError(
                    f"{rel}:{call.lineno}: {kind} drift_type must be a string literal "
                    f"so the width guard can verify it"
                )
            entity_type = _resolve_entity_type_for_call(rel, kind, raw)
            if entity_type is None:
                if kind != "reconciliation_repo_create":
                    raise AssertionError(
                        f"{rel}:{call.lineno}: {kind} entity_type must be a string literal "
                        f"so the width guard can verify it"
                    )
            discoveries.append(
                (
                    rel,
                    call.lineno,
                    _DiscoveredWriteCall(
                        drift_type=raw["drift_type"],
                        entity_type=entity_type or "",
                        severity=raw["severity"] if raw["severity"] is not None else _DEFAULT_KWARGS["severity"],
                        action=raw["action"] if raw["action"] is not None else _DEFAULT_KWARGS["action"],
                        check_entity_type=not (kind == "reconciliation_repo_create" and entity_type is None),
                    ),
                )
            )

    return discoveries


def test_overlong_drift_type_is_truncated_and_does_not_raise():
    repo = Mock()
    overlong = "orphaned_item_sub_cost_code_mapping"  # 35 chars; column is NVARCHAR(32)

    record_mapping_issue(
        repo,
        drift_type=overlong,
        entity_type="SubCostCode",
        entity_public_id=None,
        qbo_id="QBO-1",
        realm_id="realm-1",
        details="details",
    )

    repo.create.assert_called_once()
    assert repo.create.call_args.kwargs["drift_type"] == overlong[:32]


def test_none_severity_is_coerced_and_does_not_escape_record_mapping_issue():
    repo = Mock()

    record_mapping_issue(
        repo,
        drift_type="orphaned_item_scc_mapping",
        entity_type="SubCostCode",
        entity_public_id=None,
        qbo_id="QBO-1",
        realm_id="realm-1",
        details="details",
        severity=None,
    )

    repo.create.assert_called_once()
    assert repo.create.call_args.kwargs["severity"] == ""


def test_reconciliation_issue_write_literals_fit_prod_column_widths():
    """Every production ReconciliationIssue writer must use column-safe literal keys."""
    discoveries = discover_reconciliation_issue_write_literals()

    assert len(discoveries) >= _MIN_CALL_SITES, (
        f"Expected at least {_MIN_CALL_SITES} ReconciliationIssue writer call sites, "
        f"found {len(discoveries)} — discovery matcher may be broken"
    )

    found_drift_types: Set[str] = set()
    violations: List[str] = []

    for rel_path, lineno, call in discoveries:
        found_drift_types.add(call.drift_type)
        for field, max_len in _FIELD_LIMITS.items():
            if field == "entity_type" and not call.check_entity_type:
                continue
            value = getattr(call, field)
            if len(value) > max_len:
                violations.append(
                    f"{rel_path}:{lineno}: {field}={value!r} is {len(value)} chars (max {max_len})"
                )

    assert _KNOWN_DRIFT_TYPES <= found_drift_types, (
        f"Missing expected drift_type literals: {_KNOWN_DRIFT_TYPES - found_drift_types}"
    )

    if violations:
        pytest.fail("ReconciliationIssue writer literals exceed prod column widths:\n" + "\n".join(violations))
