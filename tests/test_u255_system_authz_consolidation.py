"""U-255: system_authz consolidation, scheduler reconcile wraps, QboActive read paths."""

from __future__ import annotations

import ast
import asyncio
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import REPO_ROOT, iter_prod_python_sources
from entities.payment_term.persistence.repo import PaymentTermRepository
from entities.sub_cost_code.persistence.repo import SubCostCodeRepository
from entities.vendor.persistence.repo import VendorRepository
from shared.authz.context import (
    current_can_view_team_modules,
    current_company_id,
    current_is_system_admin,
    current_user_id,
    set_authz_context,
    system_authz,
)
from shared.scheduler import _register_ms_reconcile_jobs, _register_qbo_reconcile_jobs


def _vendor_row(**overrides):
    defaults = {
        "Id": 1,
        "PublicId": "00000000-0000-0000-0000-000000000001",
        "RowVersion": b"\x00" * 8,
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-01 00:00:00",
        "Name": "Acme",
        "Abbreviation": "ACM",
        "TaxpayerId": None,
        "VendorTypeId": 1,
        "IsDraft": False,
        "IsDeleted": False,
        "IsContractLabor": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _sub_cost_code_row(**overrides):
    defaults = {
        "Id": "1",
        "PublicId": "00000000-0000-0000-0000-000000000002",
        "RowVersion": b"\x00" * 8,
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-01 00:00:00",
        "Number": "01.01",
        "Name": "Concrete",
        "Description": None,
        "CostCodeId": 1,
        "Aliases": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _payment_term_row(**overrides):
    defaults = {
        "Id": 1,
        "PublicId": "00000000-0000-0000-0000-000000000003",
        "RowVersion": b"\x00" * 8,
        "CreatedDatetime": "2026-01-01 00:00:00",
        "ModifiedDatetime": "2026-01-01 00:00:00",
        "Name": "Net 30",
        "Description": None,
        "DiscountPercent": None,
        "DiscountDays": None,
        "DueDays": 30,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_system_authz_saves_and_restores_all_four_context_vars():
    prior_team = frozenset({"BILLS", "EXPENSES"})
    set_authz_context(user_id=42, company_id=7, is_system_admin=False)
    current_can_view_team_modules.set(prior_team)

    with system_authz():
        assert current_user_id.get() is None
        assert current_company_id.get() is None
        assert current_is_system_admin.get() is True
        assert current_can_view_team_modules.get() == frozenset()

    assert current_user_id.get() == 42
    assert current_company_id.get() == 7
    assert current_is_system_admin.get() is False
    assert current_can_view_team_modules.get() == prior_team


def _is_contextvar_get(node: ast.AST, var_name: str) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == var_name
    )


def _is_hand_rolled_set_authz(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if not (isinstance(node.func, ast.Name) and node.func.id == "set_authz_context"):
        return False
    kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}

    def _is_none(value: ast.AST | None) -> bool:
        return isinstance(value, ast.Constant) and value.value is None

    def _is_true(value: ast.AST | None) -> bool:
        return isinstance(value, ast.Constant) and value.value is True

    return (
        _is_none(kwargs.get("user_id"))
        and _is_none(kwargs.get("company_id"))
        and _is_true(kwargs.get("is_system_admin"))
    )


def _extract_target_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Tuple):
        return [elt.id for elt in target.elts if isinstance(elt, ast.Name)]
    return []


def _names_bound_to_contextvar_get(assign: ast.Assign, var_name: str) -> list[str]:
    """Names an assignment binds to `<var_name>.get()` — direct or tuple-unpacked."""
    if _is_contextvar_get(assign.value, var_name):
        return [n for target in assign.targets for n in _extract_target_names(target)]
    if isinstance(assign.value, ast.Tuple):
        return [
            target_elt.id
            for target in assign.targets
            if isinstance(target, ast.Tuple)
            for target_elt, value_elt in zip(target.elts, assign.value.elts)
            if isinstance(target_elt, ast.Name) and _is_contextvar_get(value_elt, var_name)
        ]
    return []


def _assignment_captures_contextvar_get(fn_node: ast.AST, var_name: str) -> bool:
    return any(
        _names_bound_to_contextvar_get(node, var_name)
        for node in ast.walk(fn_node)
        if isinstance(node, ast.Assign)
    )


def _has_proper_team_modules_roundtrip(fn_node: ast.AST) -> bool:
    saved_names: set[str] = set()
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Assign):
            saved_names.update(
                _names_bound_to_contextvar_get(node, "current_can_view_team_modules")
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "set"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "current_can_view_team_modules"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in saved_names
        ):
            return True
    return False


