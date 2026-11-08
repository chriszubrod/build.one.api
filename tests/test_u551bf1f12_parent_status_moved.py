"""U-551b-f1-f12: a status-only approval between the pull's status read and its UPDATE is refused.

SQL pins + repo/service/connector behaviour. Everything is stubbed; no DB.
"""
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.expense_line_item.persistence.repo import ExpenseLineItemRepository
from shared.api.errors import ApiError, ErrorCode
from shared.database import DatabaseConcurrencyError
from shared.lifecycle.terminal_lock import ParentStatusMovedError
from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params, strip_sql_comments
from tests.test_u551bf1f1_pull_preserves_local_coding_under_review import (
    _MODULE,
    _SequencedExpenseService,
    _drive,
    _drive_sequenced,
    _wire_connector,
    _local_line,
    _qbo_line,
    QBO_PROJECT,
    QBO_SCC,
)
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    PurchaseLineExpenseLineItemConnector,
)

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")

_SQL = REPO_ROOT / "entities/expense_line_item/sql/dbo.expense_line_item.sql"
_NAME = "UpdateExpenseLineItemById"


def _body():
    return strip_sql_comments(sproc_body(_SQL, _NAME))


# ---- SQL text pins ----

def test_sproc_declares_observed_parent_status_param_defaulting_null():
    # kills: dropping the param or removing `= NULL` (old container's name-bound calls would break)
    assert re.search(r"@ObservedParentStatus\s+NVARCHAR\(20\)\s*=\s*NULL", sproc_params(_SQL, _NAME))


def test_sproc_raises_parent_status_moved_after_commit_with_no_rollback():
    # kills: RAISERROR before COMMIT (lock held / txn abort) or a ROLLBACK introduced
    body = _body()
    assert re.search(
        r"COMMIT\s+TRANSACTION;\s*RAISERROR\('PARENT_STATUS_MOVED:", body
    )
    assert "ROLLBACK" not in body.upper()


def test_sproc_compares_observed_to_locked_target_status():
    # kills: a check that drops the NULL-target arm or the inequality (never fires / always fires)
    body = _body()
    assert re.search(
        r"@ObservedParentStatus\s+IS\s+NOT\s+NULL\s+AND\s*\(@TargetExpenseStatus\s+IS\s+NULL\s+OR\s+"
        r"@TargetExpenseStatus\s*<>\s*@ObservedParentStatus\)",
        body,
    )


def test_parent_status_check_sits_after_holding_code_block():
    # kills: moving the fence before HOLDING_CODE / before @TargetExpenseStatus is read
    body = _body()
    assert body.index("@TargetExpenseStatus NVARCHAR(20)") < body.index("HOLDING_CODE:") < body.index("PARENT_STATUS_MOVED:")


# ---- Repo ----

def _repo_update(error=None, **kwargs):
    repo = ExpenseLineItemRepository()
    line = SimpleNamespace(
        id=1, row_version_bytes=b"x", expense_id=2, sub_cost_code_id=None, project_id=None,
        description=None, quantity=None, rate=None, amount=None, is_billable=None,
        is_billed=None, markup=None, price=None, is_draft=None,
    )
    conn = MagicMock()
    with patch("entities.expense_line_item.persistence.repo.call_procedure",
               side_effect=error) as cp:
        try:
            repo.update_by_id(line, conn=conn, **kwargs)
        except Exception as exc:
            return cp, exc
    return cp, None


def test_repo_maps_sproc_token_to_typed_error_not_value_or_concurrency():
    # kills: omitting the reraise (falls to map_database_error) or mapping to ValueError/concurrency
    _, exc = _repo_update(error=Exception("('42000', '[42000] PARENT_STATUS_MOVED: the Expense status changed')"))
    assert isinstance(exc, ParentStatusMovedError)
    assert not isinstance(exc, (ValueError, DatabaseConcurrencyError, RuntimeError))


def test_repo_sends_param_only_when_given():
    # kills: always sending (breaks old sproc) or never sending (fence inert)
    cp, _ = _repo_update(observed_parent_status="draft")
    assert cp.call_args.kwargs["params"]["ObservedParentStatus"] == "draft"
    cp, _ = _repo_update()
    assert "ObservedParentStatus" not in cp.call_args.kwargs["params"]


# ---- Service ----

def _service(update_side_effect=None):
    svc = ExpenseLineItemService.__new__(ExpenseLineItemService)
    svc.repo = Mock()
    svc.repo.update_by_id.side_effect = update_side_effect
    existing = SimpleNamespace(expense_id=2, row_version=None)
    svc.read_by_public_id = Mock(return_value=existing)
    svc._reassert_after_a_lost_write = Mock()
    return svc


