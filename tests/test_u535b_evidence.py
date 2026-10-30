"""U-535b b1: neutral per-PDF evidence over text (synthetic text only). The check never presents a conclusion."""
from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path

import pytest

import entities.attachment_backfill.business.evidence as evidence_mod
from entities.attachment_backfill.business.evidence import Evidence, collect_evidence, invoice_key

VERDICT_WORDS = ("verified", "mismatch", "partial", "unverifiable", "verdict")


def ev(text, number="1001", amount="1234.50", vendor="Acme Lumber Co"):
    total = None if amount is None else Decimal(amount)
    return collect_evidence(text, bill_number=number, total_amount=total, vendor_name=vendor)


# -- shape ------------------------------------------------------------------------------ #


def test_evidence_has_no_verdict_and_no_text():
    e = ev("SECRET-PDF-TEXT Invoice # 1001 Total 1,234.50")
    assert {f.name for f in dataclasses.fields(e)} == {
        "invoice_seen_count", "amount_seen_count", "vendor_seen", "other_invoice_label_count", "statement_like"
    }
    assert "SECRET" not in repr(e)


def test_module_exposes_no_verdict_words():
    source = Path(evidence_mod.__file__).read_text(encoding="utf-8").lower()
    for word in VERDICT_WORDS:
        assert word not in source, word
    assert not hasattr(evidence_mod, "verify_text")


def test_empty_text():
    for text in ("", None, "   \n"):
        assert ev(text) == Evidence(0, 0, False, 0, False)


# -- invoice seen --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text, number",
    [
        ("Date 10/01/2024 Total 1,234.50", "1001"),
        ("Call 615-555-1234 Total 1,234.50", "1234"),
        ("Nashville, TN 37215 Total 1,234.50", "37215"),
        ("Account 0012345678 Total 1,234.50", "12345"),
        ("Account AB12345678 Total 1,234.50", "12345"),
        ("Order 9912345678 Total 1,234.50", "123456"),
        ("PO1001 total 1,234.50", "1001"),
        ("JOB#1001 total 1,234.50", "1001"),
        ("QUOTE-1001 total 1,234.50", "1001"),
        ("ACCOUNT#1001 total 1,234.50", "1001"),
        ("Lot123 total 1,234.50", "123"),
        ("Invoice AB123456 total 1,234.50", "AB12345"),  # no substring containment
        ("REF:AB12CD34X total 1,234.50", "AB12CD34"),
        ("Invoice 1O01 Total 1,234.50", "1001"),  # OCR confusion is simply not seen
        ("Invoice: inv 1042", "INV-1042X"),  # split across tokens, no squashed containment
    ],
)
def test_not_seen(text, number):
    assert ev(text, number=number).invoice_seen_count == 0


@pytest.mark.parametrize(
    "text, number",
    [
        ("Invoice INV-0123 total 1,234.50", "0123"),
        ("Invoice#1001 total 1,234.50", "1001"),
        ("No.1001 total 1,234.50", "1001"),
        ("NBR:1001 total 1,234.50", "1001"),
        ("INVNO1001 total 1,234.50", "1001"),
        ("Invoice # 1001, total 1,234.50", "1001"),
        ("Invoice 37215 Nashville total 1,234.50", "37215"),
        ("no. 0001042", "1042"),
        ("Invoice: INV-1042", "INV-1042"),
        ("1001 total 1,234.50", "1001"),
        ("INV-1001 Total 1,234.50", "1001"),
        ("Invoice # 1001 Total 1,234.50", "INV-1001"),
    ],
)
def test_seen_once(text, number):
    assert ev(text, number=number).invoice_seen_count == 1


def test_invoice_key_shared_normalization():
    assert invoice_key("INV-0012") == invoice_key("12") == "12"
    assert invoice_key("Invoice#1001") == invoice_key("1001")
    assert invoice_key("INV 1001") == "1001"
    assert invoice_key("PO1001") == "PO1001"


def test_counts_every_occurrence():
    assert ev("Invoice # 1001 page 1\nInvoice # 1001 page 2\nTotal 1,234.50").invoice_seen_count == 2
    page = "ACME LUMBER Invoice # 1001 Page {n} of 3\n"
    assert ev(page.format(n=1) + page.format(n=2) + page.format(n=3)).invoice_seen_count == 3


def test_bill_number_with_whitespace_matches_consecutive_tokens():
    assert ev("Invoice AB 1234 total 1,234.50", number="AB 1234").invoice_seen_count == 1
    assert ev("Invoice AB1234 total 1,234.50", number="AB 1234").invoice_seen_count == 1
    assert ev("Invoice AB 9999 1234", number="AB 1234").invoice_seen_count == 0
    # A whitespace label prefix is still a prefix: "INV 1001" is the number 1001, counted once.
    assert ev("Invoice # 1001 total", number="INV 1001").invoice_seen_count == 1


def test_short_or_missing_bill_number_is_not_checkable():
    assert ev("Invoice 1 Total 1,234.50", number="1").invoice_seen_count is None
    assert ev("Invoice 12 Total 1,234.50", number="12").invoice_seen_count is None
    assert ev("anything 1042", number=None).invoice_seen_count is None
    assert ev("anything", number="--").invoice_seen_count is None


# -- amount seen ---------------------------------------------------------------------------- #


def test_amount_with_and_without_commas_counted():
    e = ev("ACME LUMBER\nInvoice # 1001\nTotal due $1,234.50 Due 1234.50")
    assert e.amount_seen_count == 2 and e.vendor_seen is True


