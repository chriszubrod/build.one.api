"""Pure classification logic + shared entity topology for dbo vs qbo staging
identity drift (U-238a headers, U-238b line items, U-238c reference entities)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlatEntitySpec:
    """One row of the flat dbo<->qbo mapping<->qbo staging topology (no line-parent hop).

    Single source of truth for 13 entities across 4 backfill/drift scripts:
    scripts/backfill_qbo_identity_headers.py, scripts/check_qbo_identity_drift_headers.py,
    scripts/backfill_qbo_identity_reference.py, and scripts/check_qbo_identity_drift_reference.py
    (U-238a transaction headers + U-238c reference entities). `dbo_table` was dropped as a
    field: it was always identical to `label`, so callers use `.label` directly.
    """

    key: str
    label: str
    mapping_table: str
    staging_table: str
    dbo_fk_col: str
    staging_fk_col: str
    has_sync_token: bool
    sproc: str


HEADER_ENTITY_SPECS: tuple[FlatEntitySpec, ...] = (
    FlatEntitySpec("bill", "Bill", "BillBill", "Bill", "BillId", "QboBillId", True, "SetBillQboIdentity"),
    FlatEntitySpec("expense", "Expense", "PurchaseExpense", "Purchase", "ExpenseId", "QboPurchaseId", True, "SetExpenseQboIdentity"),
    FlatEntitySpec("invoice", "Invoice", "InvoiceInvoice", "Invoice", "InvoiceId", "QboInvoiceId", True, "SetInvoiceQboIdentity"),
    FlatEntitySpec("project", "Project", "CustomerProject", "Customer", "ProjectId", "QboCustomerId", False, "SetProjectQboIdentity"),
    FlatEntitySpec("company", "Company", "CompanyInfoCompany", "CompanyInfo", "CompanyId", "QboCompanyInfoId", False, "SetCompanyQboIdentity"),
)


REFERENCE_ENTITY_SPECS: tuple[FlatEntitySpec, ...] = (
    FlatEntitySpec("vendor", "Vendor", "VendorVendor", "Vendor", "VendorId", "QboVendorId", False, "SetVendorQboIdentity"),
    FlatEntitySpec("customer", "Customer", "CustomerCustomer", "Customer", "CustomerId", "QboCustomerId", False, "SetCustomerQboIdentity"),
    FlatEntitySpec("cost_code", "CostCode", "ItemCostCode", "Item", "CostCodeId", "QboItemId", False, "SetCostCodeQboIdentity"),
    FlatEntitySpec("sub_cost_code", "SubCostCode", "ItemSubCostCode", "Item", "SubCostCodeId", "QboItemId", False, "SetSubCostCodeQboIdentity"),
    FlatEntitySpec("payment_term", "PaymentTerm", "TermPaymentTerm", "Term", "PaymentTermId", "QboTermId", False, "SetPaymentTermQboIdentity"),
    FlatEntitySpec("address", "Address", "PhysicalAddressAddress", "PhysicalAddress", "AddressId", "QboPhysicalAddressId", False, "SetAddressQboIdentity"),
    FlatEntitySpec("attachment", "Attachment", "AttachableAttachment", "Attachable", "AttachmentId", "QboAttachableId", False, "SetAttachmentQboIdentity"),
    FlatEntitySpec("bill_credit", "BillCredit", "VendorCreditBillCredit", "VendorCredit", "BillCreditId", "QboVendorCreditId", False, "SetBillCreditQboIdentity"),
)


@dataclass(frozen=True)
class LineEntitySpec:
    """One row of the dbo<->qbo mapping<->staging topology for a line-item entity.

    Single source of truth shared by scripts/backfill_qbo_identity_lines.py and
    scripts/check_qbo_identity_drift_lines.py. Line specs carry the dbo-side
    parent FK (per-parent QboId uniqueness) and the staging-header hop needed
    to reach RealmId (line staging tables have no RealmId of their own).
    """

    key: str
    label: str
    mapping_table: str
    staging_table: str
    dbo_fk_col: str
    staging_fk_col: str
    parent_fk_col: str
    staging_header_table: str
    staging_header_fk_col: str
    sproc: str


LINE_ENTITY_SPECS: tuple[LineEntitySpec, ...] = (
    LineEntitySpec(
        "bill_line_item", "BillLineItem", "BillLineItemBillLine", "BillLine",
        "BillLineItemId", "QboBillLineId", "BillId", "Bill", "QboBillId",
        "SetBillLineItemQboIdentity",
    ),
    LineEntitySpec(
        "invoice_line_item", "InvoiceLineItem", "InvoiceLineItemInvoiceLine", "InvoiceLine",
        "InvoiceLineItemId", "QboInvoiceLineId", "InvoiceId", "Invoice", "QboInvoiceId",
        "SetInvoiceLineItemQboIdentity",
    ),
    LineEntitySpec(
        "expense_line_item", "ExpenseLineItem", "PurchaseLineExpenseLineItem", "PurchaseLine",
        "ExpenseLineItemId", "QboPurchaseLineId", "ExpenseId", "Purchase", "QboPurchaseId",
        "SetExpenseLineItemQboIdentity",
    ),
    LineEntitySpec(
        "bill_credit_line_item", "BillCreditLineItem", "VendorCreditLineItemBillCreditLineItem",
        "VendorCreditLine", "BillCreditLineItemId", "QboVendorCreditLineId", "BillCreditId",
        "VendorCredit", "QboVendorCreditId", "SetBillCreditLineItemQboIdentity",
    ),
)


def stamp_line_identity_or_warn(
    repo,
    *,
    id: int,
    qbo_id: Optional[str],
    realm_id: Optional[str],
    context: str,
    enforce_realm_pairing: bool = False,
) -> None:
    """Best-effort dbo line identity stamp (U-238b). By every call site the line
    item and its mapping are already committed, so a stamp failure must never
    abort or roll back otherwise-successful work — log and move on (the row is
    a self-healing pending_backfill candidate for the next pull).

    U-293-dw: QboId and RealmId should be stamped as an atomic pair — a QBO
    line id is only unique within its own parent transaction (not globally),
    so QboId alone is not a complete identity. Two independent layers now
    enforce this; they're deliberately NOT the same mechanism, and callers
    should not assume the Python-layer one below is the only thing standing
    between them and a partial stamp:

    1. THIS function's `enforce_realm_pairing=True` (opt-in, default False)
       skips the write entirely — never calls the repo at all — when qbo_id
       is known but the realm_id THIS CALL passed is falsy. Default is False
       because a Python-side skip here can't distinguish "this row has no
       realm anywhere" from "this row already has one, this call just didn't
       pass a fresh value" — a caller must only opt in once it also supplies
       the existing row's own realm_id as a fallback (so an already
       realm-complete row keeps self-healing its QboId on every touch);
       flipping this on without that fallback would regress the pre-existing
       unconditional every-touch self-heal for that caller's family. Only
       BillLineItemConnector opts in today.
    2. Independently, the underlying Set*LineItemQboIdentity sproc (ALL FOUR
       — bill/invoice/expense/bill_credit line items) now carries its OWN
       atomic-pair guard: it reads the row's existing RealmId itself before
       deciding whether to write QboId, so it protects every caller
       uniformly regardless of this function's `enforce_realm_pairing` flag
       or how the row is reached — including scripts/backfill_qbo_identity_lines.py's
       `_stamp_via_sproc`, which calls the sproc directly, bypassing this
       function entirely. This is the layer that actually prevents a NEW
       partial-stamp row for invoice/expense/bill_credit line items today.

    So "the sibling families keep their pre-existing behavior" below refers
    ONLY to layer 1 (this function still calls the repo unconditionally for
    them, exactly as before U-293-dw) — it does NOT mean they're unprotected
    against partial stamps; layer 2 covers that. Layer 1 is wired for the
    other 3 families in U-293b, once each gets the matching Python-side
    existing-realm-id fallback this needs to be safe to enable.
    """
    if enforce_realm_pairing and qbo_id is not None and not realm_id:
        logger.warning(
            f"{context} but refusing to stamp dbo identity: qbo_id={qbo_id!r} is known "
            f"but realm_id is missing. Skipping rather than partial-stamping (QboId alone "
            f"is not unique across parents) — leaving as a pending_backfill candidate."
        )
        return
    try:
        repo.set_qbo_identity(id=id, qbo_id=qbo_id, realm_id=realm_id)
    except Exception as stamp_err:
        logger.warning(f"{context} but could not stamp dbo identity: {stamp_err}")


def _norm(value: Optional[str]) -> str:
    return "" if value is None else str(value)


def classify_qbo_identity_drift(
    *,
    dbo_qbo_id: Optional[str],
    dbo_realm_id: Optional[str],
    dbo_sync_token: Optional[str],
    has_mapping: bool,
    staging_qbo_id: Optional[str],
    staging_realm_id: Optional[str],
    staging_sync_token: Optional[str],
    has_sync_token: bool,
) -> str:
    """
    Classify one dbo header row against its qbo mapping+staging identity.

    Returns one of: match | drift | pending_backfill | orphan_dbo_value
    """
    dbo_stamped = dbo_qbo_id is not None

    if dbo_stamped and not has_mapping:
        return "orphan_dbo_value"

    if not dbo_stamped and has_mapping:
        return "pending_backfill"

    if not dbo_stamped and not has_mapping:
        return "match"

    # dbo is stamped and mapping exists — compare to staging values.
    identity_match = (
        _norm(dbo_qbo_id) == _norm(staging_qbo_id)
        and _norm(dbo_realm_id) == _norm(staging_realm_id)
    )
    if has_sync_token:
        identity_match = identity_match and _norm(dbo_sync_token) == _norm(staging_sync_token)

    return "match" if identity_match else "drift"
