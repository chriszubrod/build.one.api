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
from integrations.intuit.qbo.base.drift_types import (
    DRIFT_ORPHANED_ITEM_SCC_MAPPING,
    KNOWN_DRIFT_TYPES as _KNOWN_DRIFT_TYPES,
)
from integrations.intuit.qbo.base.reconciliation_recorder import (
    _ACTION_DEFAULT,
    _FIELD_LIMITS,
    record_mapping_issue,
)
from integrations.intuit.qbo.base.watermark import _QBO_SYNC_ENTITY_META

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
# Production call sites as of U-353 — guard must discover at least this many writers.
# Was 31 as of U-334. record_mapping_issue (12) + record_identity_mapping_conflict (9)
# + record_duplicate_identity_conflict (6) + direct repo.create (1) = 28 total.
# U-351 (physical_address) was net neutral: retired one legacy
# record_identity_mapping_conflict call (the mapping-table-era
# _record_identity_mapping_conflict_issue) and added one new record_duplicate_
# identity_conflict call (_record_duplicate_qbo_address_issue, reachable since
# Address's create path DOES adopt by street/city) -- 11->10, 6->7.
# U-352 (payment_term) removed one record_duplicate_identity_conflict call outright
# -- TermPaymentTermConnector._record_duplicate_qbo_payment_term_issue was deleted as
# dead code, since PaymentTerm's create path never adopts by name and so has no
# side-channel collision that call could ever have fired for -- 7->6.
# U-353 (this unit) retired qbo.VendorCreditBillCredit, removing
# VendorCreditBillCreditConnector's _record_identity_mapping_conflict_issue
# (record_identity_mapping_conflict, 10->9) and _record_missing_bill_credit_issue
# (record_mapping_issue, 13->12) -- both call sites protected a mapping-table-vs-dbo
# drift scenario that no longer exists once dbo.BillCredit.QboId is the sole store.
_MIN_CALL_SITES = 27
_SKIP_FILES = frozenset({"reconciliation_recorder.py"})
_DEFAULT_KWARGS = {
    "severity": "critical",
    "action": _ACTION_DEFAULT,
}
# WatermarkRun._record_bound_forced_advance (integrations/intuit/qbo/base/watermark.py, U-228) passes
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


_WRITER_FN_NAMES = frozenset({
    "record_mapping_issue",
    "record_identity_mapping_conflict",
    "record_duplicate_identity_conflict",
})


def _classify_reconciliation_issue_write_call(node: ast.AST) -> Optional[str]:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name) and func.id in _WRITER_FN_NAMES:
        return func.id
    if isinstance(func, ast.Attribute) and func.attr in _WRITER_FN_NAMES:
        return func.attr
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
    if kind == "record_mapping_issue" and rel_path.replace("\\", "/") == "integrations/intuit/qbo/base/watermark.py":
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

    assert found_drift_types <= _KNOWN_DRIFT_TYPES, (
        f"Undeclared drift_type literals in production writers: "
        f"{found_drift_types - _KNOWN_DRIFT_TYPES}"
    )

    if violations:
        pytest.fail("ReconciliationIssue writer literals exceed prod column widths:\n" + "\n".join(violations))


def test_record_mapping_issue_known_drift_type_does_not_log_unregistered(caplog):
    repo = Mock()
    with caplog.at_level("ERROR"):
        record_mapping_issue(
            repo,
            drift_type=DRIFT_ORPHANED_ITEM_SCC_MAPPING,
            entity_type="SubCostCode",
            entity_public_id=None,
            qbo_id="QBO-1",
            realm_id="realm-1",
            details="details",
        )
    assert not any("Unregistered ReconciliationIssue DriftType" in m for m in caplog.messages)
    repo.create.assert_called_once()


def test_record_mapping_issue_unregistered_drift_type_logs_but_still_writes(caplog):
    repo = Mock()
    with caplog.at_level("ERROR"):
        record_mapping_issue(
            repo,
            drift_type="not_a_real_drift_type",
            entity_type="SubCostCode",
            entity_public_id=None,
            qbo_id="QBO-1",
            realm_id="realm-1",
            details="details",
        )
    assert any(
        "Unregistered ReconciliationIssue DriftType 'not_a_real_drift_type'" in m
        for m in caplog.messages
    )
    repo.create.assert_called_once()
    assert repo.create.call_args.kwargs["drift_type"] == "not_a_real_drift_type"
