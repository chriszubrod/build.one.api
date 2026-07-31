"""U-178 — invoice Phase-1 draw audit pure logic."""

from decimal import Decimal

from entities.invoice.business.audit import (
    assemble_audit_report,
    compute_coverage_gaps,
    detect_double_bill_pairs,
)


def _dbl_line(
    ili_id: int,
    *,
    amount="100.00",
    service_date="2026-07-01",
    source_type="BillLineItem",
    source_line_item_id=1,
    vendor_id=10,
):
    return {
        "invoice_line_item_id": ili_id,
        "amount": Decimal(str(amount)),
        "service_date": service_date,
        "source_type": source_type,
        "source_line_item_id": source_line_item_id,
        "vendor_id": vendor_id,
    }


def test_detect_double_bill_pairs_flags_cross_type_same_amount_overlap():
    lines = [
        _dbl_line(1, source_type="BillLineItem", source_line_item_id=1, vendor_id=10),
        _dbl_line(
            2,
            source_type="ExpenseLineItem",
            source_line_item_id=2,
            vendor_id=10,
        ),
    ]
    pairs = detect_double_bill_pairs(lines)
    assert len(pairs) == 1
    assert pairs[0]["line_a"]["invoice_line_item_id"] == 1
    assert pairs[0]["line_b"]["invoice_line_item_id"] == 2


def test_detect_double_bill_pairs_flags_different_vendor_same_amount():
    lines = [
        _dbl_line(1, source_type="BillLineItem", source_line_item_id=1, vendor_id=10),
        _dbl_line(2, source_type="BillLineItem", source_line_item_id=2, vendor_id=99),
    ]
    pairs = detect_double_bill_pairs(lines)
    assert len(pairs) == 1


def test_detect_double_bill_pairs_different_amount_not_flagged():
    lines = [
        _dbl_line(1, amount="100.00"),
        _dbl_line(2, amount="100.02", source_line_item_id=2),
    ]
    assert detect_double_bill_pairs(lines) == []


def test_detect_double_bill_pairs_same_source_not_flagged():
    lines = [
        _dbl_line(1, source_line_item_id=5, vendor_id=10),
        _dbl_line(2, source_line_item_id=5, vendor_id=10),
    ]
    assert detect_double_bill_pairs(lines) == []


def test_detect_double_bill_pairs_decimal_tolerance():
    lines = [
        _dbl_line(1, amount="100.00"),
        _dbl_line(2, amount="100.009", source_line_item_id=2, vendor_id=99),
    ]
    assert len(detect_double_bill_pairs(lines)) == 1


def test_detect_double_bill_pairs_adjacent_dates():
    lines = [
        _dbl_line(1, service_date="2026-07-01", vendor_id=10),
        _dbl_line(
            2,
            service_date="2026-07-02",
            source_line_item_id=2,
            vendor_id=99,
        ),
    ]
    assert len(detect_double_bill_pairs(lines)) == 1


def test_assemble_audit_report_clear_when_all_green():
    report = assemble_audit_report(
        {
            "qbo_mapping_present": True,
            "duplicate_projects": [],
            "staging_stale": [],
            "link_lines": [{"invoice_line_item_id": 1, "status": "already_linked"}],
            "coverage_gaps": [],
            "double_bill_pairs": [],
            "lines_detail": [],
        }
    )
    assert report["verdict"] == "clear"
    assert report["gaps"] == []


def test_assemble_audit_report_halt_missing_qbo_mapping():
    report = assemble_audit_report({"qbo_mapping_present": False})
    assert report["verdict"] == "halt"
    assert any(g["class"] == "missing_qbo_mapping" for g in report["gaps"])


def test_assemble_audit_report_halt_duplicate_project():
    report = assemble_audit_report(
        {
            "qbo_mapping_present": True,
            "duplicate_projects": [{"id": 99, "name": "HP", "qbo_mappings": 1}],
        }
    )
    assert report["verdict"] == "halt"
    assert any(g["class"] == "duplicate_project" for g in report["gaps"])


def test_assemble_audit_report_halt_stale_staging():
    report = assemble_audit_report(
        {
            "qbo_mapping_present": True,
            "staging_stale": [{"entity": "bill", "last_sync_datetime": "2020-01-01 00:00:00"}],
        }
    )
    assert report["verdict"] == "halt"
    assert any(g["class"] == "stale_staging" for g in report["gaps"])


def test_assemble_audit_report_halt_bad_link_status():
    report = assemble_audit_report(
        {
            "qbo_mapping_present": True,
            "link_lines": [{"invoice_line_item_id": 1, "status": "no_match"}],
        }
    )
    assert report["verdict"] == "halt"
    assert any(g["class"] == "source_link" for g in report["gaps"])


def test_assemble_audit_report_halt_coverage_gap():
    report = assemble_audit_report(
        {
            "qbo_mapping_present": True,
            "coverage_gaps": [
                {
                    "invoice_line_item_id": 1,
                    "attachment_count": 0,
                    "sub_cost_code_id": None,
                }
            ],
        }
    )
    assert report["verdict"] == "halt"
    assert any(g["class"] == "coverage" for g in report["gaps"])


def _binding(ili_id=1, source_type="BillLineItem", source_line_item_id=10):
    return {
        "invoice_line_item_id": ili_id,
        "source_type": source_type,
        "source_line_item_id": source_line_item_id,
    }


def test_compute_coverage_gaps_zero_attachments():
    binding = _binding()
    gaps = compute_coverage_gaps(
        [binding],
        {("BillLineItem", 10): {"attachment_count": 0, "sub_cost_code_id": 5}},
    )
    assert len(gaps) == 1
    assert gaps[0]["attachment_count"] == 0
    assert gaps[0]["sub_cost_code_id"] == 5


def test_compute_coverage_gaps_missing_sub_cost_code():
    binding = _binding()
    gaps = compute_coverage_gaps(
        [binding],
        {("BillLineItem", 10): {"attachment_count": 2, "sub_cost_code_id": None}},
    )
    assert len(gaps) == 1
    assert gaps[0]["sub_cost_code_id"] is None


def test_compute_coverage_gaps_clear_when_fully_covered():
    binding = _binding()
    assert (
        compute_coverage_gaps(
            [binding],
            {("BillLineItem", 10): {"attachment_count": 1, "sub_cost_code_id": 99}},
        )
        == []
    )


def test_compute_coverage_gaps_missing_from_coverage_map():
    binding = _binding()
    gaps = compute_coverage_gaps([binding], {})
    assert len(gaps) == 1
    assert gaps[0]["attachment_count"] == 0
    assert gaps[0]["sub_cost_code_id"] is None


def test_assemble_audit_report_halt_double_bill_pair():
    pair = {
        "line_a": {"invoice_line_item_id": 1},
        "line_b": {"invoice_line_item_id": 2},
    }
    report = assemble_audit_report(
        {
            "qbo_mapping_present": True,
            "double_bill_pairs": [pair],
        }
    )
    assert report["verdict"] == "halt"
    assert any(g["class"] == "double_bill_pair" for g in report["gaps"])
