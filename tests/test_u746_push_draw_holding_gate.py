"""U-746 — a source on the holding SCC (00.02) is never billed through push-draw.

U-632 gated `complete_invoice`, but `push_draw` (the path QBO-pulled draws take)
marked sources billed unchecked, and `force=true` waived the whole audit. Found on
SHT-27 (2026-10-29): a $21.27 Expense line on 00.02 sat in a draw about to publish.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from entities.invoice.business.audit import (
    InvoiceDrawAuditService,
    assemble_audit_report,
    compute_holding_code_gaps,
)
from entities.invoice.business.push import evaluate_gates
from entities.invoice.business.service import InvoiceService
from entities.sub_cost_code.business.service import SubCostCodeService
from shared.lifecycle.completion_gate import HoldingCodeRefused
from tests.test_invoice_draw_push import (
    _audit_clear,
    _gates_open,
    _push_service_with_mocks,
)

HOLDING_SCC_ID = 481
REAL_SCC_ID = 264


def _is_holding(scc_id):
    return scc_id == HOLDING_SCC_ID


def _patch_holding():
    return patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        side_effect=_is_holding,
    )


# --- evaluate_gates ---------------------------------------------------------


@pytest.mark.parametrize("verdict", ["clear", "halt"])
@pytest.mark.parametrize("force", [False, True])
def test_evaluate_gates_holding_code_is_not_waived_by_force(verdict, force):
    assert (
        evaluate_gates("true", "true", audit_verdict=verdict, force=force, holding_code=True)
        == "holding_code"
    )


def test_evaluate_gates_writes_disabled_still_reported_first():
    assert (
        evaluate_gates("false", "true", audit_verdict="halt", force=True, holding_code=True)
        == "writes_disabled"
    )


# --- audit ------------------------------------------------------------------


def _cov(ili_id, scc_id):
    return {
        "invoice_line_item_id": ili_id,
        "source_type": "ExpenseLineItem",
        "source_line_item_id": 1000 + ili_id,
        "attachment_count": 1,
        "sub_cost_code_id": scc_id,
    }


def test_compute_holding_code_gaps_keeps_only_holding_sources():
    rows = [_cov(1, REAL_SCC_ID), _cov(2, HOLDING_SCC_ID), _cov(3, None)]
    gaps = compute_holding_code_gaps(rows, _is_holding)
    assert [g["invoice_line_item_id"] for g in gaps] == [2]


def test_assemble_audit_report_emits_holding_code_halt():
    report = assemble_audit_report(
        {"qbo_mapping_present": True, "holding_code_gaps": [_cov(2, HOLDING_SCC_ID)]}
    )
    assert report["verdict"] == "halt"
    assert report["gaps"] == [
        {
            "class": "holding_code",
            "severity": "halt",
            "invoice_line_item_id": 2,
            "source_type": "ExpenseLineItem",
            "source_line_item_id": 1002,
            "sub_cost_code_id": HOLDING_SCC_ID,
        }
    ]


def _audit_service_for_one_line(monkeypatch, scc_id, own_scc_id=None):
    invoice = SimpleNamespace(
        id=1129, public_id="inv-pub", project_id=None, invoice_number="SHT-27"
    )
    invoice_service = Mock()
    invoice_service.read_by_public_id.return_value = invoice
    invoice_service.holding_scc_service.return_value = SubCostCodeService()
    invoice_service.invoice_line_item_service.read_by_invoice_id.return_value = [
        SimpleNamespace(id=35232, sub_cost_code_id=own_scc_id)
    ]
    reconciliation = Mock()
    reconciliation.propose_links.return_value = {
        "lines": [
            {
                "invoice_line_item_id": 35232,
                "status": "linkable",
                "proposed": {"source_type": "ExpenseLineItem", "source_line_item_id": 13051},
            }
        ],
        "summary": {"linkable": 1},
    }
    invoice_repo = Mock()
    invoice_repo.read_source_line_coverage.return_value = {
        ("ExpenseLineItem", 13051): {"attachment_count": 1, "sub_cost_code_id": scc_id},
    }
    svc = InvoiceDrawAuditService(
        invoice_service=invoice_service,
        invoice_repo=invoice_repo,
        reconciliation_service=reconciliation,
        project_service=Mock(),
        qbo_customer_repo=Mock(),
        sync_repo=Mock(),
        box_workbook_repo=Mock(),
        box_folder_repo=Mock(),
    )
    monkeypatch.setattr(svc, "_read_qbo_mapping", lambda project: {"present": True})
    monkeypatch.setattr(svc, "_read_duplicate_projects", lambda project_id: [])
    monkeypatch.setattr(svc, "_read_box_mappings", lambda project_id: {})
    monkeypatch.setattr(svc, "_read_staging_freshness", lambda: {"stale": []})
    monkeypatch.setattr(svc, "_build_double_bill_lines", lambda lines, bindings: [])
    monkeypatch.setattr(svc, "_scan_foreign_project_markers", lambda project, bindings: [])
    return svc


@pytest.mark.parametrize(
    "scc_id, expect_halt",
    [(HOLDING_SCC_ID, True), (REAL_SCC_ID, False)],
)
def test_audit_flags_a_proposed_source_on_the_holding_code(monkeypatch, scc_id, expect_halt):
    svc = _audit_service_for_one_line(monkeypatch, scc_id)
    with _patch_holding():
        report = svc.audit("inv-pub")

    holding = [g for g in report["gaps"] if g["class"] == "holding_code"]
    assert bool(holding) is expect_halt
    assert report["verdict"] == ("halt" if expect_halt else "clear")


def test_audit_flags_an_invoice_line_on_the_holding_code_itself(monkeypatch):
    svc = _audit_service_for_one_line(monkeypatch, REAL_SCC_ID, own_scc_id=HOLDING_SCC_ID)
    with _patch_holding():
        report = svc.audit("inv-pub")

    holding = [g for g in report["gaps"] if g["class"] == "holding_code"]
    assert [(g["invoice_line_item_id"], g["source_type"]) for g in holding] == [(35232, None)]
    assert report["verdict"] == "halt"


# --- push_draw ----------------------------------------------------------------


def test_push_draw_holding_gap_halts_before_any_write_even_with_force(monkeypatch):
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks()
    gap = {"class": "holding_code", "severity": "halt", "invoice_line_item_id": 35232}
    collabs["audit_service"].audit.return_value = {"verdict": "halt", "gaps": [gap]}

    result = svc.push_draw("inv-pub-id", force=True)

    assert result["status"] == "halt"
    assert result["reason"] == "holding_code"
    assert result["holding_lines"] == [gap]
    collabs["reconciliation_service"].apply_links.assert_not_called()
    invoice_service._mark_source_as_billed.assert_not_called()


def test_push_draw_persisted_holding_line_halts_before_billing(monkeypatch):
    _gates_open(monkeypatch)
    packet_fn = MagicMock()
    svc, invoice_service, collabs = _push_service_with_mocks(generate_packet_fn=packet_fn)
    _audit_clear(collabs)
    line = SimpleNamespace(id=1)
    collabs["line_item_service"].read_by_invoice_id.return_value = [line]
    invoice_service.holding_line_item_ids.return_value = [35232]

    result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "holding_code"
    assert result["holding_lines"] == [{"invoice_line_item_id": 35232}]
    invoice_service.holding_line_item_ids.assert_called_once_with([line])
    invoice_service._mark_source_as_billed.assert_not_called()
    packet_fn.assert_not_called()


def test_push_draw_holding_recheck_error_fails_closed(monkeypatch):
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks()
    _audit_clear(collabs)
    invoice_service.holding_line_item_ids.side_effect = RuntimeError("db down")

    result = svc.push_draw("inv-pub-id")

    assert result["status"] == "halt"
    assert result["reason"] == "holding_code_check_failed"
    invoice_service._mark_source_as_billed.assert_not_called()


def test_holding_line_item_ids_names_own_and_source_holding_lines():
    def _ili(ili_id, own_scc, bill_line_item_id):
        return SimpleNamespace(
            id=ili_id,
            sub_cost_code_id=own_scc,
            source_type="BillLineItem" if bill_line_item_id else "Manual",
            bill_line_item_id=bill_line_item_id,
            expense_line_item_id=None,
            bill_credit_line_item_id=None,
        )

    lines = [
        _ili(1, REAL_SCC_ID, 501),       # coded line, coded source
        _ili(2, None, 502),              # source on the holding code
        _ili(3, HOLDING_SCC_ID, None),   # Manual line itself on the holding code
    ]
    source_scc = {501: REAL_SCC_ID, 502: HOLDING_SCC_ID}
    inv_svc = InvoiceService.__new__(InvoiceService)
    with patch(
        "entities.bill_line_item.business.service.BillLineItemService.read_by_id",
        side_effect=lambda bli_id: SimpleNamespace(sub_cost_code_id=source_scc[bli_id]),
    ), _patch_holding():
        assert inv_svc.holding_line_item_ids(lines) == [2, 3]
        assert inv_svc.holding_line_item_ids(lines[:1]) == []


def test_push_draw_holding_recheck_error_returns_the_type_not_the_text(monkeypatch):
    _gates_open(monkeypatch)
    svc, invoice_service, collabs = _push_service_with_mocks()
    _audit_clear(collabs)
    invoice_service.holding_line_item_ids.side_effect = RuntimeError("Bill 9917 in project 64")

    result = svc.push_draw("inv-pub-id")

    step = next(s for s in result["steps"] if s["step"] == "holding_code_recheck")
    assert step["error"] == "RuntimeError"
    assert "9917" not in str(result)


def test_complete_invoice_stops_before_packet_when_billing_refuses_a_holding_source():
    # The up-front check passed, then a recode to 00.02 raced it: the per-line
    # refusal must stop completion before the packet and uploads (U-746).
    service = InvoiceService()
    invoice = SimpleNamespace(
        id=4, public_id="inv-1", project_id=None, payment_term_id=None, is_draft=True,
        invoice_date=None, due_date=None, invoice_number="SHT-27", total_amount=None,
        memo=None, row_version="rv",
    )
    service.read_by_public_id = MagicMock(return_value=invoice)
    service.update_by_public_id = MagicMock(return_value=invoice)
    service._assert_no_holding_code_on_complete = MagicMock()
    service.invoice_line_item_service.read_by_invoice_id = MagicMock(
        return_value=[SimpleNamespace(id=1, is_draft=False)]
    )
    service._mark_source_as_billed = MagicMock(side_effect=HoldingCodeRefused("00.02"))
    service._upload_to_sharepoint = MagicMock()

    with patch("entities.invoice.business.service.PaymentTermService"), patch(
        "entities.invoice.api.router._generate_invoice_packet"
    ) as packet:
        with pytest.raises(HoldingCodeRefused):
            service.complete_invoice(public_id="inv-1")

    packet.assert_not_called()
    service._upload_to_sharepoint.assert_not_called()


# --- _mark_source_as_billed backstop -----------------------------------------

_SOURCES = {
    "BillLineItem": (
        "entities.bill_line_item.business.service.BillLineItemService",
        "bill_line_item_id",
        {"bill_id": 7},
        "entities.bill.business.service.BillService",
    ),
    "ExpenseLineItem": (
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        "expense_line_item_id",
        {"expense_id": 7},
        "entities.expense.business.service.ExpenseService",
    ),
    "BillCreditLineItem": (
        "entities.bill_credit_line_item.business.service.BillCreditLineItemService",
        "bill_credit_line_item_id",
        {"bill_credit_id": 7},
        "entities.bill_credit.business.service.BillCreditService",
    ),
}


def _mark_billed(source_type, scc_id, own_scc_id=REAL_SCC_ID):
    svc_path, fk, parent_fk, parent_path = _SOURCES[source_type]
    line_item = SimpleNamespace(
        source_type=source_type,
        sub_cost_code_id=own_scc_id,
        bill_line_item_id=None,
        expense_line_item_id=None,
        bill_credit_line_item_id=None,
    )
    setattr(line_item, fk, 13051)
    source = SimpleNamespace(
        public_id="src-pub",
        row_version="rv",
        is_billed=False,
        sub_cost_code_id=scc_id,
        **parent_fk,
    )
    inv_svc = InvoiceService.__new__(InvoiceService)
    raised = None
    with patch(svc_path) as source_cls, patch(parent_path) as parent_cls, _patch_holding():
        source_cls.return_value.read_by_id.return_value = source
        parent_cls.return_value.read_by_id.return_value = SimpleNamespace(public_id="parent-pub")
        try:
            inv_svc._mark_source_as_billed(line_item)
        except HoldingCodeRefused as exc:
            raised = exc
    return raised, source_cls.return_value.update_by_public_id


@pytest.mark.parametrize("source_type", sorted(_SOURCES))
def test_mark_source_as_billed_refuses_a_holding_source(source_type):
    raised, update = _mark_billed(source_type, HOLDING_SCC_ID)
    assert isinstance(raised, HoldingCodeRefused)
    update.assert_not_called()


@pytest.mark.parametrize("source_type", sorted(_SOURCES))
def test_mark_source_as_billed_refuses_when_the_invoice_line_itself_is_holding(source_type):
    raised, update = _mark_billed(source_type, REAL_SCC_ID, own_scc_id=HOLDING_SCC_ID)
    assert isinstance(raised, HoldingCodeRefused)
    update.assert_not_called()


@pytest.mark.parametrize("source_type", sorted(_SOURCES))
def test_mark_source_as_billed_still_bills_a_coded_source(source_type):
    raised, update = _mark_billed(source_type, REAL_SCC_ID)
    assert raised is None
    update.assert_called_once()
    assert update.call_args.kwargs["is_billed"] is True
