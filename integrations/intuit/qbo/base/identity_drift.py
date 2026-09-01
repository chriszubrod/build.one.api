"""Pure classification logic + shared entity topology for dbo vs qbo staging
identity drift (U-238a headers, U-238b line items, U-238c reference entities)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlatEntitySpec:
    """One row of the flat dbo<->qbo mapping<->qbo staging topology (no line-parent hop).

    Single source of truth (currently 7 entities: 4 header + 3 reference — see
    HEADER_ENTITY_SPECS / REFERENCE_ENTITY_SPECS below, row count trimmed over time as
    families go dbo-native, most recently U-325) across 4 backfill/drift scripts:
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
    # U-305: opt-in RBAC gate (the entity's own dbo.UserCanAccess<X> UDF name) for
    # read_qbo_identity_rows_by_realm_id below. Optional + defaulted so every other
    # existing row is unaffected — only entities that opt in (bill, bill_credit
    # as of U-305) get the generic bulk-identity-read function; every other spec's
    # construction and the 4 existing backfill/drift scripts are untouched.
    access_udf: Optional[str] = None


HEADER_ENTITY_SPECS: tuple[FlatEntitySpec, ...] = (
    FlatEntitySpec(
        "bill", "Bill", "BillBill", "Bill", "BillId", "QboBillId", True, "SetBillQboIdentity",
        access_udf="UserCanAccessBill",
    ),
    FlatEntitySpec("expense", "Expense", "PurchaseExpense", "Purchase", "ExpenseId", "QboPurchaseId", True, "SetExpenseQboIdentity"),
    FlatEntitySpec("invoice", "Invoice", "InvoiceInvoice", "Invoice", "InvoiceId", "QboInvoiceId", True, "SetInvoiceQboIdentity"),
    # U-325: the "project" row was removed — this family is now dbo-native only (U-314),
    # and qbo.CustomerProject is staged for DROP. A LEFT JOIN through that table would
    # flag every dbo-stamped row as a false orphan_dbo_value and error outright once
    # the table is dropped.
    #
    # U-350: the "company" row was removed for the identical reason — this family is
    # now dbo-native only (qbo.CompanyInfoCompany retired, the U-349 program's
    # pattern-setter), and a LEFT JOIN through it would flag every dbo-stamped Company
    # as a false orphan_dbo_value and error outright once the table is dropped.
)


