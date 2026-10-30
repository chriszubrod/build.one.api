"""U-535b b1: Box invoice filename parser (synthetic names only)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from entities.attachment_backfill.business.filename import (
    norm_invoice,
    norm_vendor,
    parse_amount,
    parse_filename,
)


def test_full_seven_part_name():
    p = parse_filename("ABC1 - Acme Lumber - INV-00123 - framing package - 10.01 - $1,234.50 - 3-7-2024.pdf")
    assert p is not None
    assert (p.project, p.vendor, p.invoice) == ("ABC1", "Acme Lumber", "INV-00123")
    assert p.invoice_norm == "INV00123"
    assert p.description == "framing package"
    assert p.cost_code == "10.01"
    assert p.amount == Decimal("1234.50")
    assert p.bill_date == date(2024, 3, 7)
    assert p.pushed is False


def test_without_cost_code():
    p = parse_filename("ABC1 - Acme - 555 - misc - $10.00 - 1-2-2023.pdf")
    assert p is not None and p.cost_code is None and p.amount == Decimal("10.00")


def test_description_with_separator_is_joined():
    p = parse_filename("ABC1 - Acme - 555 - part one - part two - $10.00 - 1-2-2023.pdf")
    assert p is not None and p.description == "part one - part two"


@pytest.mark.parametrize(
    "segment, expected",
    [
        ("$1,234.56", Decimal("1234.56")),
        ("($1,234.56)", Decimal("-1234.56")),
        ("-$45.00", Decimal("-45.00")),
        ("$-45.00", Decimal("-45.00")),
        ("$100", Decimal("100")),
        ("1234.56", None),
        ("($12.00", None),
        ("$1,23.00", None),
    ],
)
def test_amounts(segment, expected):
    assert parse_amount(segment) == expected


def test_credit_name_parses_negative():
    p = parse_filename("ABC1 - Acme - CM-9 - return - ($250.00) - 5-1-2022.pdf")
    assert p is not None and p.amount == Decimal("-250.00")


def test_pushed_suffix_detected_and_stripped():
    p = parse_filename("ABC1 - Acme - 555 - misc - $10.00 - 1-2-2023-abcdef01.pdf")
    assert p is not None and p.pushed is True and p.bill_date == date(2023, 1, 2)


def test_uppercase_hex_is_not_our_push_suffix():
    # sanitize_filename lowercases the identity; an upper-case tail is someone else's name.
    assert parse_filename("ABC1 - Acme - 555 - misc - $10.00 - 1-2-2023-ABCDEF01.pdf") is None


def test_duplicate_suffix_stripped():
    p = parse_filename("ABC1 - Acme - 555 - misc - $10.00 - 1-2-2023 (2).pdf")
    assert p is not None and p.bill_date == date(2023, 1, 2) and p.pushed is False


def test_date_is_optional():
    p = parse_filename("ABC1 - Acme - 555 - misc - $10.00.pdf")
    assert p is not None and p.bill_date is None


@pytest.mark.parametrize(
    "name",
    [
        "ABC1 - Acme - $10.00 - 1-2-2023.pdf",  # only 2 head segments
        "ABC1 - Acme - 555 - misc - 1-2-2023.pdf",  # no amount
        "ABC1 - Acme - 555 - misc - $10.00 - 1-2-2023.xlsx",  # not a pdf
        "random scan.pdf",
        "",
    ],
)
def test_unparseable(name):
    assert parse_filename(name) is None


def test_pdf_extension_case_insensitive():
    assert parse_filename("ABC1 - Acme - 555 - misc - $10.00 - 1-2-2023.PDF") is not None


@pytest.mark.parametrize(
    "raw, expected",
    [("INV-00123", "INV00123"), ("000123", "123"), ("0000", "0"), ("ab-12/c", "AB12C"), ("", ""), ("--", ""), (None, "")],
)
def test_norm_invoice(raw, expected):
    assert norm_invoice(raw) == expected


def test_norm_vendor():
    assert norm_vendor("  Acme, Lumber & Co. ") == "ACME LUMBER CO"


def test_three_head_segments_parse():
    p = parse_filename("ABC1 - Acme - 42 - $5.00 - 1-2-2024.pdf")
    assert p is not None and p.invoice == "42" and p.description == ""
    p2 = parse_filename("ABC1 - Acme - 42 - 10.01 - $5.00 - 1-2-2024.pdf")
    assert p2 is not None and p2.cost_code == "10.01"


def test_parsed_filename_has_no_head_field():
    import dataclasses

    p = parse_filename("ABC1 - Acme - 42 - misc - $5.00 - 1-2-2024.pdf")
    assert "head" not in {f.name for f in dataclasses.fields(p)}