def test_service_passes_observed_parent_status_to_repo():
    # kills: dropping the pass-through
    svc = _service()
    svc.update_by_public_id("p", row_version="AAAA", _via_internal_pipeline=True, observed_parent_status="in_review")
    assert svc.repo.update_by_id.call_args.kwargs["observed_parent_status"] == "in_review"


def test_service_lets_parent_status_moved_propagate_untouched():
    # kills: the concurrency handler (or a broad except) swallowing / re-asserting on the new error
    err = ParentStatusMovedError()
    svc = _service(update_side_effect=err)
    with pytest.raises(ParentStatusMovedError) as ei:
        svc.update_by_public_id("p", row_version="AAAA", _via_internal_pipeline=True, observed_parent_status="draft")
    assert ei.value is err
    svc._reassert_after_a_lost_write.assert_not_called()


# ---- Connector ----

def _drive_raising(exc, *statuses):
    """Returns (raised exc or None, connector, stamp-heal mock)."""
    seq = _SequencedExpenseService(*statuses)
    connector, _ = _wire_connector(_local_line(), seq, update_side_effect=exc)
    with patch(f"{_MODULE}.resolve_dbo_sub_cost_code", return_value=SimpleNamespace(id=QBO_SCC)), \
         patch(f"{_MODULE}.verify_identity_dbo_only", return_value="cust-1"), \
         patch(f"{_MODULE}.stamp_line_identity_or_warn") as heal:
        try:
            connector.sync_from_qbo_purchase_line(
                10, "exp-pub", _qbo_line(), frozenset({"2"}), realm_id="realm-1")
        except Exception as raised:
            return raised, connector, heal
    return None, connector, heal


def test_observed_status_is_the_late_read_not_the_early_one():
    # kills: passing the early (first) status, or a constant, as observed_parent_status
    kwargs, _ = _drive_sequenced("draft", "in_review")
    assert kwargs["observed_parent_status"] == "in_review"


def test_observed_status_none_when_parent_unreadable():
    # kills: sending a fabricated status when the late read yields nothing
    kwargs, _ = _drive("draft", _local_line(), expense=None)
    assert kwargs.get("observed_parent_status") is None


def test_parent_status_moved_holds_watermark_with_race_runtimeerror(caplog):
    # kills: "return direct" reinstated (no raise => line silently un-mirrored, watermark advances);
    #        "raise ValueError" (permanent-skip class); a second INFO log; reconcile issue; realm self-heal
    with caplog.at_level(logging.INFO, logger=_MODULE):
        exc, connector, heal = _drive_raising(ParentStatusMovedError(), "draft", "draft")
    assert isinstance(exc, RuntimeError)
    assert connector.reconciliation_repo.method_calls == []
    heal.assert_not_called()
    assert not isinstance(exc, ValueError)
    assert "concurrent write race" in str(exc)
    msgs = [r for r in caplog.records if "parent_status_moved_line_skipped" in r.getMessage()]
    assert len(msgs) == 1 and msgs[0].levelno == logging.INFO
    assert "Reviewer description" not in msgs[0].getMessage()


def test_parent_status_moved_raise_is_not_permanent_skip_or_api_error():
    # kills: wrong class => ValueError permanent skip, or ApiError/HTTPException 409 leaking out of the pull
    exc, _, _ = _drive_raising(ParentStatusMovedError(), "draft", "draft")
    assert isinstance(exc, RuntimeError)
    assert not isinstance(exc, (ValueError, ApiError, HTTPException, ParentStatusMovedError))


def test_status_read_is_locked_and_ordered_between_count_lock_and_fence():
    # kills: dropping the UPDLOCK/HOLDLOCK hint from the status read, or moving the read before the COUNT lock / after the fence
    body = _body()
    sel = body.index("SELECT @TargetExpenseStatus = [Status]")
    stmt = body[sel: body.index(";", sel)]
    assert "WITH (UPDLOCK, HOLDLOCK)" in stmt
    assert body.rfind("WITH (UPDLOCK, HOLDLOCK)", 0, sel) != -1
    assert sel < body.index("PARENT_STATUS_MOVED:")


def test_error_code_constant_matches_wire_literal():
    # kills: constant drifting from the literal clients key on
    assert ErrorCode.PARENT_STATUS_MOVED == "parent_status_moved"
    assert ParentStatusMovedError().error_code == "parent_status_moved"


def test_concurrency_error_still_propagates():
    # kills: broadening the except so a real ROWVERSION conflict is swallowed as a skip
    exc, _, _ = _drive_raising(DatabaseConcurrencyError("rv"), "draft", "draft")
    assert isinstance(exc, DatabaseConcurrencyError)
