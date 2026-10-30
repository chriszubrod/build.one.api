"""U-187 — invoice double-bill-by-invoice-number (KI-41) + foreign-project
page-leak (KI-40) pure logic, plus the U-748 parent-id wiring through the audit."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from entities.invoice.business.audit import (
    InvoiceDrawAuditService,
    assemble_audit_report,
    detect_double_bill_by_invoice_number,
    find_foreign_project_markers,
    find_same_parent_splits,
)


def _num_line(
    ili_id,
    *,
    source_type="BillLineItem",
    source_line_item_id=1,
    vendor_id=10,
    vendor_invoice_number="INV-100",
    source_parent_id=None,
    amount=None,
):
    return {
        "invoice_line_item_id": ili_id,
        "source_type": source_type,
        "source_line_item_id": source_line_item_id,
        "source_parent_id": source_parent_id,
        "amount": amount,
        "vendor_id": vendor_id,
        "vendor_invoice_number": vendor_invoice_number,
    }


# --- detect_double_bill_by_invoice_number (KI-41) -------------------------- #


def test_double_bill_by_number_equal_number_different_source_pairs():
    lines = [
        _num_line(1, source_type="ExpenseLineItem", source_line_item_id=55, vendor_id=380,
                  vendor_invoice_number="9025307153"),
        _num_line(2, source_type="ExpenseLineItem", source_line_item_id=99, vendor_id=1305,
                  vendor_invoice_number="9025307153"),
    ]
    pairs = detect_double_bill_by_invoice_number(lines)
    assert len(pairs) == 1
    assert pairs[0]["vendor_invoice_number"] == "9025307153"
    assert {pairs[0]["line_a"]["invoice_line_item_id"], pairs[0]["line_b"]["invoice_line_item_id"]} == {1, 2}


def test_double_bill_by_number_normalizes_formatting():
    lines = [
        _num_line(1, source_line_item_id=1, vendor_invoice_number="inv 12-34"),
        _num_line(2, source_type="ExpenseLineItem", source_line_item_id=2,
                  vendor_invoice_number="INV1234"),
    ]
    assert len(detect_double_bill_by_invoice_number(lines)) == 1


def test_double_bill_by_number_same_source_not_paired():
    lines = [
        _num_line(1, source_type="BillLineItem", source_line_item_id=7,
                  vendor_invoice_number="ABC-9"),
        _num_line(2, source_type="BillLineItem", source_line_item_id=7,
                  vendor_invoice_number="ABC-9"),
    ]
    assert detect_double_bill_by_invoice_number(lines) == []


def test_double_bill_by_number_blank_ignored():
    lines = [
        _num_line(1, source_line_item_id=1, vendor_invoice_number=""),
        _num_line(2, source_type="ExpenseLineItem", source_line_item_id=2,
                  vendor_invoice_number=None),
    ]
    assert detect_double_bill_by_invoice_number(lines) == []


def test_double_bill_by_number_qbo_placeholder_ignored():
    lines = [
        _num_line(1, source_line_item_id=1, vendor_invoice_number="QBO-4567"),
        _num_line(2, source_type="ExpenseLineItem", source_line_item_id=2,
                  vendor_invoice_number="QBO-4567"),
    ]
    assert detect_double_bill_by_invoice_number(lines) == []


def test_double_bill_by_number_distinct_numbers_not_paired():
    lines = [
        _num_line(1, source_line_item_id=1, vendor_invoice_number="A-1"),
        _num_line(2, source_type="ExpenseLineItem", source_line_item_id=2,
                  vendor_invoice_number="A-2"),
    ]
    assert detect_double_bill_by_invoice_number(lines) == []


# --- U-748: one vendor invoice split across lines of ONE parent ------------ #


def _split_line(ili_id, source_line_item_id, amount, *, source_type="BillLineItem",
                source_parent_id=40885, parent_total="39941.00", parent_line_sum="39941.00",
                vendor_invoice_number="7072"):
    row = _num_line(
        ili_id,
        source_type=source_type,
        source_line_item_id=source_line_item_id,
        source_parent_id=source_parent_id,
        amount=None if amount is None else Decimal(amount),
        vendor_id=413,
        vendor_invoice_number=vendor_invoice_number,
    )
    row["source_parent_total"] = None if parent_total is None else Decimal(parent_total)
    row["source_parent_line_sum"] = None if parent_line_sum is None else Decimal(parent_line_sum)
    return row


def test_same_parent_split_is_not_a_double_bill_but_is_reported():
    # BR-MAIN-31: Harpeth Painting 7072 = one $39,941 Bill coded to two cost codes.
    lines = [
        _split_line(35158, 25548, "9657.00"),
        _split_line(35159, 25443, "30284.00"),
    ]
    assert detect_double_bill_by_invoice_number(lines) == []
    [split] = find_same_parent_splits(lines)
    assert split["vendor_invoice_number"] == "7072"
    assert (split["line_a"]["source_parent_id"], split["line_a"]["amount"]) == (40885, "9657.00")
    assert (split["line_b"]["source_parent_id"], split["line_b"]["amount"]) == (40885, "30284.00")


def test_leftover_original_after_a_qbo_recode_is_reported_not_halted():
    # U-748 Pass 3: a $5,000 line re-coded as $3,000 + $2,000 without deleting the
    # original. QBO's header includes all three, so the parent is self-consistent and
    # nothing halts; the only defence is the reviewer reading same_parent_split_pairs.
    kw = dict(source_parent_id=500, parent_total="10000.00", parent_line_sum="10000.00",
              vendor_invoice_number="1234")
    lines = [
        _split_line(1, 11, "5000.00", **kw),
        _split_line(2, 12, "3000.00", **kw),
        _split_line(3, 13, "2000.00", **kw),
    ]
    assert detect_double_bill_by_invoice_number(lines) == []
    assert len(find_same_parent_splits(lines)) == 3


def test_same_parent_equal_amounts_still_paired():
    # An even split looks exactly like a duplicated line, so it still halts.
    kw = dict(parent_total="40000.00", parent_line_sum="40000.00")
    lines = [
        _split_line(1, 11, "20000.00", **kw),
        _split_line(2, 12, "20000.00", **kw),
    ]
    assert len(detect_double_bill_by_invoice_number(lines)) == 1
    assert find_same_parent_splits(lines) == []


def test_parent_lines_exceeding_its_header_still_paired():
    # KI-17 / KI-50: QBO regenerated a line id and the old local line survived, so the
    # parent's lines sum past its header (Urban Evolutions 33033: $33,221 of lines on a
    # $16,610.50 bill). With the amounts edited apart, only this check catches it.
    kw = dict(source_parent_id=19416, parent_total="16610.50", parent_line_sum="33221.00",
              vendor_invoice_number="33033")
    lines = [
        _split_line(1, 23760, "16610.50", **kw),
        _split_line(2, 24400, "16610.00", **kw),
    ]
    assert len(detect_double_bill_by_invoice_number(lines)) == 1
    assert find_same_parent_splits(lines) == []


@pytest.mark.parametrize(
    "header_gap, exempt",
    [("0.01", True), ("0.02", False)],
)
def test_parent_consistency_uses_the_cent_tolerance(header_gap, exempt):
    kw = dict(parent_total="39941.00", parent_line_sum=str(Decimal("39941.00") + Decimal(header_gap)))
    lines = [
        _split_line(1, 25548, "9657.00", **kw),
        _split_line(2, 25443, "30284.00", **kw),
    ]
    assert (detect_double_bill_by_invoice_number(lines) == []) is exempt


@pytest.mark.parametrize(
    "amount_b, exempt",
    [("100.01", False), ("100.02", True)],
)
def test_amount_difference_uses_the_cent_tolerance(amount_b, exempt):
    kw = dict(parent_total="9999.00", parent_line_sum="9999.00")
    lines = [
        _split_line(1, 11, "100.00", **kw),
        _split_line(2, 12, amount_b, **kw),
    ]
    assert (detect_double_bill_by_invoice_number(lines) == []) is exempt


def test_different_parents_same_number_still_paired():
    lines = [
        _split_line(1, 25548, "9657.00", source_parent_id=40885),
        _split_line(2, 25999, "30284.00", source_parent_id=40999),
    ]
    assert len(detect_double_bill_by_invoice_number(lines)) == 1


def test_bill_and_expense_sharing_a_parent_id_number_still_paired():
    # Parent identity is (source type, id): Bill 500 and Expense 500 are unrelated.
    lines = [
        _split_line(1, 1, "100.00", source_type="BillLineItem", source_parent_id=500),
        _split_line(2, 2, "250.00", source_type="ExpenseLineItem", source_parent_id=500),
    ]
    assert len(detect_double_bill_by_invoice_number(lines)) == 1


@pytest.mark.parametrize(
    "overrides_b",
    [
        {"source_parent_id": None},
        {"amount": None},
        {"parent_total": None},
        {"parent_line_sum": None},
    ],
    ids=["parent", "amount", "parent_total", "parent_line_sum"],
)
def test_anything_unresolved_still_paired(overrides_b):
    lines = [
        _split_line(1, 25548, "9657.00", **{k: v for k, v in overrides_b.items() if k != "amount"}),
        _split_line(2, 25443, None if "amount" in overrides_b else "30284.00",
                    **{k: v for k, v in overrides_b.items() if k != "amount"}),
    ]
    assert len(detect_double_bill_by_invoice_number(lines)) == 1


def _audit_service(repo, **overrides):
    kwargs = dict(
        invoice_service=MagicMock(),
        invoice_repo=repo,
        reconciliation_service=MagicMock(),
        project_service=MagicMock(),
        qbo_customer_repo=MagicMock(),
        sync_repo=MagicMock(),
        box_workbook_repo=MagicMock(),
        box_folder_repo=MagicMock(),
    )
    kwargs.update(overrides)
    return InvoiceDrawAuditService(**kwargs)


_HARPETH_BINDINGS = [
    {"invoice_line_item_id": 35158, "source_type": "BillLineItem", "source_line_item_id": 25548},
    {"invoice_line_item_id": 35159, "source_type": "BillLineItem", "source_line_item_id": 25443},
]
_HARPETH_PARENT = {"parent_id": 40885, "vendor_id": 413,
                   "parent_total": Decimal("39941.00"), "parent_line_sum": Decimal("39941.00")}


def _harpeth_repo(parents):
    repo = MagicMock()
    repo.read_source_parents_for_source_lines.return_value = parents
    repo.read_vendor_invoice_numbers_for_source_lines.return_value = {
        ("BillLineItem", 25548): "7072",
        ("BillLineItem", 25443): "7072",
    }
    repo.read_source_line_coverage.return_value = {
        ("BillLineItem", 25548): {"attachment_count": 1, "sub_cost_code_id": 447},
        ("BillLineItem", 25443): {"attachment_count": 1, "sub_cost_code_id": 267},
    }
    return repo


_HARPETH_LINK_LINES = [
    {"invoice_line_item_id": 35158, "amount": Decimal("9657.00"), "service_date": "2026-10-19",
     "status": "linkable", "proposed": {"source_type": "BillLineItem", "source_line_item_id": 25548}},
    {"invoice_line_item_id": 35159, "amount": Decimal("30284.00"), "service_date": "2026-10-19",
     "status": "linkable", "proposed": {"source_type": "BillLineItem", "source_line_item_id": 25443}},
]


def test_build_double_bill_lines_threads_the_parent_from_repo():
    svc = _audit_service(_harpeth_repo({
        ("BillLineItem", 25548): _HARPETH_PARENT,
        ("BillLineItem", 25443): _HARPETH_PARENT,
    }))
    rows = svc._build_double_bill_lines(_HARPETH_LINK_LINES, _HARPETH_BINDINGS)
    assert [
        (r["source_parent_id"], r["vendor_id"], r["source_parent_total"], r["source_parent_line_sum"])
        for r in rows
    ] == [(40885, 413, Decimal("39941.00"), Decimal("39941.00"))] * 2
    assert detect_double_bill_by_invoice_number(rows) == []


def test_build_double_bill_lines_with_no_parent_row_still_pairs():
    svc = _audit_service(_harpeth_repo({("BillLineItem", 25548): _HARPETH_PARENT}))
    rows = svc._build_double_bill_lines(_HARPETH_LINK_LINES, _HARPETH_BINDINGS)
    assert rows[1]["source_parent_id"] is None
    assert len(detect_double_bill_by_invoice_number(rows)) == 1


def test_audit_reports_a_split_without_halting(monkeypatch):
    invoice = SimpleNamespace(id=1128, public_id="inv-pub", project_id=None,
                              invoice_number="BR-MAIN-31")
    invoice_service = MagicMock()
    invoice_service.read_by_public_id.return_value = invoice
    invoice_service.holding_scc_service.return_value.is_holding_code = lambda scc_id: False
    invoice_service.invoice_line_item_service.read_by_invoice_id.return_value = []
    reconciliation = MagicMock()
    reconciliation.propose_links.return_value = {"lines": _HARPETH_LINK_LINES, "summary": {"linkable": 2}}
    svc = _audit_service(
        _harpeth_repo({("BillLineItem", 25548): _HARPETH_PARENT, ("BillLineItem", 25443): _HARPETH_PARENT}),
        invoice_service=invoice_service,
        reconciliation_service=reconciliation,
    )
    monkeypatch.setattr(svc, "_read_qbo_mapping", lambda project: {"present": True})
    monkeypatch.setattr(svc, "_read_duplicate_projects", lambda project_id: [])
    monkeypatch.setattr(svc, "_read_box_mappings", lambda project_id: {})
    monkeypatch.setattr(svc, "_read_staging_freshness", lambda: {"stale": []})
    monkeypatch.setattr(svc, "_scan_foreign_project_markers", lambda project, bindings: [])

    report = svc.audit("inv-pub")

    assert report["verdict"] == "clear", report["gaps"]
    assert report["double_bill_invoice_number_pairs"] == []
    assert len(report["same_parent_split_pairs"]) == 1


# --- find_foreign_project_markers (KI-40) ---------------------------------- #


def test_foreign_marker_hit_on_multipage():
    hits = find_foreign_project_markers(
        attachment_text="Rogers Group invoice 0051135218 for TB3 project, 917 Tyne Blvd",
        own_project_tokens=["BR-MAIN", "Buffalo Road"],
        foreign_project_tokens=["TB3", "OHR2"],
        pages_count=2,
    )
    assert len(hits) == 1
    assert hits[0]["token"] == "TB3"


def test_foreign_marker_own_token_never_flagged():
    hits = find_foreign_project_markers(
        attachment_text="BR-MAIN work summary for Buffalo Road",
        own_project_tokens=["BR-MAIN", "Buffalo Road"],
        foreign_project_tokens=["BR-MAIN"],  # also listed as own → excluded
        pages_count=3,
    )
    assert hits == []


def test_foreign_marker_single_page_never_flagged():
    hits = find_foreign_project_markers(
        attachment_text="TB3 invoice bundled here",
        own_project_tokens=["BR-MAIN"],
        foreign_project_tokens=["TB3"],
        pages_count=1,
    )
    assert hits == []


def test_foreign_marker_short_tokens_skipped():
    # 2-char tokens are too noisy to substring-match — dropped.
    hits = find_foreign_project_markers(
        attachment_text="hp materials delivered",
        own_project_tokens=[],
        foreign_project_tokens=["HP"],
        pages_count=2,
    )
    assert hits == []


def test_foreign_marker_suppresses_substring_of_own_token():
    # KI-40 false-positive guard: foreign abbrev 'MAIN' is a substring of the
    # invoice's own 'Main Street' / 'BR-MAIN' — it must NOT flag.
    hits = find_foreign_project_markers(
        attachment_text="Invoice for 123 Main Street renovation, phase 2",
        own_project_tokens=["Main Street", "BR-MAIN"],
        foreign_project_tokens=["MAIN"],
        pages_count=2,
    )
    assert hits == []


def test_foreign_marker_word_boundary_no_partial_alnum():
    # 'TB3' must not fire inside 'TB300'.
    hits = find_foreign_project_markers(
        attachment_text="delivered to tb300 warehouse dock",
        own_project_tokens=[],
        foreign_project_tokens=["TB3"],
        pages_count=2,
    )
    assert hits == []


# --- assemble_audit_report wiring ------------------------------------------ #


def test_assemble_flags_double_bill_invoice_number_gap():
    report = assemble_audit_report(
        {
            "qbo_mapping_present": True,
            "double_bill_invoice_number_pairs": [
                {"line_a": {"invoice_line_item_id": 1}, "line_b": {"invoice_line_item_id": 2},
                 "vendor_invoice_number": "9025307153"}
            ],
        }
    )
    assert report["verdict"] == "halt"
    assert any(g["class"] == "double_bill_invoice_number" for g in report["gaps"])


def test_assemble_flags_foreign_project_page_leak_gap():
    report = assemble_audit_report(
        {
            "qbo_mapping_present": True,
            "foreign_project_markers": [
                {"invoice_line_item_id": 1, "attachment_id": 9, "pages_count": 2,
                 "markers": [{"token": "TB3"}]}
            ],
        }
    )
    assert report["verdict"] == "halt"
    assert any(g["class"] == "foreign_project_page_leak" for g in report["gaps"])
