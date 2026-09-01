"""U-225 guard: the U-225 FK-gap migration declares all 22 expected NO-ACTION
foreign keys.

U-353: qbo.VendorCreditBillCredit was retired (table + connector SQL file both
removed), so this file's original guard -- exactly one CREATE TABLE
[qbo].[VendorCreditBillCredit] body across the repo -- no longer applies; there
is zero such bodies now, by design. That guard (and the connector-file
existence check it depended on) was removed rather than updated to assert
"zero"; the FK-gap migration guard below is unrelated to VendorCreditBillCredit
(it covers the other 11 mapping tables' FK constraints) and stays as-is."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# U-225 Part 2: the FK-gap migration must declare all 22 constraints (11 tables x 2).
# ---------------------------------------------------------------------------

FK_GAP_MIGRATION = REPO_ROOT / "scripts/migrations/u225_qbo_mapping_fk_gaps.sql"

EXPECTED_FK_NAMES = [
    "FK_BillBill_QboBill", "FK_BillBill_Bill",
    "FK_BillLineItemBillLine_QboBillLine", "FK_BillLineItemBillLine_BillLineItem",
    "FK_InvoiceInvoice_QboInvoice", "FK_InvoiceInvoice_Invoice",
    "FK_InvoiceLineItemInvoiceLine_QboInvoiceLine", "FK_InvoiceLineItemInvoiceLine_InvoiceLineItem",
    "FK_CustomerCustomer_QboCustomer", "FK_CustomerCustomer_Customer",
    "FK_CustomerProject_QboCustomer", "FK_CustomerProject_Project",
    "FK_VendorVendor_QboVendor", "FK_VendorVendor_Vendor",
    "FK_ItemCostCode_QboItem", "FK_ItemCostCode_CostCode",
    "FK_ItemSubCostCode_QboItem", "FK_ItemSubCostCode_SubCostCode",
    "FK_TermPaymentTerm_QboTerm", "FK_TermPaymentTerm_PaymentTerm",
    "FK_AttachableAttachment_QboAttachable", "FK_AttachableAttachment_Attachment",
]

_ADD_CONSTRAINT_RE = re.compile(
    r"ADD\s+CONSTRAINT\s+\[(?P<name>\w+)\]\s+FOREIGN\s+KEY[^;]*?ON\s+DELETE\s+NO\s+ACTION",
    re.IGNORECASE | re.DOTALL,
)


def test_fk_gap_migration_exists() -> None:
    assert FK_GAP_MIGRATION.is_file(), f"missing {FK_GAP_MIGRATION}"


def test_fk_gap_migration_declares_all_22_no_action_constraints() -> None:
    text = FK_GAP_MIGRATION.read_text(encoding="utf-8")
    found = {m.group("name") for m in _ADD_CONSTRAINT_RE.finditer(text)}
    missing = [name for name in EXPECTED_FK_NAMES if name not in found]
    assert not missing, f"{FK_GAP_MIGRATION.name}: missing expected FK constraints: {missing!r}"
    assert len(EXPECTED_FK_NAMES) == 22, "expected exactly 22 FK constraints (11 tables x 2)"


def test_fk_gap_migration_has_no_cascade() -> None:
    """No table in this migration should get ON DELETE CASCADE -- U-225's Part 2
    decision (2026-08-16, Chris confirmed) is NO ACTION on both sides, matching the
    DBA role brief's standing convention and avoiding a silent-cascade trap once
    U-226's app-level mapping cleanup ships."""
    text = FK_GAP_MIGRATION.read_text(encoding="utf-8")
    assert "ON DELETE CASCADE" not in text.upper()


def test_fk_gap_migration_guarded_and_nocheck() -> None:
    text = FK_GAP_MIGRATION.read_text(encoding="utf-8")
    add_count = len(re.findall(r"ADD\s+CONSTRAINT", text, re.IGNORECASE))
    guard_count = len(re.findall(r"IF\s+NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+sys\.foreign_keys", text, re.IGNORECASE))
    nocheck_count = len(
        re.compile(
            r"ALTER\s+TABLE\s+\[qbo\]\.\[\w+\]\s+WITH\s+NOCHECK",
            re.IGNORECASE,
        ).findall(text)
    )
    assert add_count == 22, f"expected 22 ADD CONSTRAINT statements, found {add_count}"
    assert guard_count == 22, f"expected 22 NOT EXISTS guards, found {guard_count}"
    assert nocheck_count == 22, f"expected 22 WITH NOCHECK clauses, found {nocheck_count}"
