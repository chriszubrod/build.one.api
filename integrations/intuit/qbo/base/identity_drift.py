"""Pure classification logic + shared entity topology for dbo vs qbo staging
identity drift (U-238a)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