def _saves_three_authz_vars(fn_node: ast.AST) -> bool:
    return (
        _assignment_captures_contextvar_get(fn_node, "current_user_id")
        and _assignment_captures_contextvar_get(fn_node, "current_company_id")
        and _assignment_captures_contextvar_get(fn_node, "current_is_system_admin")
    )


def _function_has_hand_copied_authz(fn_node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    if not _saves_three_authz_vars(fn_node):
        return False
    has_hand_set = any(_is_hand_rolled_set_authz(node) for node in ast.walk(fn_node))
    if not has_hand_set:
        return False
    if _has_proper_team_modules_roundtrip(fn_node):
        return False
    return True


def _function_def_from_source(source: str) -> ast.FunctionDef:
    module = ast.parse(source)
    fn = module.body[0]
    assert isinstance(fn, ast.FunctionDef)
    return fn


def _find_hand_copied_authz_sites() -> list[tuple[str, str, int]]:
    """Scan production sources for the old hand-copied authz shape.

    Only `set_authz_context(...)` callers can possibly match — a substring
    pre-filter skips ast.parse on the ~98% of files that can't (measured:
    31/1598 files contain the literal call), same idea as
    test_router_money_coercion_guard.py's shared-scan comment.
    """
    matches: list[tuple[str, str, int]] = []
    for path in sorted(iter_prod_python_sources()):
        source = path.read_text(encoding="utf-8")
        if "set_authz_context" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _function_has_hand_copied_authz(node):
                    matches.append(
                        (str(path.relative_to(REPO_ROOT)), node.name, node.lineno)
                    )
    return matches


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            """
def renamed_hand_copy():
    saved_uid = current_user_id.get()
    saved_cid = current_company_id.get()
    saved_isa = current_is_system_admin.get()
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)
    try:
        pass
    finally:
        set_authz_context(user_id=saved_uid, company_id=saved_cid, is_system_admin=saved_isa)
""",
            True,
        ),
        (
            """
def tuple_unpack_hand_copy():
    saved_uid, saved_cid, saved_isa = (
        current_user_id.get(),
        current_company_id.get(),
        current_is_system_admin.get(),
    )
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)
    try:
        pass
    finally:
        set_authz_context(
            user_id=saved_uid, company_id=saved_cid, is_system_admin=saved_isa
        )
""",
            True,
        ),
        (
            """
def get_without_team_restore():
    prior_uid, prior_cid, prior_isa = (
        current_user_id.get(),
        current_company_id.get(),
        current_is_system_admin.get(),
    )
    prior_team = current_can_view_team_modules.get()
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)
    try:
        pass
    finally:
        set_authz_context(user_id=prior_uid, company_id=prior_cid, is_system_admin=prior_isa)
""",
            True,
        ),
        (
            """
def correct_get_then_restore():
    prior_uid = current_user_id.get()
    prior_cid = current_company_id.get()
    prior_isa = current_is_system_admin.get()
    prior_team = current_can_view_team_modules.get()
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)
    try:
        pass
    finally:
        set_authz_context(user_id=prior_uid, company_id=prior_cid, is_system_admin=prior_isa)
        current_can_view_team_modules.set(prior_team)
""",
            False,
        ),
    ],
)
def test_hand_copied_authz_detector_snippets(source, expected):
    fn = _function_def_from_source(source)
    assert _function_has_hand_copied_authz(fn) is expected


