"""U-535b b2: live snippets agree with the stored evidence counts (pure, no I/O)."""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from entities.attachment_backfill.business.evidence import collect_evidence
from entities.attachment_backfill.business.snippets import (
    KIND_AMOUNT,
    KIND_INVOICE,
    KIND_OTHER_LABEL,
    KIND_VENDOR,
    MAX_PER_KIND,
    MAX_SNIPPETS,
    collect_snippets,
    find_hits,
)


def _assert_agree(text, bill_number, total_amount, vendor_name):
    ev = collect_evidence(text, bill_number=bill_number, total_amount=total_amount, vendor_name=vendor_name)
    hits = find_hits(text, bill_number=bill_number, total_amount=total_amount, vendor_name=vendor_name)
    if ev.invoice_seen_count is None:
        assert hits[KIND_INVOICE] == []
    else:
        assert len(hits[KIND_INVOICE]) == ev.invoice_seen_count
    if ev.amount_seen_count is None:
        assert hits[KIND_AMOUNT] == []
    else:
        assert len(hits[KIND_AMOUNT]) == ev.amount_seen_count
    if ev.vendor_seen is None:
        assert hits[KIND_VENDOR] == []
    else:
        assert bool(hits[KIND_VENDOR]) == ev.vendor_seen
    assert len(hits[KIND_OTHER_LABEL]) == ev.other_invoice_label_count
    return ev, hits


def _snips(text, bill_number="1001", total_amount=Decimal("1234.50"), vendor_name="Acme Lumber"):
    return collect_snippets(text, bill_number=bill_number, total_amount=total_amount, vendor_name=vendor_name)


def test_invoice_token_hits_with_label_prefix():
    text = "ACME LUMBER\nInvoice INV-1001 dated 1/2/2023\nRef 1001 total $1,234.50"
    ev, hits = _assert_agree(text, "1001", Decimal("1234.50"), "Acme Lumber")
    assert ev.invoice_seen_count == 2
    assert [text[h.start:h.end] for h in hits[KIND_INVOICE]] == ["INV-1001", "1001"]


def test_zip_after_state_is_not_an_invoice_hit():
    text = "Ship to 12 Main St Nashville TN 37201 invoice 37201 amount 10.00 more words here to pad"
    ev, hits = _assert_agree(text, "37201", Decimal("10.00"), None)
    assert ev.invoice_seen_count == 1
    assert len(hits[KIND_INVOICE]) == 1 and hits[KIND_INVOICE][0].start == text.index("invoice 37201") + 8


def test_spaced_bill_number_matches_joined_window():
    text = "Invoice AB 1234 is due; see AB1234 also. Amount 55.00 and padding text for length here"
    ev, hits = _assert_agree(text, "AB 1234", Decimal("55.00"), None)
    assert ev.invoice_seen_count == 2
    assert sorted(text[h.start:h.end] for h in hits[KIND_INVOICE]) == ["AB 1234", "AB1234"]


def test_signed_amount_only_same_sign_counts():
    text = "Credit (1,234.50) applied; charge 1,234.50; refund -1234.50; again $ 1234.50 end"
    ev, hits = _assert_agree(text, "X9", Decimal("1234.50"), None)
    assert ev.amount_seen_count == 2
    ev_neg, hits_neg = _assert_agree(text, "X9", Decimal("-1234.50"), None)
    assert ev_neg.amount_seen_count == 2
    assert {h.haystack[h.start:h.end] for h in hits_neg[KIND_AMOUNT]} == {"1,234.50", "1234.50"}


def test_zero_amount_has_no_amount_hits():
    _ev, hits = _assert_agree("0.00 0.00 total 0.00", "1001", Decimal("0"), None)
    assert hits[KIND_AMOUNT] == []


def test_vendor_token_whole_word_only():
    text = "From: Acme-Lumber Co. -- also ACMELUMBERS (not a hit) and acme"
    ev, hits = _assert_agree(text, "1001", None, "Acme Lumber Company")
    assert ev.vendor_seen is True
    assert [h.haystack[h.start:h.end] for h in hits[KIND_VENDOR]] == ["ACME", "LUMBER", "ACME"]


