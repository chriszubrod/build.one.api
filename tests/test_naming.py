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


def test_build_line_pdf_filename_caps_long_description():
    # OHR2-36 (2026-07-13): multi-sentence contract-labor narratives blew
    # SharePoint's 400-char decoded-URL-path limit. Description clips to 120.
    long_desc = "word " * 70  # 350 chars, clips to 119 ("word " * 24 rstripped)
    result = build_line_pdf_filename(
        invoice_number="OHR2-36",
        vendor_name="CL Co",
        parent_number="P1",
        description=long_desc,
        scc_number="01.001",
        price=1234.5,
        source_date="2026-06-30",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    assert len(result) <= 200 + len(".pdf")
    # clipped description keeps the trailing segments intact (under the hard cap)
    assert result.endswith(" - 01.001 - $1,234.50 - 2026-06-30.pdf")
    # the description segment itself was clipped, not dropped
    assert "word word" in result


def test_build_line_pdf_filename_hard_caps_base_name():
    # Even with every component long, the base name stays under the cap and
    # doesn't end in a dangling separator fragment.
    result = build_line_pdf_filename(
        invoice_number="X" * 60,
        vendor_name="V" * 60,
        parent_number="P" * 60,
        description="D" * 200,
        scc_number="S" * 20,
        price=99,
        source_date="2026-06-30",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    base = result[: -len(".pdf")]
    assert len(base) <= 200
    assert not base.endswith("-")
    assert not base.endswith(" ")
    assert result.endswith(".pdf")


def test_build_line_pdf_filename_short_names_unchanged_by_caps():
    # The cap must not perturb names that were already short (deterministic
    # naming is the Box re-version idempotency anchor).
    result = build_line_pdf_filename(
        invoice_number="INV-012",
        vendor_name="Acme",
        parent_number="P1",
        description="Short description",
        scc_number="SCC-1",
        price=10,
        source_date="2026-06-01",
        file_extension="pdf",
        original_filename=None,
        content_type=None,
    )
    assert result == "INV-012 - Acme - P1 - Short description - SCC-1 - $10.00 - 2026-06-01.pdf"


def test_sanitize_for_filename():
    assert sanitize_for_filename("a<b>c") == "a_b_c"


def test_build_packet_filename():
    assert build_packet_filename("2026.01.HP") == "2026.01.HP - Packet.pdf"
