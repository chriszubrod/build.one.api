"""U-177 — invoice source-linking pure logic (Step 4.1)."""

from decimal import Decimal
from unittest.mock import Mock, patch

from entities.invoice.business.reconciliation import (
    InvoiceReconciliationService,
    _filter_candidates_ki35,
    resolve_link_proposals,
)


def _line(
    ili_id: int,
    *,
    line_num: int = 1,
    amount="100.00",
    description="Stone Materials",
    service_date="2026-07-01",
    source_type="Manual",
    bill_line_item_id=None,
    expense_line_item_id=None,
    bill_credit_line_item_id=None,
    linked_txn_type=None,
    manual_derivative=False,
):
    return {
        "invoice_line_item_id": ili_id,
        "line_num": line_num,
        "amount": Decimal(str(amount)) if amount is not None else None,
        "description": description,
        "service_date": service_date,
        "source_type": source_type,
        "bill_line_item_id": bill_line_item_id,
        "expense_line_item_id": expense_line_item_id,
        "bill_credit_line_item_id": bill_credit_line_item_id,
        "linked_txn_type": linked_txn_type,
        "manual_derivative": manual_derivative,
    }


def _cand(
    ili_id: int,
    *,
    tier: int,
    source_type: str,
    source_line_item_id: int,
    source_project_id=None,
    direct_dbo=False,
    source_line_num=None,
):
    return {
        "invoice_line_item_id": ili_id,
        "tier": tier,
        "source_type": source_type,
        "source_line_item_id": source_line_item_id,
        "source_project_id": source_project_id,
        "direct_dbo": direct_dbo,
        "source_line_num": source_line_num,
    }


INVOICE_PROJECT = 42


def test_ki37_cross_project_rejected():
    lines = [_line(1)]
    candidates = [
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=10, source_project_id=99),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    assert out[0]["status"] == "cross_project_rejected"
    assert out[0]["reject_reason"] == "source_project_mismatch"
    assert out[0]["proposed"] is None


def test_cross_project_null_source_project_is_allowed():
    lines = [_line(1)]
    candidates = [
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=10, source_project_id=None),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    assert out[0]["status"] == "linkable"
    assert out[0]["proposed"]["source_line_item_id"] == 10


def test_fingerprint_group_two_lines_two_sources_linkable_1_to_1():
    lines = [
        _line(1, line_num=1, description="Stone Materials"),
        _line(2, line_num=2, description="Stone Materials"),
    ]
    candidates = [
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=101, source_line_num=1),
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=102, source_line_num=2),
        _cand(2, tier=1, source_type="BillLineItem", source_line_item_id=101, source_line_num=1),
        _cand(2, tier=1, source_type="BillLineItem", source_line_item_id=102, source_line_num=2),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    by_id = {r["invoice_line_item_id"]: r for r in out}
    assert by_id[1]["status"] == "linkable"
    assert by_id[1]["proposed"]["source_line_item_id"] == 101
    assert by_id[2]["status"] == "linkable"
    assert by_id[2]["proposed"]["source_line_item_id"] == 102


def test_two_identical_lines_one_shared_source_both_ambiguous():
    lines = [
        _line(1, line_num=1),
        _line(2, line_num=2),
    ]
    candidates = [
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=50, source_line_num=1),
        _cand(2, tier=1, source_type="BillLineItem", source_line_item_id=50, source_line_num=1),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    assert out[0]["status"] == "ambiguous"
    assert out[0]["reject_reason"] == "multiple_matches"
    assert out[0]["proposed"] is None
    assert out[1]["status"] == "ambiguous"
    assert out[1]["reject_reason"] == "multiple_matches"
    assert out[1]["proposed"] is None


def test_one_line_two_same_tier_candidates_ambiguous():
    lines = [_line(1)]
    candidates = [
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=10, source_line_num=1),
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=11, source_line_num=2),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    assert out[0]["status"] == "ambiguous"
    assert out[0]["reject_reason"] == "multiple_matches"
    assert out[0]["proposed"] is None


def test_no_match_missing_service_date_diagnosable():
    lines = [_line(1, service_date=None)]
    out = resolve_link_proposals(lines, [], INVOICE_PROJECT)
    assert out[0]["status"] == "no_match"
    assert out[0]["reject_reason"] == "missing_service_date"
    assert out[0]["proposed"] is None


def test_ki35_fallback_only_when_no_staging_match():
    staging = [_cand(1, tier=1, source_type="BillLineItem", source_line_item_id=1, direct_dbo=False)]
    dbo = [_cand(1, tier=1, source_type="BillLineItem", source_line_item_id=2, direct_dbo=True)]
    assert _filter_candidates_ki35(staging + dbo) == staging
    assert _filter_candidates_ki35(dbo) == dbo


def test_ki35_resolve_prefers_staging_over_direct_dbo():
    lines = [_line(1)]
    candidates = [
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=1, direct_dbo=False),
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=99, direct_dbo=True),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    assert out[0]["proposed"]["source_line_item_id"] == 1


