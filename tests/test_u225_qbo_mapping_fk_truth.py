"""U-225 guard: qbo.VendorCreditBillCredit is declared by exactly one CREATE TABLE body,
and the U-225 FK-gap migration declares all 22 expected NO-ACTION foreign keys."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

VENDORCREDIT_BASE_FILE = (
    REPO_ROOT / "integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql"
)
VENDORCREDIT_CONNECTOR_FILE = (
    REPO_ROOT
    / "integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql"
)

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+\[qbo\]\.\[VendorCreditBillCredit\]", re.IGNORECASE
)


def _count_create_table_bodies() -> dict[str, int]:
    counts = {}
    for path in (VENDORCREDIT_BASE_FILE, VENDORCREDIT_CONNECTOR_FILE):
        text = path.read_text(encoding="utf-8")
        counts[str(path.relative_to(REPO_ROOT))] = len(_CREATE_TABLE_RE.findall(text))
    return counts


def test_files_exist() -> None:
    assert VENDORCREDIT_BASE_FILE.is_file(), f"missing {VENDORCREDIT_BASE_FILE}"
    assert VENDORCREDIT_CONNECTOR_FILE.is_file(), f"missing {VENDORCREDIT_CONNECTOR_FILE}"


def test_vendor_credit_bill_credit_has_exactly_one_create_table_body() -> None:
    """U-225: the connector file's divergent (INT, CASCADE) body must stay deleted --
    the base file (qbo.vendorcredit.sql) is the sole home, matching live prod
    (BIGINT ids, NO ACTION on both FKs, re-measured 2026-08-16)."""
    counts = _count_create_table_bodies()
    total = sum(counts.values())
    assert total == 1, (
        f"expected exactly one CREATE TABLE [qbo].[VendorCreditBillCredit] body "
        f"across the repo, found {total}: {counts!r}"
    )
    base_rel = str(VENDORCREDIT_BASE_FILE.relative_to(REPO_ROOT))
    assert counts[base_rel] == 1, (
        f"expected the CREATE TABLE body to live in {base_rel}, found counts={counts!r}"
    )


def test_vendor_credit_bill_credit_base_declares_no_action_both_sides() -> None:
    text = VENDORCREDIT_BASE_FILE.read_text(encoding="utf-8")
    # Locate the CREATE TABLE block and confirm neither FK carries ON DELETE CASCADE.
    match = _CREATE_TABLE_RE.search(text)
    assert match, "CREATE TABLE [qbo].[VendorCreditBillCredit] not found in base file"
    block_end = text.index(");", match.start())
    block = text[match.start():block_end]
    assert "ON DELETE CASCADE" not in block.upper(), (
        "qbo.VendorCreditBillCredit base file must declare NO ACTION (implicit, no "
        "ON DELETE clause) to match live prod -- found an explicit ON DELETE CASCADE"
    )


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
