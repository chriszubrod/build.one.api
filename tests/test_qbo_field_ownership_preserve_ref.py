"""Pure-logic tests for U-027 — the shared "rule of three" document-number
preserve/upgrade decision lifted into the QBO base layer.

`integrations.intuit.qbo.base.field_ownership.preserve_human_edited_ref` keeps a
locally stored (possibly human-corrected) document number across a QBO re-pull
UNLESS it is empty/None or the exact ``QBO-<qbo_id>`` placeholder (which still
upgrades to a real doc_number). No I/O — this is the same decision U-024 proved
for the purchase→Expense connector, now generalized for Bill / BillCredit /
Invoice / Expense.
"""
import pytest

from integrations.intuit.qbo.base.field_ownership import (
    is_qbo_placeholder_ref,
    preserve_human_edited_ref,
    qbo_ref_or_placeholder,
)


# --- qbo_ref_or_placeholder (the single mint) -------------------------------

def test_mint_returns_doc_number_when_present():
    assert qbo_ref_or_placeholder("5001", "77") == "5001"


@pytest.mark.parametrize("doc_number", [None, ""])
def test_mint_falls_back_to_placeholder(doc_number):
    assert qbo_ref_or_placeholder(doc_number, "77") == "QBO-77"


def test_mint_and_detector_are_inverses():
    # What the minter produces for a doc-numberless record is EXACTLY what the
    # recognizer flags — they share one format source, so they cannot drift.
    minted = qbo_ref_or_placeholder(None, "77")
    assert is_qbo_placeholder_ref(minted, "77") is True
    # A real doc_number the minter passes through is NOT a placeholder.
    assert is_qbo_placeholder_ref(qbo_ref_or_placeholder("5001", "77"), "77") is False


# --- is_qbo_placeholder_ref -------------------------------------------------

def test_placeholder_exact_match_is_true():
    assert is_qbo_placeholder_ref("QBO-77", "77") is True


def test_placeholder_int_qbo_id_formats_identically():
    # qbo_id may arrive as int or str; both format to the same placeholder.
    assert is_qbo_placeholder_ref("QBO-77", 77) is True


@pytest.mark.parametrize(
    "value",
    [
        "INV-9987",     # a real human value
        "QBO-7",        # different id
        "QBO-770",      # superstring of the id
        "QBO-77 ",      # trailing space — not exact
        "qbo-77",       # wrong case
        "",             # empty
        None,           # missing
    ],
)
def test_placeholder_near_misses_are_false(value):
    assert is_qbo_placeholder_ref(value, "77") is False


# --- preserve_human_edited_ref ----------------------------------------------

def test_preserves_manual_value():
    # Stored value differs from both incoming and the placeholder → keep it.
    assert preserve_human_edited_ref("INV-9987", "5001", "77") == "INV-9987"


def test_upgrades_placeholder_to_real_doc_number():
    assert preserve_human_edited_ref("QBO-77", "5001", "77") == "5001"


@pytest.mark.parametrize("stored", [None, ""])
def test_empty_or_none_stored_takes_incoming(stored):
    assert preserve_human_edited_ref(stored, "5001", "77") == "5001"


def test_manual_value_survives_even_when_incoming_is_placeholder():
    # doc_number was None upstream so incoming == "QBO-77"; the manual value must
    # NOT be overwritten by the placeholder.
    assert preserve_human_edited_ref("INV-9987", "QBO-77", "77") == "INV-9987"


def test_placeholder_stays_placeholder_when_incoming_is_placeholder():
    # Both stored and incoming are the placeholder (QBO still has no doc_number):
    # nothing to upgrade to yet, returns the (identical) incoming placeholder.
    assert preserve_human_edited_ref("QBO-77", "QBO-77", "77") == "QBO-77"


def test_int_qbo_id_upgrades_placeholder():
    # Same behavior when qbo_id is an int.
    assert preserve_human_edited_ref("QBO-77", "5001", 77) == "5001"