def test_already_linked_skip():
    lines = [
        _line(
            1,
            source_type="BillLineItem",
            bill_line_item_id=55,
        )
    ]
    lines[0]["source_project_id"] = INVOICE_PROJECT
    out = resolve_link_proposals(lines, [_cand(1, tier=1, source_type="BillLineItem", source_line_item_id=999)], INVOICE_PROJECT)
    assert out[0]["status"] == "already_linked"
    assert out[0]["proposed"]["source_line_item_id"] == 55


def test_no_match_never_auto_linked():
    lines = [_line(1)]
    out = resolve_link_proposals(lines, [], INVOICE_PROJECT)
    assert out[0]["status"] == "no_match"
    assert out[0]["proposed"] is None


def test_tier_preference_bill_before_purchase():
    lines = [_line(1)]
    candidates = [
        _cand(1, tier=2, source_type="ExpenseLineItem", source_line_item_id=200),
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=100),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    assert out[0]["proposed"]["source_type"] == "BillLineItem"
    assert out[0]["proposed"]["tier"] == 1


def test_manual_derivative_candidate():
    lines = [_line(1, linked_txn_type="ReimburseCharge", manual_derivative=True)]
    out = resolve_link_proposals(lines, [], INVOICE_PROJECT)
    assert out[0]["status"] == "manual_derivative_candidate"


def test_apply_idempotency_at_decision_level():
    """Second apply pass skips already-linked rows without re-invoking link sproc."""
    svc = InvoiceReconciliationService(
        invoice_repo=Mock(),
        invoice_line_item_repo=Mock(),
        invoice_service=Mock(),
    )

    with patch.object(
        InvoiceReconciliationService,
        "propose_links",
        side_effect=[
            {
                "project_id": INVOICE_PROJECT,
                "invoice_public_id": "inv-pid",
                "lines": [
                    {
                        "invoice_line_item_id": 10,
                        "status": "linkable",
                        "proposed": {
                            "source_type": "BillLineItem",
                            "source_line_item_id": 5,
                            "source_project_id": INVOICE_PROJECT,
                            "tier": 1,
                        },
                    }
                ],
            },
            {
                "project_id": INVOICE_PROJECT,
                "invoice_public_id": "inv-pid",
                "lines": [
                    {
                        "invoice_line_item_id": 10,
                        "status": "already_linked",
                        "proposed": {
                            "source_type": "BillLineItem",
                            "source_line_item_id": 5,
                            "source_project_id": INVOICE_PROJECT,
                            "tier": None,
                        },
                    }
                ],
            },
        ],
    ):
        first = svc.apply_links("inv-pid")
        second = svc.apply_links("inv-pid")

    assert first["summary"]["applied_count"] == 1
    assert svc.invoice_line_item_repo.link_invoice_line_item_source.call_count == 1
    assert second["summary"]["applied_count"] == 0
    assert second["skipped"][0]["apply_action"] == "already_linked"


def test_whitespace_description_distinct_sources_never_cross_linked():
    """Descriptions differing only by trailing whitespace must not share a fingerprint group."""
    lines = [
        _line(1, line_num=1, description="Stone Materials"),
        _line(2, line_num=2, description="Stone Materials "),
    ]
    candidates = [
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=201, source_line_num=1),
        _cand(2, tier=1, source_type="BillLineItem", source_line_item_id=202, source_line_num=2),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    by_id = {r["invoice_line_item_id"]: r for r in out}
    for ili_id, own_source in ((1, 201), (2, 202)):
        row = by_id[ili_id]
        other_source = 202 if own_source == 201 else 201
        if row["status"] == "linkable":
            assert row["proposed"]["source_line_item_id"] == own_source
        else:
            assert row["status"] == "ambiguous"
        if row.get("proposed"):
            assert row["proposed"]["source_line_item_id"] != other_source


def test_same_numeric_id_different_source_types_both_linkable():
    """BillLineItem id=5 and ExpenseLineItem id=5 are distinct source keys."""
    lines = [
        _line(1, line_num=1),
        _line(2, line_num=2),
    ]
    candidates = [
        _cand(1, tier=1, source_type="BillLineItem", source_line_item_id=5, source_line_num=1),
        _cand(2, tier=1, source_type="ExpenseLineItem", source_line_item_id=5, source_line_num=1),
    ]
    out = resolve_link_proposals(lines, candidates, INVOICE_PROJECT)
    by_id = {r["invoice_line_item_id"]: r for r in out}
    assert by_id[1]["status"] == "linkable"
    assert by_id[1]["proposed"]["source_type"] == "BillLineItem"
    assert by_id[1]["proposed"]["source_line_item_id"] == 5
    assert by_id[2]["status"] == "linkable"
    assert by_id[2]["proposed"]["source_type"] == "ExpenseLineItem"
    assert by_id[2]["proposed"]["source_line_item_id"] == 5
