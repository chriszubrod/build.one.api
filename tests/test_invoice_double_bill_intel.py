"""U-187 — invoice double-bill-by-invoice-number (KI-41) + foreign-project
page-leak (KI-40) pure logic."""

from entities.invoice.business.audit import (
    assemble_audit_report,
    detect_double_bill_by_invoice_number,
    find_foreign_project_markers,
)


def _num_line(
    ili_id,
    *,
    source_type="BillLineItem",
    source_line_item_id=1,
    vendor_id=10,
    vendor_invoice_number="INV-100",
):
    return {
        "invoice_line_item_id": ili_id,
        "source_type": source_type,
        "source_line_item_id": source_line_item_id,
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