def test_other_label_distinct_values_excluding_bill_number_and_previous():
    text = (
        "INVOICE # 1001\n"
        "Invoice No: 2002 and Invoice # 2002 again\n"
        "Previous Invoice # 3003\n"
        "INVOICE NUMBER\n4004\n"
    )
    ev, hits = _assert_agree(text, "1001", None, None)
    assert ev.other_invoice_label_count == 2
    assert [h.haystack[h.start:h.end] for h in hits[KIND_OTHER_LABEL]] == ["2002", "4004"]


def test_snippet_shape_context_and_original_case():
    text = "x" * 100 + " Acme Lumber invoice 1001 for 1,234.50 " + "y" * 100
    snips = _snips(text)
    assert {s["kind"] for s in snips} == {KIND_INVOICE, KIND_AMOUNT, KIND_VENDOR}
    for s in snips:
        assert set(s) == {"kind", "before", "hit", "after"}
        assert len(s["before"]) <= 60 and len(s["after"]) <= 60
    vendor = [s for s in snips if s["kind"] == KIND_VENDOR]
    assert [s["hit"] for s in vendor] == ["Acme", "Lumber"]  # shown from the original, not upper-cased
    inv = next(s for s in snips if s["kind"] == KIND_INVOICE)
    assert inv["hit"] == "1001" and inv["before"].endswith("invoice ") and inv["after"].startswith(" for")


def test_snippet_context_collapses_whitespace():
    text = "line one\n\n\tAcme   1001\n\n  tail"
    inv = next(s for s in _snips(text) if s["kind"] == KIND_INVOICE)
    assert "\n" not in inv["before"] and "  " not in inv["before"]


def test_caps_per_kind_and_total():
    text = " ".join(["1001 1,234.50 Acme Lumber"] * 30) + "\n" + "\n".join(f"Invoice # {5000 + i}" for i in range(30))
    hits = find_hits(text, bill_number="1001", total_amount=Decimal("1234.50"), vendor_name="Acme Lumber")
    assert all(len(hits[k]) > MAX_PER_KIND for k in hits)
    snips = _snips(text)
    assert len(snips) == MAX_SNIPPETS
    for kind in (KIND_INVOICE, KIND_AMOUNT, KIND_VENDOR, KIND_OTHER_LABEL):
        assert sum(1 for s in snips if s["kind"] == kind) <= MAX_PER_KIND
    assert len(collect_snippets(text, bill_number="1001", total_amount=None, vendor_name=None, max_total=4)) == 4


def test_empty_and_none_text():
    assert _snips("") == [] and _snips(None) == []


_VOCAB = [
    "1001", "INV-1001", "#1001", "AB", "1234", "AB1234", "TN", "37201", "1,234.50", "1234.50", "(1,234.50)", "-1234.50",
    "$1,234.50", "Invoice", "INVOICE", "#", "No.", "NUMBER", ":", "Previous", "Statement", "STATEMENT", "Acme", "Lumber",
    "ACME-LUMBER", "2002", "3003", "12/01/2023", "foo", "bar", "Total", "Due", "\n", "\n", "\t", "  ",
]


@pytest.mark.parametrize("seed", range(200))
def test_randomized_agreement_with_evidence(seed):
    rng = random.Random(seed)
    text = " ".join(rng.choice(_VOCAB) for _ in range(rng.randint(5, 80)))
    bill = rng.choice(["1001", "AB 1234", "INV-1001", "37201", "2002", "12"])
    amount = rng.choice([Decimal("1234.50"), Decimal("-1234.50"), None, Decimal("0")])
    vendor = rng.choice(["Acme Lumber", "Acme Lumber Company", None, "Co"])
    _assert_agree(text, bill, amount, vendor)


def test_hit_whitespace_is_normalized_like_context():
    text = "Invoice AB\n1234 total 5.00 and more padding text here for length"
    inv = [s for s in _snips(text, bill_number="AB 1234", total_amount=None, vendor_name=None) if s["kind"] == KIND_INVOICE]
    assert [s["hit"] for s in inv] == ["AB 1234"]