REFERENCE_ENTITY_SPECS: tuple[FlatEntitySpec, ...] = (
    # U-325: the "vendor"/"customer"/"cost_code"/"sub_cost_code" rows were removed —
    # these families are now dbo-native only (U-314, U-307d), and qbo.VendorVendor /
    # qbo.CustomerCustomer / qbo.ItemCostCode / qbo.ItemSubCostCode are staged for DROP.
    # A LEFT JOIN through those tables would flag every dbo-stamped row as a false
    # orphan_dbo_value and error outright once the tables are dropped.
    FlatEntitySpec("payment_term", "PaymentTerm", "TermPaymentTerm", "Term", "PaymentTermId", "QboTermId", False, "SetPaymentTermQboIdentity"),
    # U-351: the "address" row was removed for the identical reason — this family is
    # now dbo-native only (qbo.PhysicalAddressAddress retired, the U-349 program's
    # second family), and a LEFT JOIN through it would flag every dbo-stamped Address
    # as a false orphan_dbo_value and error outright once the table is dropped.
    #
    # U-300c-prereq: the "attachment" reference-drift spec row was removed — the attachable
    # push/pull both went dbo-native (U-285/U-300b/U-300c-prereq), so a LEFT JOIN through
    # qbo.AttachableAttachment/qbo.Attachable now flags every dbo-stamped attachment as a
    # false orphan_dbo_value, and the join errors outright once U-300c drops the tables.
    FlatEntitySpec(
        "bill_credit", "BillCredit", "VendorCreditBillCredit", "VendorCredit", "BillCreditId",
        "QboVendorCreditId", False, "SetBillCreditQboIdentity",
        access_udf="UserCanAccessBillCredit",
    ),
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
       unconditional every-touch self-heal for that caller's family. All four
       line connectors (bill/invoice/expense/bill_credit) opt in as of
       U-293b, each with its own matching existing-realm-id fallback.
    2. Independently, the underlying Set*LineItemQboIdentity sproc (ALL FOUR
       — bill/invoice/expense/bill_credit line items) now carries its OWN
       atomic-pair guard: it reads the row's existing RealmId itself before
       deciding whether to write QboId, so it protects every caller
       uniformly regardless of this function's `enforce_realm_pairing` flag
       or how the row is reached — including scripts/backfill_qbo_identity_lines.py's
       `_stamp_via_sproc`, which calls the sproc directly, bypassing this
       function entirely. This is the layer that actually prevents a NEW
       partial-stamp row for invoice/expense/bill_credit line items today.

    Historical note: between U-293-dw and U-293b, the sibling (non-Bill)
    families called the repo unconditionally here (layer 1 not yet wired for
    them) — they were never unprotected against partial stamps in that
    window, since layer 2 already covered every caller. U-293b closed that
    gap by wiring layer 1 for the other 3 families too, each with its own
    existing-realm-id fallback.
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


def _bit(flag: Optional[bool]) -> Optional[int]:
    """SQL Server BIT params take 0/1, not Python bool."""
    if flag is None:
        return None
    return 1 if flag else 0


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


def read_qbo_identity_rows_by_realm_id(
    spec: FlatEntitySpec,
    realm_id: str,
    *,
    actor_user_id: Optional[int] = None,
    actor_is_system_admin: Optional[bool] = None,
) -> list:
    """Registry-driven bulk dbo-native (Id, QboId) identity read for one
    header/reference entity + realm (U-305).

    Generic counterpart to U-301a's Expense-specific `ReadExpenseQboIdsByRealmId`
    sproc: rather than hand-copying a matching SELECT sproc per entity (U-305's
    Decision-1), this executes one parametrized query built from the entity's
    own `FlatEntitySpec` row — `label` for the dbo table, `access_udf` for its
    RBAC gate — so it is reusable by any registry entity that opts in (bill,
    bill_credit as of U-305), not a Bill/BillCredit-specific pair. `label`/
    `access_udf` are internal registry constants, never external input, so the
    f-string interpolation below carries no injection risk — the same pattern
    scripts/check_qbo_identity_drift_{headers,reference}.py already use for
    `dbo.[{spec.label}]`.

    RBAC-scoped exactly like a hand-written sproc would be: the WHERE clause
    calls the entity's own `dbo.UserCanAccess<X>` UDF per row — the same
    mechanism `ReadExpenseQboIdsByRealmId` uses. Requires `spec.access_udf` —
    raises `ValueError` rather than silently returning an unscoped result for
    a spec that hasn't opted in (a bulk identity read must never ship without
    its RBAC gate wired).
    """
    if not spec.access_udf:
        raise ValueError(
            f"{spec.key}: read_qbo_identity_rows_by_realm_id requires "
            f"FlatEntitySpec.access_udf to be set (no RBAC gate configured)"
        )
    from shared.database import get_connection, map_database_error

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT [Id], [QboId]
                FROM dbo.[{spec.label}]
                WHERE [RealmId] = ?
                  AND [QboId] IS NOT NULL
                  AND dbo.{spec.access_udf}(?, ?, [Id]) = 1
                """,
                [realm_id, actor_user_id, _bit(actor_is_system_admin)],
            )
            return [
                SimpleNamespace(id=row.Id, qbo_id=row.QboId)
                for row in cur.fetchall()
            ]
    except Exception as error:
        logger.error(
            "Error during registry-driven bulk QBO identity read (entity=%s realm_id=%s): %s",
            spec.key, realm_id, error,
        )
        raise map_database_error(error)