def test_amount_whitespace_collapsed_and_bounded():
    assert ev("inv 1001 total\n 1,234.50 ").amount_seen_count == 1
    assert ev("inv 1001 total 11,234.50").amount_seen_count == 0
    assert ev("inv 1001 total 1,234.505").amount_seen_count == 0


def test_amount_sign_must_agree():
    assert ev("Invoice 1001 credit (1,234.50)").amount_seen_count == 0
    assert ev("Invoice 1001 credit -1,234.50").amount_seen_count == 0
    assert ev("Credit 1001 (1,234.50) and -$1,234.50", amount="-1234.50").amount_seen_count == 2
    assert ev("Credit 1001 1,234.50", amount="-1234.50").amount_seen_count == 0


def test_zero_or_missing_amount_is_not_checkable():
    assert ev("Invoice 1001 total 0.00 balance $0.00", amount="0.00").amount_seen_count is None
    assert ev("Invoice 1001 0.00", amount=None).amount_seen_count is None


# -- vendor ---------------------------------------------------------------------------------- #


def test_vendor_stopwords_do_not_count():
    assert ev("SUPPLY COMPANY", vendor="Best Supply Company").vendor_seen is False
    assert ev("SUPPLY COMPANY", vendor="The Supply Company").vendor_seen is None
    assert ev("BEST SUPPLY", vendor="Bestest Supply Company").vendor_seen is False
    assert ev("BESTEST SUPPLY", vendor="Bestest Supply Company").vendor_seen is True


# -- other invoice labels ---------------------------------------------------------------------- #


def test_inv_no_label_and_ref_are_reported_side_by_side():
    e = ev("Inv. No. 2002\nRef 1001\nTotal 1,234.50")
    assert e.other_invoice_label_count == 1 and e.invoice_seen_count == 1


def test_labelled_other_number_is_counted_and_own_number_is_not():
    assert ev("Invoice # 5873\nPO 1001\nTotal 1,234.50").other_invoice_label_count == 1
    assert ev("Invoice #: INV-1001\nTotal 1,234.50").other_invoice_label_count == 0


def test_label_value_on_the_next_line():
    assert ev("Invoice Number\n2002 10/01/2024\nTotal 1,234.50").other_invoice_label_count == 1
    e = ev("Invoice # Date Terms\n1001 10/01/2024 Net 30\nTotal 1,234.50")
    assert e.other_invoice_label_count == 0 and e.invoice_seen_count == 1


def test_previous_and_original_invoice_labels_ignored():
    text = "Invoice # 1001\nPrevious Invoice # 990 500.00\nPrevious Invoice # 995 600.00\nOriginal Invoice # 980\nTotal 1,234.50"
    e = ev(text)
    assert e.statement_like is False and e.other_invoice_label_count == 0


# -- statement-like flag ------------------------------------------------------------------------ #

SINGLE_INVOICE = """ACME LUMBER COMPANY
123 Main Street Suite 4500
Nashville, TN 37215
Invoice # Date Terms
1001 10/01/2024 Net 30
Qty Item Description Amount
12 2X4-8 Studs 120.00
40 4471 Fasteners 40.00
3 7731 Hangers 30.00
Scope per Statement of Work
Total 1,234.50
"""


def test_multi_line_single_invoice_is_not_statement_like():
    e = ev(SINGLE_INVOICE)
    assert e.statement_like is False and e.other_invoice_label_count == 0
    assert e.invoice_seen_count == 1 and e.amount_seen_count == 1


def test_statement_of_work_boilerplate_alone_is_not_statement_like():
    assert ev("Invoice 1001\nScope per Statement of Work\nLabor 100.00 Material 200.00 Total 1,234.50").statement_like is False


def test_three_distinct_labels_are_statement_like_and_counted_as_other():
    e = ev("Invoice # 1001 1,234.50\nInvoice No. 1002 50.00\nInvoice ID 1003 75.00")
    assert e.statement_like is True and e.other_invoice_label_count == 2


def test_titled_statement_with_labels_is_flagged():
    text = (
        "STATEMENT OF ACCOUNT\n"
        "Invoice # 1001 10/01/2024 1,234.50\n"
        "Invoice # 1002 10/05/2024 50.00\n"
        "Invoice # 1003 10/09/2024 75.00\n"
    )
    e = ev(text)
    assert e.statement_like is True and e.other_invoice_label_count == 2


def test_two_labels_need_a_title_phrase():
    two = "Invoice # 1001 1,234.50\nInvoice # 1002 50.00\n"
    assert ev(two).statement_like is False
    assert ev("STATEMENT\n" + two).statement_like is True
    assert ev("Customer Account Activity\n" + two).statement_like is True
    assert ev("Scope per Statement of Work\n" + two).statement_like is False


def test_table_statement_under_one_header_is_statement_like():
    text = "STATEMENT\nDate Invoice # Amount\n10/01/24 1001 1,234.50\n10/05/24 1002 50.00\n10/09/24 1003 75.00\n"
    assert ev(text).statement_like is True


def test_inv_lines_statement_is_statement_like():
    text = "ACCOUNT STATEMENT\nINV 1001 10/01/2024 1,234.50\nINV 1002 10/05/2024 50.00\nINV 1003 10/09/2024 75.00\n"
    assert ev(text).statement_like is True


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Invoice # AB1234 - Page 1", 1),
        ("Invoice #: AB1234", 1),
        ("AB1234 - x", 1),
        ("# AB1234", 1),
        ("Invoice AB 1234 total", 1),
        ("AB 1234 and again AB1234", 2),
    ],
)
def test_spaced_bill_number_counts_each_printed_number_once(text, expected):
    ev = collect_evidence(text, bill_number="AB 1234", total_amount=None, vendor_name=None)
    assert ev.invoice_seen_count == expected
