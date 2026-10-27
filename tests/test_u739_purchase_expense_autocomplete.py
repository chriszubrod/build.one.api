"""U-739 — auto-complete pull-born drafts after purchase pull UPDATE when fully coded."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
    expense_autocomplete_skip_reason,
    local_line_is_holding_sub_cost_code,
)

SERVICE_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"
LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)
UNCODED_ACCOUNT = "Cost of construction : NEED TO CATEGORIZE"

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _qbo_purchase(**over):
    base = dict(
        id=4,
        qbo_id="PURCH-99",
        realm_id="realm-1",
        entity_ref_value="V-1",
        doc_number="INV-100",
        txn_date="2026-08-01",
        private_note="memo",
        total_amt=100,
        credit=False,
        sync_token="3",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _staged_line(*, line_id="1", account=UNCODED_ACCOUNT, item_ref="item-9"):
    return SimpleNamespace(
        id=1,
        qbo_line_id=line_id,
        account_ref_name=account,
        item_ref_value=item_ref,
    )


def _local_line(*, line_id="1", scc_id=42):
    return SimpleNamespace(id=10, qbo_id=line_id, sub_cost_code_id=scc_id)


def _expense(**over):
    base = dict(
        id=55,
        public_id="pub-55",
        reference_number="R-1",
        row_version="rv-55",
        status="draft",
        status_origin="qbo_pull",
        is_draft=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _build_hit_connector(*, local_lines, scc_service=None):
    expense_service = Mock()
    direct = _expense()
    expense_service.read_by_qbo_identity.return_value = direct
    expense_service.update_by_public_id.return_value = direct
    expense_service.repo = Mock()

    line_connector = Mock()
    eli_svc = Mock()
    eli_svc.read_by_expense_id.return_value = local_lines
    line_connector.expense_line_item_service = eli_svc

    scc = scc_service or Mock()
    scc.is_holding_code.return_value = False
    scc.read_by_id.return_value = SimpleNamespace(number="10.01")

    with patch(LINE_CONNECTOR_PATH, return_value=line_connector):
        connector = PurchaseExpenseConnector(
            expense_service=expense_service,
            sub_cost_code_service=scc,
            autocomplete_coded_drafts=True,
        )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    connector._line_connector = line_connector
    return connector, expense_service, direct


def _run_hit(connector, expense_service, lines=None):
    lines = lines or [_staged_line()]
    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        return connector.sync_from_qbo_purchase(_qbo_purchase(), lines)


# ---------------------------------------------------------------------------
# Pure eligibility helper
# ---------------------------------------------------------------------------


def test_skip_reason_none_when_all_rules_hold():
    scc = Mock()
    scc.is_holding_code.return_value = False
    scc.read_by_id.return_value = SimpleNamespace(number="10.01")
    assert (
        expense_autocomplete_skip_reason(
            _expense(),
            [_staged_line()],
            [_local_line()],
            scc,
        )
        is None
    )


@pytest.mark.parametrize(
    "expense_over,expected",
    [
        ({"status": "completed", "is_draft": False}, "not_draft"),
        ({"status_origin": "user"}, "status_origin_not_eligible"),
        ({"status_origin": "backfill"}, "status_origin_not_eligible"),
        ({"status_origin": "completion"}, "status_origin_not_eligible"),
    ],
)
def test_skip_reason_header_gates(expense_over, expected):
    scc = Mock()
    scc.is_holding_code.return_value = False
    assert (
        expense_autocomplete_skip_reason(
            _expense(**expense_over),
            [_staged_line()],
            [_local_line()],
            scc,
        )
        == expected
    )


def test_skip_reason_staged_uncoded_line():
    scc = Mock()
    staged = [_staged_line(account=UNCODED_ACCOUNT, item_ref=None)]
    assert (
        expense_autocomplete_skip_reason(_expense(), staged, [_local_line()], scc)
        == "staged_uncoded_line"
    )


def test_skip_reason_missing_sub_cost_code():
    scc = Mock()
    assert (
        expense_autocomplete_skip_reason(
            _expense(),
            [_staged_line()],
            [_local_line(scc_id=None)],
            scc,
        )
        == "missing_sub_cost_code"
    )


def test_skip_reason_holding_by_flag():
    scc = Mock()
    scc.is_holding_code.return_value = True
    assert (
        expense_autocomplete_skip_reason(
            _expense(),
            [_staged_line()],
            [_local_line(scc_id=99)],
            scc,
        )
        == "holding_sub_cost_code"
    )


def test_skip_reason_holding_by_number_00_02():
    scc = Mock()
    scc.is_holding_code.return_value = False
    scc.read_by_id.return_value = SimpleNamespace(number="00.02")
    assert local_line_is_holding_sub_cost_code(99, scc) is True
    assert (
        expense_autocomplete_skip_reason(
            _expense(),
            [_staged_line()],
            [_local_line(scc_id=99)],
            scc,
        )
        == "holding_sub_cost_code"
    )


def test_skip_reason_line_qbo_id_mismatch_leftover():
    scc = Mock()
    scc.is_holding_code.return_value = False
    local = [_local_line(line_id="1"), _local_line(line_id="2")]
    assert (
        expense_autocomplete_skip_reason(_expense(), [_staged_line()], local, scc)
        == "line_qbo_id_mismatch"
    )


def test_skip_reason_line_qbo_id_mismatch_null_local_qbo_id():
    scc = Mock()
    scc.is_holding_code.return_value = False
    local = [_local_line(line_id="1"), SimpleNamespace(id=11, qbo_id=None, sub_cost_code_id=42)]
    assert (
        expense_autocomplete_skip_reason(_expense(), [_staged_line()], local, scc)
        == "line_qbo_id_mismatch"
    )


def test_skip_reason_line_qbo_id_mismatch_duplicate_local_qbo_id():
    scc = Mock()
    scc.is_holding_code.return_value = False
    local = [_local_line(line_id="1"), _local_line(line_id="1")]
    assert (
        expense_autocomplete_skip_reason(_expense(), [_staged_line()], local, scc)
        == "line_qbo_id_mismatch"
    )


def test_skip_reason_line_qbo_id_mismatch_null_staged_qbo_line_id():
    scc = Mock()
    scc.is_holding_code.return_value = False
    staged = [_staged_line(line_id="1"), _staged_line(line_id=None)]
    staged[1].qbo_line_id = None
    assert (
        expense_autocomplete_skip_reason(_expense(), staged, [_local_line()], scc)
        == "line_qbo_id_mismatch"
    )


def test_skip_reason_line_qbo_id_mismatch_missing_staged():
    scc = Mock()
    scc.is_holding_code.return_value = False
    assert (
        expense_autocomplete_skip_reason(
            _expense(),
            [_staged_line(line_id="1"), _staged_line(line_id="2")],
            [_local_line(line_id="1")],
            scc,
        )
        == "line_qbo_id_mismatch"
    )


def test_skip_reason_zero_lines():
    scc = Mock()
    assert expense_autocomplete_skip_reason(_expense(), [_staged_line()], [], scc) == "zero_lines"


# ---------------------------------------------------------------------------
# Connector UPDATE path — complete_expense
# ---------------------------------------------------------------------------


def test_hit_path_autocompletes_when_eligible():
    connector, expense_service, direct = _build_hit_connector(local_lines=[_local_line()])
    completed = _expense(status="completed", is_draft=False)
    expense_service.complete_expense.return_value = {"expense_finalized": True}
    expense_service.read_by_public_id.return_value = completed

    with patch("shared.authz.context.system_authz") as mock_authz:
        mock_authz.return_value.__enter__ = Mock(return_value=None)
        mock_authz.return_value.__exit__ = Mock(return_value=False)
        result = _run_hit(connector, expense_service)

    mock_authz.assert_called_once()
    mock_authz.return_value.__enter__.assert_called_once()
    expense_service.complete_expense.assert_called_once_with("pub-55")
    assert result is completed


@pytest.mark.parametrize(
    "expense_over",
    [
        {"status": "in_review", "is_draft": False},
        {"status_origin": "user"},
    ],
)
def test_hit_path_does_not_autocomplete_when_ineligible(expense_over):
    connector, expense_service, direct = _build_hit_connector(local_lines=[_local_line()])
    direct.status = expense_over.get("status", direct.status)
    direct.status_origin = expense_over.get("status_origin", direct.status_origin)

    _run_hit(connector, expense_service)
    expense_service.complete_expense.assert_not_called()


def test_hit_path_skips_autocomplete_when_staged_still_uncoded():
    connector, expense_service, _ = _build_hit_connector(local_lines=[_local_line()])
    lines = [_staged_line(account=UNCODED_ACCOUNT, item_ref=None)]
    _run_hit(connector, expense_service, lines=lines)
    expense_service.complete_expense.assert_not_called()


def test_complete_failure_isolated_pull_still_succeeds(caplog):
    connector, expense_service, direct = _build_hit_connector(local_lines=[_local_line()])
    expense_service.complete_expense.return_value = {
        "expense_finalized": False,
        "message": "Vendor not found",
    }

    with patch("shared.authz.context.system_authz") as mock_authz:
        mock_authz.return_value.__enter__ = Mock(return_value=None)
        mock_authz.return_value.__exit__ = Mock(return_value=False)
        with caplog.at_level(logging.WARNING):
            result = _run_hit(connector, expense_service)

    assert result is direct
    assert any(
        r.message == "qbo.purchase.expense_autocomplete_failed" for r in caplog.records
    )


def test_complete_exception_isolated_pull_still_succeeds(caplog):
    connector, expense_service, direct = _build_hit_connector(local_lines=[_local_line()])
    expense_service.complete_expense.side_effect = RuntimeError("boom")

    with patch("shared.authz.context.system_authz") as mock_authz:
        mock_authz.return_value.__enter__ = Mock(return_value=None)
        mock_authz.return_value.__exit__ = Mock(return_value=False)
        with caplog.at_level(logging.WARNING):
            result = _run_hit(connector, expense_service)

    assert result is direct
    failed = [
        r
        for r in caplog.records
        if r.message == "qbo.purchase.expense_autocomplete_failed"
    ]
    assert failed and failed[0].reason == "RuntimeError"


def test_create_miss_path_never_calls_complete():
    expense_service = Mock()
    expense_service.repo = Mock()
    expense_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77", qbo_id=None, realm_id=None)
    expense_service.create.return_value = created
    expense_service.read_by_id.return_value = SimpleNamespace(
        id=77, public_id="pub-77", qbo_id="PURCH-99", realm_id="realm-1"
    )
    with patch(LINE_CONNECTOR_PATH, return_value=Mock()):
        connector = PurchaseExpenseConnector(expense_service=expense_service)
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        connector.sync_from_qbo_purchase(
            _qbo_purchase(),
            [_staged_line(account=UNCODED_ACCOUNT, item_ref=None)],
        )

    expense_service.complete_expense.assert_not_called()


def test_coding_backfill_redrafted_expense_is_eligible():
    """Chris 2026-10-27: rows the U-486b coding backfill re-drafted qualify once coded."""
    from integrations.intuit.qbo.purchase.connector.expense.business.service import AUTOCOMPLETE_STATUS_ORIGINS

    assert AUTOCOMPLETE_STATUS_ORIGINS == frozenset({"qbo_pull", "coding_backfill"})


def test_autocomplete_flag_false_never_completes():
    connector, expense_service, _ = _build_hit_connector(local_lines=[_local_line()])
    connector.autocomplete_coded_drafts = False
    _run_hit(connector, expense_service)
    expense_service.complete_expense.assert_not_called()


def test_completed_expense_skips_autocomplete_without_line_read():
    connector, expense_service, direct = _build_hit_connector(local_lines=[_local_line()])
    direct.status = "completed"
    direct.is_draft = False
    eli_svc = connector._line_connector.expense_line_item_service
    _run_hit(connector, expense_service)
    eli_svc.read_by_expense_id.assert_not_called()
    expense_service.complete_expense.assert_not_called()


def test_line_read_exception_isolated_pull_still_succeeds(caplog):
    connector, expense_service, direct = _build_hit_connector(local_lines=[_local_line()])
    connector._line_connector.expense_line_item_service.read_by_expense_id.side_effect = (
        RuntimeError("db down")
    )
    with caplog.at_level(logging.WARNING):
        result = _run_hit(connector, expense_service)
    assert result is direct
    assert any(
        r.message == "qbo.purchase.expense_autocomplete_failed"
        and getattr(r, "reason", None) == "RuntimeError"
        for r in caplog.records
    )
    expense_service.complete_expense.assert_not_called()


def test_skip_reason_exception_isolated(caplog):
    connector, expense_service, direct = _build_hit_connector(local_lines=[_local_line()])
    with patch(
        f"{SERVICE_MODULE}.expense_autocomplete_skip_reason",
        side_effect=ValueError("bad skip"),
    ):
        with caplog.at_level(logging.WARNING):
            result = _run_hit(connector, expense_service)
    assert result is direct
    assert any(
        r.message == "qbo.purchase.expense_autocomplete_failed"
        and getattr(r, "reason", None) == "ValueError"
        for r in caplog.records
    )


def test_autocompleted_with_errors_logs_warning_not_success(caplog):
    connector, expense_service, direct = _build_hit_connector(local_lines=[_local_line()])
    expense_service.complete_expense.return_value = {
        "expense_finalized": True,
        "errors": [{"step": "excel_sync", "error": "mapping missing"}],
    }
    completed = _expense(status="completed", is_draft=False)
    expense_service.read_by_public_id.return_value = completed

    with patch("shared.authz.context.system_authz") as mock_authz:
        mock_authz.return_value.__enter__ = Mock(return_value=None)
        mock_authz.return_value.__exit__ = Mock(return_value=False)
        with caplog.at_level(logging.WARNING):
            result = _run_hit(connector, expense_service)

    assert result is completed
    assert not any(r.message == "qbo.purchase.expense_autocompleted" for r in caplog.records)
    warn = [
        r
        for r in caplog.records
        if r.message == "qbo.purchase.expense_autocompleted_with_errors"
    ]
    assert len(warn) == 1
    assert warn[0].error_count == 1
    assert warn[0].steps == ["excel_sync"]


def test_scheduled_purchase_pull_opt_in_autocomplete_flag():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "scripts/sync_qbo_purchase.py").read_text(
        encoding="utf-8"
    )
    assert "PurchaseExpenseConnector(autocomplete_coded_drafts=True)" in text
