from entities.invoice.business.naming import (
    build_line_pdf_filename,
    build_packet_filename,
    sanitize_for_filename,
)


def test_build_line_pdf_filename_all_parts_present():
    result = build_line_pdf_filename(
        invoice_number="INV-001",
        vendor_name="Acme Corp",
        parent_number="PO-99",
        description="Drywall install",
        scc_number="SCC-42",
        price=1234.5,
        source_date="2026-01-15",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    expected = (
        "INV-001 - Acme Corp - PO-99 - Drywall install - SCC-42 - "
        "$1,234.50 - 2026-01-15.pdf"
    )
    assert result == expected


def test_build_line_pdf_filename_omits_empty_middle_parts():
    result = build_line_pdf_filename(
        invoice_number="INV-002",
        vendor_name=None,
        parent_number="",
        description="Only desc",
        scc_number="SCC-1",
        price=100,
        source_date="2026-02-01",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    expected = "INV-002 - Only desc - SCC-1 - $100.00 - 2026-02-01.pdf"
    assert result == expected
    assert "  -  -  " not in result


def test_build_line_pdf_filename_sanitizes_reserved_chars():
    result = build_line_pdf_filename(
        invoice_number="INV-003",
        vendor_name="Vendor",
        parent_number="P1",
        description="a/b:c*d",
        scc_number="SCC",
        price=50,
        source_date="2026-03-01",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    assert "a_b_c_d" in result
    assert "a/b:c*d" not in result


def test_build_line_pdf_filename_price_none_omits_dollar_segment():
    result = build_line_pdf_filename(
        invoice_number="INV-004",
        vendor_name="Vendor",
        parent_number="P1",
        description="work",
        scc_number="SCC",
        price=None,
        source_date="2026-04-01",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    expected = "INV-004 - Vendor - P1 - work - SCC - 2026-04-01.pdf"
    assert result == expected
    assert "$" not in result


def test_build_line_pdf_filename_non_numeric_price_omits_dollar_segment():
    result = build_line_pdf_filename(
        invoice_number="INV-005",
        vendor_name="Vendor",
        parent_number="P1",
        description="work",
        scc_number="SCC",
        price="notanumber",
        source_date="2026-05-01",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    expected = "INV-005 - Vendor - P1 - work - SCC - 2026-05-01.pdf"
    assert result == expected
    assert "$" not in result


def test_build_line_pdf_filename_extension_file_extension_wins():
    result = build_line_pdf_filename(
        invoice_number="INV-006",
        vendor_name="V",
        parent_number="P",
        description="D",
        scc_number="S",
        price=1,
        source_date="2026-06-01",
        file_extension="pdf",
        original_filename="scan.tiff",
        content_type="image/png",
    )
    # file_extension must win over BOTH original_filename (.tiff) and content_type (.png).
    assert result.endswith(".pdf")
    assert not result.endswith(".tiff")
    assert not result.endswith(".png")


def test_build_line_pdf_filename_extension_from_original_filename():
    result = build_line_pdf_filename(
        invoice_number="INV-007",
        vendor_name="V",
        parent_number="P",
        description="D",
        scc_number="S",
        price=1,
        source_date="2026-06-01",
        file_extension="",
        original_filename="scan.PNG",
        content_type=None,
    )
    assert result.endswith(".PNG")


def test_build_line_pdf_filename_extension_from_content_type():
    result = build_line_pdf_filename(
        invoice_number="INV-008",
        vendor_name="V",
        parent_number="P",
        description="D",
        scc_number="S",
        price=1,
        source_date="2026-06-01",
        file_extension="",
        original_filename="",
        content_type="application/pdf",
    )
    assert result.endswith(".pdf")


def test_build_line_pdf_filename_no_extension_when_all_empty():
    result = build_line_pdf_filename(
        invoice_number="INV-009",
        vendor_name="V",
        parent_number="P",
        description="D",
        scc_number="S",
        price=1,
        source_date="2026-06-01",
        file_extension="",
        original_filename="",
        content_type="",
    )
    assert not result.endswith(".pdf")
    assert "." not in result.split(" - ")[-1]


def test_build_line_pdf_filename_dotted_extension_stays_single_dotted():
    result = build_line_pdf_filename(
        invoice_number="INV-010",
        vendor_name="V",
        parent_number="P",
        description="D",
        scc_number="S",
        price=1,
        source_date="2026-06-01",
        file_extension=".pdf",
        original_filename=None,
        content_type=None,
    )
    assert result.endswith(".pdf")
    assert not result.endswith("..pdf")


def test_build_line_pdf_filename_undotted_extension_gets_leading_dot():
    result = build_line_pdf_filename(
        invoice_number="INV-011",
        vendor_name="V",
        parent_number="P",
        description="D",
        scc_number="S",
        price=1,
        source_date="2026-06-01",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    assert result.endswith(".pdf")


def test_sanitize_for_filename():
    assert sanitize_for_filename("a<b>c") == "a_b_c"


def test_build_packet_filename():
    assert build_packet_filename("2026.01.HP") == "2026.01.HP - Packet.pdf"