def test_system_authz_contextmanager_is_not_hand_copy():
    fn = _function_def_from_source(inspect.getsource(system_authz))
    assert _function_has_hand_copied_authz(fn) is False


def test_no_hand_copied_three_var_authz_blocks_anywhere():
    """U-268 closed the 6th and last hand-copy (completion_job.run_job) — no
    whitelist remains; every hand-copied shape anywhere is now a regression."""
    matches = _find_hand_copied_authz_sites()
    assert matches == [], (
        "Hand-copied 3-of-4-var authz blocks must use system_authz(); "
        f"unexpected matches: {matches!r}"
    )


def _capture_scheduler_sync_fn(register_fn, job_id: str):
    scheduler = Mock()
    jobs: list[tuple[str | None, object]] = []
    scheduler.add_job = lambda fn, **kwargs: jobs.append((kwargs.get("id"), fn))

    captured: dict[str, object] = {}

    async def _capture_to_thread(fn, *args, **kwargs):
        captured["fn"] = fn
        return None

    with patch.object(asyncio, "to_thread", _capture_to_thread):
        register_fn(scheduler)
        async_job = next(fn for jid, fn in jobs if jid == job_id)
        asyncio.run(async_job())
    sync_fn = captured.get("fn")
    assert sync_fn is not None, f"did not capture sync fn for job {job_id!r}"
    return sync_fn


def test_scheduler_reconcile_bills_runs_under_system_authz():
    admin_seen: list[bool] = []

    class FakeReconciliationService:
        def reconcile_bills(self, *, realm_id: str) -> None:
            admin_seen.append(current_is_system_admin.get())

        def reconcile_purchases(self, *, realm_id: str) -> None:
            admin_seen.append(current_is_system_admin.get())

        def reconcile_vendor_credits(self, *, realm_id: str) -> None:
            admin_seen.append(current_is_system_admin.get())

    sync_fn = _capture_scheduler_sync_fn(_register_qbo_reconcile_jobs, "qbo_reconcile_bills")

    fake_auth = SimpleNamespace(realm_id="realm-1")
    with patch(
        "integrations.intuit.qbo.auth.business.service.QboAuthService",
        return_value=Mock(ensure_valid_token=Mock(return_value=fake_auth)),
    ), patch(
        "integrations.intuit.qbo.reconciliation.business.service.ReconciliationService",
        FakeReconciliationService,
    ):
        set_authz_context(user_id=1, company_id=1, is_system_admin=False)
        sync_fn()

    assert admin_seen == [True, True, True]
    assert current_is_system_admin.get() is False


def test_scheduler_reconcile_excel_runs_under_system_authz():
    admin_seen: list[bool] = []

    class FakeExcelMissingRowDetector:
        def run(self) -> None:
            admin_seen.append(current_is_system_admin.get())

    sync_fn = _capture_scheduler_sync_fn(_register_ms_reconcile_jobs, "ms_reconcile_excel")

    with patch(
        "integrations.ms.reconciliation.business.excel_detector.ExcelMissingRowDetector",
        FakeExcelMissingRowDetector,
    ):
        set_authz_context(user_id=1, company_id=1, is_system_admin=False)
        sync_fn()

    assert admin_seen == [True]
    assert current_is_system_admin.get() is False


@pytest.mark.parametrize(
    "repo_cls,row_factory,attr",
    [
        (VendorRepository, _vendor_row, "qbo_active"),
        (SubCostCodeRepository, _sub_cost_code_row, "qbo_active"),
        (PaymentTermRepository, _payment_term_row, "qbo_active"),
    ],
)
def test_repo_from_db_qbo_active_mapping(repo_cls, row_factory, attr):
    repo = repo_cls()

    without = repo._from_db(row_factory())
    assert getattr(without, attr) is None
    assert without.to_dict()[attr] is None

    active = repo._from_db(row_factory(QboActive=True))
    assert getattr(active, attr) is True
    assert active.to_dict()[attr] is True

    inactive = repo._from_db(row_factory(QboActive=False))
    assert getattr(inactive, attr) is False
    assert inactive.to_dict()[attr] is False
