"""Pure classification logic + shared entity topology for dbo vs qbo staging
identity drift (U-238a headers, U-238b line items)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HeaderEntitySpec:
    """One row of the dbo<->qbo mapping<->qbo staging topology for a header entity.

    Single source of truth shared by scripts/backfill_qbo_identity_headers.py and
    scripts/check_qbo_identity_drift_headers.py — both scripts describe the same
    5 entities and previously hand-typed two independent, driftable copies of
    this table. `dbo_table` was dropped as a field: it was always identical to
    `label` in both callers, so callers use `.label` directly.
    """

    key: str
    label: str
    mapping_table: str
    staging_table: str
    dbo_fk_col: str
    staging_fk_col: str
    has_sync_token: bool
    sproc: str


HEADER_ENTITY_SPECS: tuple[HeaderEntitySpec, ...] = (
    HeaderEntitySpec("bill", "Bill", "BillBill", "Bill", "BillId", "QboBillId", True, "SetBillQboIdentity"),
    HeaderEntitySpec("expense", "Expense", "PurchaseExpense", "Purchase", "ExpenseId", "QboPurchaseId", True, "SetExpenseQboIdentity"),
    HeaderEntitySpec("invoice", "Invoice", "InvoiceInvoice", "Invoice", "InvoiceId", "QboInvoiceId", True, "SetInvoiceQboIdentity"),
    HeaderEntitySpec("project", "Project", "CustomerProject", "Customer", "ProjectId", "QboCustomerId", False, "SetProjectQboIdentity"),
    HeaderEntitySpec("company", "Company", "CompanyInfoCompany", "CompanyInfo", "CompanyId", "QboCompanyInfoId", False, "SetCompanyQboIdentity"),
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
) -> None:
    """Best-effort dbo line identity stamp (U-238b). By every call site the line
    item and its mapping are already committed, so a stamp failure must never
    abort or roll back otherwise-successful work — log and move on (the row is
    a self-healing pending_backfill candidate for the next pull)."""
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
