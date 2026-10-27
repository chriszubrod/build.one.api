"""U-739 — backfill script selection + dry-run (mocked DB)."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock, Mock, patch

import pytest

MODULE = "scripts.backfill_complete_coded_qbo_drafts"


def _reload_module():
    import scripts.backfill_complete_coded_qbo_drafts as mod

    return importlib.reload(mod)


def test_candidate_sql_requires_qbo_pull_draft_and_uncoded_predicate():
    mod = _reload_module()
    sql = mod.CANDIDATE_EXPENSES_SQL
    assert "StatusOrigin" in sql and "qbo_pull" in sql and "coding_backfill" in sql
    assert "Status] = N'draft'" in sql
    assert "NEED TO CATEGORIZE" in sql
    assert "ItemRefValue] IS NULL" in sql
    assert "IsHoldingCode" in sql
    assert "00.02" in sql


def test_dry_run_writes_would_complete_without_calling_complete(tmp_path):
    mod = _reload_module()
    out = tmp_path / "out.csv"
    candidate = MagicMock(
        ExpenseId=101,
        PublicId="pub-101",
        QboPurchaseId=500,
        QboPurchaseQboId="P-1",
    )
    header = MagicMock(Status="draft", StatusOrigin="qbo_pull")
    local_row = MagicMock(Id=1, QboId="1", SubCostCodeId=9)
    staged_row = MagicMock(
        QboLineId="1",
        AccountRefName="Materials",
        ItemRefValue="item-1",
    )

    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        [candidate],
        [local_row],
        [staged_row],
    ]
    cursor.fetchone.return_value = header

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = lambda s, *a: False

    expense_svc = MagicMock()

    with patch(f"{MODULE}.get_connection", return_value=conn), patch(
        f"{MODULE}.assert_cli_system_admin"
    ), patch(f"{MODULE}.ExpenseService", return_value=expense_svc), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService"
    ) as scc_cls:
        scc = MagicMock()
        scc.is_holding_code.return_value = False
        scc_cls.return_value = scc
        rc = mod.main(["--out", str(out)])

    assert rc == 0
    expense_svc.complete_expense.assert_not_called()
    text = out.read_text(encoding="utf-8")
    assert "would_complete" in text
    assert "101" in text


def test_apply_completed_with_errors_outcome(tmp_path):
    mod = _reload_module()
    out = tmp_path / "apply.csv"
    candidate = MagicMock(
        ExpenseId=202,
        PublicId="pub-202",
        QboPurchaseId=501,
        QboPurchaseQboId="P-2",
    )
    header = MagicMock(Status="draft", StatusOrigin="qbo_pull")
    local_row = MagicMock(Id=1, QboId="1", SubCostCodeId=9)
    staged_row = MagicMock(
        QboLineId="1",
        AccountRefName="Materials",
        ItemRefValue="item-1",
    )

    cursor = MagicMock()
    cursor.fetchall.side_effect = [[candidate], [local_row], [staged_row]]
    cursor.fetchone.return_value = header

    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda s: s
    conn.__exit__ = lambda s, *a: False

    expense_svc = MagicMock()
    expense_svc.complete_expense.return_value = {
        "expense_finalized": True,
        "errors": [{"step": "sharepoint_upload", "error": "timeout"}],
    }

    with patch(f"{MODULE}.get_connection", return_value=conn), patch(
        f"{MODULE}.assert_cli_system_admin"
    ), patch(f"{MODULE}.ExpenseService", return_value=expense_svc), patch(
        f"{MODULE}.system_authz"
    ) as mock_authz, patch(
        "entities.sub_cost_code.business.service.SubCostCodeService"
    ) as scc_cls:
        mock_authz.return_value.__enter__ = Mock(return_value=None)
        mock_authz.return_value.__exit__ = Mock(return_value=False)
        scc = MagicMock()
        scc.is_holding_code.return_value = False
        scc_cls.return_value = scc
        rc = mod.main(["--apply", "--out", str(out)])

    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "completed_with_errors:1" in text
    conn.commit.assert_not_called()
    conn.rollback.assert_not_called()
