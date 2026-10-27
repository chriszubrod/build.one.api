"""
Merge duplicate ExpenseLineItem rows left by QBO Purchase line-id regeneration on
58999→item recode (U-738). Keeps the newer (live QBO-bound) row; moves the leftover's
attachments and invoice links onto it; deletes the stale leftover row.

SAFE BY DEFAULT: dry-run unless --apply. Counts-only stdout; per-row audit in
--out CSV (required). One transaction per expense; no Expense.Status changes; no
outbox / workbook enqueue.

Usage:
  PYTHONPATH=. ./.venv/bin/python scripts/backfill_merge_recode_leftover_expense_lines.py \\
      --out /tmp/u738_preview.csv
  PYTHONPATH=. ./.venv/bin/python scripts/backfill_merge_recode_leftover_expense_lines.py \\
      --apply --limit 50 --out /tmp/u738_apply.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence

from integrations.intuit.qbo.base.ids import normalize_qbo_id
from integrations.intuit.qbo.purchase.connector.recode_leftover import (
    compute_kept_billing_fields,
    public_ids_in_workbook_outbox_payload,
    true_recode_skip_reason,
)
from scripts.sync_helper import assert_cli_system_admin
from shared.database import call_procedure, get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_merge_recode_leftover_expense_lines")

WORKBOOK_COL_Z_INDEX = 25

CANDIDATE_EXPENSES_SQL = """
SELECT DISTINCT e.[Id] AS ExpenseId
FROM dbo.[Expense] e
INNER JOIN qbo.[Purchase] p ON p.[QboId] = e.[QboId] AND p.[RealmId] = e.[RealmId]
INNER JOIN dbo.[ExpenseLineItem] eli ON eli.[ExpenseId] = e.[Id]
WHERE e.[QboId] IS NOT NULL AND e.[RealmId] IS NOT NULL
  AND eli.[QboId] IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM qbo.[PurchaseLine] pl
      WHERE pl.[QboPurchaseId] = p.[Id]
        AND pl.[QboLineId] = eli.[QboId]
  )
  AND (? IS NULL OR e.[Id] = ?)
ORDER BY e.[Id]
"""

EXPENSE_CONTEXT_SQL = """
SELECT
    e.[Id] AS ExpenseId,
    e.[RealmId] AS RealmId,
    p.[Id] AS QboPurchaseId
FROM dbo.[Expense] e
INNER JOIN qbo.[Purchase] p ON p.[QboId] = e.[QboId] AND p.[RealmId] = e.[RealmId]
WHERE e.[Id] = ?;
"""

LIVE_QBO_LINE_IDS_SQL = """
SELECT pl.[QboLineId]
FROM qbo.[PurchaseLine] pl
WHERE pl.[QboPurchaseId] = ?;
"""

LOCAL_LINES_SQL = """
SELECT
    eli.[Id],
    eli.[PublicId],
    eli.[RowVersion],
    eli.[QboId],
    eli.[RealmId],
    eli.[SubCostCodeId],
    eli.[ProjectId],
    eli.[Description],
    eli.[Quantity],
    eli.[Rate],
    eli.[Amount],
    eli.[IsBillable],
    eli.[IsBilled],
    eli.[BillableDecidedByUserId],
    eli.[BillableDecidedAt],
    eli.[Markup],
    eli.[Price],
    eli.[IsDraft],
    eli.[CreatedDatetime]
FROM dbo.[ExpenseLineItem] eli
WHERE eli.[ExpenseId] = ?
ORDER BY eli.[Id];
"""

LOCAL_LINES_LOCK_SQL = """
SELECT
    eli.[Id],
    eli.[PublicId],
    eli.[RowVersion],
    eli.[QboId],
    eli.[RealmId],
    eli.[SubCostCodeId],
    eli.[ProjectId],
    eli.[Description],
    eli.[Quantity],
    eli.[Rate],
    eli.[Amount],
    eli.[IsBillable],
    eli.[IsBilled],
    eli.[BillableDecidedByUserId],
    eli.[BillableDecidedAt],
    eli.[Markup],
    eli.[Price],
    eli.[IsDraft],
    eli.[CreatedDatetime]
FROM dbo.[ExpenseLineItem] eli WITH (UPDLOCK, HOLDLOCK)
WHERE eli.[ExpenseId] = ?
ORDER BY eli.[Id];
"""

ATTACHMENT_IDS_SQL = """
SELECT [AttachmentId] FROM dbo.[ExpenseLineItemAttachment] WHERE [ExpenseLineItemId] = ?;
"""

# The leftover's link to an Attachment the kept line ALSO links (QBO re-attached the
# same receipt to the new line) is a pure duplicate: drop it, the Attachment row stays.
DROP_DUPLICATE_ATTACHMENT_LINK_SQL = """
DELETE FROM dbo.[ExpenseLineItemAttachment]
WHERE [ExpenseLineItemId] = ?
  AND [AttachmentId] IN (SELECT [AttachmentId] FROM dbo.[ExpenseLineItemAttachment] WHERE [ExpenseLineItemId] = ?);
"""

INVOICE_REF_COUNT_SQL = """
SELECT COUNT(*) FROM dbo.[InvoiceLineItem] WHERE [ExpenseLineItemId] = ?;
"""

REPOINT_ATTACHMENT_SQL = """
UPDATE dbo.[ExpenseLineItemAttachment]
SET [ExpenseLineItemId] = ?, [ModifiedDatetime] = SYSUTCDATETIME()
WHERE [ExpenseLineItemId] = ?
  AND NOT EXISTS (
      SELECT 1 FROM dbo.[ExpenseLineItemAttachment] x
      WHERE x.[ExpenseLineItemId] = ? AND x.[AttachmentId] = [ExpenseLineItemAttachment].[AttachmentId]
  );
"""

REPOINT_INVOICE_LINE_SQL = """
UPDATE dbo.[InvoiceLineItem]
SET [ExpenseLineItemId] = ?, [ModifiedDatetime] = SYSUTCDATETIME()
WHERE [ExpenseLineItemId] = ?;
"""

DELETE_LEFT_OVER_SQL = """
DELETE FROM dbo.[ExpenseLineItem]
WHERE [Id] = ? AND [RowVersion] = ?;
"""

WORKBOOK_OUTBOX_SQL = """
SELECT [Payload]
FROM ms.[Outbox]
WHERE [Kind] IN ('insert_excel_row', 'append_excel_row')
  AND [Status] IN ('done', 'pending', 'in_progress', 'failed');
"""

HOLDING_SCC_SQL = """
SELECT [Id] FROM dbo.[SubCostCode] WHERE [IsHoldingCode] = 1;
"""


@dataclass(frozen=True)
class LocalLine:
    id: int
    public_id: str
    row_version: bytes
    qbo_id: Optional[str]
    realm_id: Optional[str]
    sub_cost_code_id: Optional[int]
    project_id: Optional[int]
    description: Optional[str]
    quantity: Optional[Decimal]
    rate: Optional[Decimal]
    amount: Optional[Decimal]
    is_billable: Optional[bool]
    is_billed: Optional[bool]
    billable_decided_by_user_id: Optional[int]
    billable_decided_at: Any
    markup: Optional[Decimal]
    price: Optional[Decimal]
    is_draft: bool
    created_datetime: Any
    attachment_count: int = 0
    invoice_ref_count: int = 0
    attachment_ids: tuple = ()


@dataclass(frozen=True)
class MergePlan:
    leftover: LocalLine
    newer: LocalLine
    move_attachments: bool
    move_invoice_refs: bool
    drop_duplicate_attachment_links: bool = False


def _live_id_set(qbo_line_ids: Sequence[str]) -> frozenset:
    return frozenset(
        normalize_qbo_id(x)
        for x in qbo_line_ids
        if x is not None and normalize_qbo_id(x) is not None
    )


def classify_local_lines_for_merge(
    local_lines: Sequence[LocalLine],
    live_qbo_line_ids: Sequence[str],
    *,
    holding_sub_cost_code_ids: frozenset[int],
    workbook_public_ids: frozenset[str],
) -> tuple[Optional[MergePlan], Optional[str], dict[str, bool]]:
    """
    Pure decision: find unambiguous leftover+newer pair or return skip/flag reason.
    """
    workbook_flags = {"newer_in_workbook": False, "leftover_in_workbook": False}
    live_ids = _live_id_set(live_qbo_line_ids)
    if not live_ids:
        return None, "skipped:no_live_qbo_lines", workbook_flags

    leftovers = [
        li
        for li in local_lines
        if normalize_qbo_id(li.qbo_id) is not None
        and normalize_qbo_id(li.qbo_id) not in live_ids
    ]
    live_bound = [li for li in local_lines if normalize_qbo_id(li.qbo_id) in live_ids]
    if not leftovers:
        return None, "skipped:no_leftover", workbook_flags
    if not live_bound:
        return None, "skipped:no_live_bound_line", workbook_flags

    if len(leftovers) != 1:
        return None, "flag_manual:ambiguous_leftover_count", workbook_flags
    leftover = leftovers[0]

    newer_candidates = sorted(
        (li for li in live_bound if li.id > leftover.id),
        key=lambda x: x.id,
    )
    if len(newer_candidates) != 1:
        return None, "flag_manual:ambiguous_newer_count", workbook_flags
    newer = newer_candidates[0]

    workbook_flags["newer_in_workbook"] = newer.public_id.lower() in workbook_public_ids
    workbook_flags["leftover_in_workbook"] = leftover.public_id.lower() in workbook_public_ids
    if workbook_flags["leftover_in_workbook"]:
        return None, "flag_manual:leftover_in_workbook", workbook_flags
    # Box DETAILS rows are rebuilt from dbo at drain (no payload evidence); a CODED,
    # non-holding leftover may already be a Box row keyed by its PublicId -> manual.
    if (
        leftover.sub_cost_code_id is not None
        and leftover.sub_cost_code_id not in holding_sub_cost_code_ids
    ):
        return None, "flag_manual:coded_leftover_may_be_in_box", workbook_flags
    if leftover.project_id is not None and leftover.project_id != newer.project_id:
        return None, "flag_manual:project_mismatch", workbook_flags
    if (
        leftover.billable_decided_at is not None
        and newer.billable_decided_at is not None
        and bool(leftover.is_billable) != bool(newer.is_billable)
    ):
        return None, "flag_manual:conflicting_billable_decision", workbook_flags

    if leftover.invoice_ref_count > 0 and newer.invoice_ref_count > 0:
        return None, "flag_manual:both_invoice_linked", workbook_flags
    duplicate_attachment_links = False
    if leftover.attachment_count > 0 and newer.attachment_count > 0:
        # Same Attachment linked to both (QBO re-attached the receipt to the new line):
        # the leftover's link is a duplicate. Any other attachment -> manual.
        if set(leftover.attachment_ids) and set(leftover.attachment_ids) <= set(newer.attachment_ids):
            duplicate_attachment_links = True
        else:
            return None, "flag_manual:both_have_attachments", workbook_flags

    recode_reason = true_recode_skip_reason(
        leftover,
        newer.amount,
        holding_sub_cost_code_ids=holding_sub_cost_code_ids,
        live_line_count=len(live_ids),
    )
    if recode_reason is not None:
        return None, f"flag_manual:{recode_reason}", workbook_flags

    move_attachments = leftover.attachment_count > 0 and newer.attachment_count == 0
    move_invoice_refs = leftover.invoice_ref_count > 0 and newer.invoice_ref_count == 0

    return (
        MergePlan(
            leftover=leftover,
            newer=newer,
            move_attachments=move_attachments,
            move_invoice_refs=move_invoice_refs,
            drop_duplicate_attachment_links=duplicate_attachment_links,
        ),
        None,
        workbook_flags,
    )


def _row_to_local_line(row, *, attachment_count: int, invoice_ref_count: int, attachment_ids: tuple = ()) -> LocalLine:
    return LocalLine(
        id=int(row.Id),
        public_id=str(row.PublicId),
        row_version=row.RowVersion,
        qbo_id=row.QboId,
        realm_id=row.RealmId,
        sub_cost_code_id=row.SubCostCodeId,
        project_id=row.ProjectId,
        description=row.Description,
        quantity=row.Quantity,
        rate=row.Rate,
        amount=row.Amount,
        is_billable=row.IsBillable,
        is_billed=row.IsBilled,
        billable_decided_by_user_id=getattr(row, "BillableDecidedByUserId", None),
        billable_decided_at=getattr(row, "BillableDecidedAt", None),
        markup=row.Markup,
        price=row.Price,
        is_draft=bool(row.IsDraft),
        created_datetime=row.CreatedDatetime,
        attachment_count=attachment_count,
        invoice_ref_count=invoice_ref_count,
        attachment_ids=attachment_ids,
    )


def _load_expense_lines(cursor, expense_id: int, *, lock: bool = False) -> list[LocalLine]:
    sql = LOCAL_LINES_LOCK_SQL if lock else LOCAL_LINES_SQL
    cursor.execute(sql, expense_id)
    rows = cursor.fetchall()
    out: list[LocalLine] = []
    for row in rows:
        cursor.execute(ATTACHMENT_IDS_SQL, row.Id)
        att_ids = tuple(sorted(int(r[0]) for r in cursor.fetchall()))
        cursor.execute(INVOICE_REF_COUNT_SQL, row.Id)
        inv = int(cursor.fetchval() or 0)
        out.append(
            _row_to_local_line(
                row, attachment_count=len(att_ids), invoice_ref_count=inv, attachment_ids=att_ids
            )
        )
    return out


def _load_workbook_public_ids(cursor) -> frozenset[str]:
    """Column-Z keys ever queued for a SharePoint DETAILS write. FAILS CLOSED: if the
    query fails the run aborts (a missing guard must never let --apply proceed); a
    single unparseable payload is skipped and counted instead of disabling the guard."""
    ids: set[str] = set()
    bad_rows = 0
    cursor.execute(WORKBOOK_OUTBOX_SQL)
    for row in cursor.fetchall():
        try:
            ids.update(
                public_ids_in_workbook_outbox_payload(
                    row.Payload if isinstance(row.Payload, str) else str(row.Payload or ""),
                    col_index=WORKBOOK_COL_Z_INDEX,
                )
            )
        except Exception:
            bad_rows += 1
    if bad_rows:
        logger.warning("Skipped %d unparseable ms.Outbox workbook payload(s)", bad_rows)
    return frozenset(x.lower() for x in ids)


def _load_holding_sub_cost_code_ids(cursor) -> frozenset[int]:
    try:
        cursor.execute(HOLDING_SCC_SQL)
        return frozenset(int(row.Id) for row in cursor.fetchall())
    except Exception:
        return frozenset()


def _apply_merge(cursor, plan: MergePlan, expense_id: int) -> tuple[int, int]:
    """Returns (attachments_moved, invoice_links_moved)."""
    newer = plan.newer
    leftover = plan.leftover
    attachments_moved = 0
    invoice_links_moved = 0

    if plan.drop_duplicate_attachment_links:
        cursor.execute(DROP_DUPLICATE_ATTACHMENT_LINK_SQL, leftover.id, newer.id)
    if plan.move_attachments:
        cursor.execute(REPOINT_ATTACHMENT_SQL, newer.id, leftover.id, newer.id)
        attachments_moved = int(cursor.rowcount or 0)
    if plan.move_invoice_refs:
        cursor.execute(REPOINT_INVOICE_LINE_SQL, newer.id, leftover.id)
        invoice_links_moved = int(cursor.rowcount or 0)

    kept_has_invoice = (
        newer.invoice_ref_count + invoice_links_moved > 0 or leftover.invoice_ref_count > 0
    )
    billing = compute_kept_billing_fields(
        newer, leftover, kept_has_invoice_ref=kept_has_invoice
    )

    update_params: dict[str, Any] = {
        "Id": newer.id,
        "RowVersion": newer.row_version,
        "ExpenseId": expense_id,
        "SubCostCodeId": newer.sub_cost_code_id,
        "ProjectId": newer.project_id,
        "Description": newer.description,
        "Quantity": newer.quantity,
        "Rate": newer.rate,
        "Amount": newer.amount,
        "IsBillable": billing["is_billable"],
        "IsBilled": billing["is_billed"],
        "Markup": newer.markup,
        "Price": newer.price,
        "IsDraft": newer.is_draft,
        "AllowTerminalParent": 1,
    }
    if billing.get("billable_decided_at") is not None:
        update_params["BillableDecidedAt"] = billing["billable_decided_at"]
    if billing.get("billable_decided_by_user_id") is not None:
        update_params["BillableDecidedByUserId"] = billing["billable_decided_by_user_id"]

    call_procedure(cursor, "UpdateExpenseLineItemById", update_params)
    updated = cursor.fetchone()
    if not updated:
        raise RuntimeError(f"UpdateExpenseLineItemById returned no row for kept {newer.id}")

    cursor.execute(DELETE_LEFT_OVER_SQL, leftover.id, leftover.row_version)
    if cursor.rowcount != 1:
        raise RuntimeError(
            f"Delete leftover {leftover.id} rowcount={cursor.rowcount} (row version race?)"
        )
    return attachments_moved, invoice_links_moved


def _audit_fields_for_plan(plan: MergePlan) -> dict[str, Any]:
    kept = plan.newer
    removed = plan.leftover
    kept_has_invoice = (
        kept.invoice_ref_count > 0
        or removed.invoice_ref_count > 0
        or plan.move_invoice_refs
    )
    billing = compute_kept_billing_fields(
        kept, removed, kept_has_invoice_ref=kept_has_invoice
    )
    return {
        "kept_line_id": kept.id,
        "kept_public_id": kept.public_id,
        "removed_line_id": removed.id,
        "removed_public_id": removed.public_id,
        "before_sub_cost_code_id": kept.sub_cost_code_id,
        "after_sub_cost_code_id": kept.sub_cost_code_id,
        "before_project_id": kept.project_id,
        "after_project_id": kept.project_id,
        "before_amount": kept.amount,
        "after_amount": kept.amount,
        "before_is_billable": kept.is_billable,
        "after_is_billable": billing["is_billable"],
        "before_is_billed": kept.is_billed,
        "after_is_billed": billing["is_billed"],
        "removed_sub_cost_code_id": removed.sub_cost_code_id,
        "removed_project_id": removed.project_id,
        "removed_amount": removed.amount,
        "removed_is_billable": removed.is_billable,
        "removed_is_billed": removed.is_billed,
        "removed_billable_decided_at": removed.billable_decided_at,
        "removed_qbo_id": removed.qbo_id,
        "attachments_moved": int(plan.move_attachments and removed.attachment_count > 0),
        "duplicate_attachment_link_dropped": int(plan.drop_duplicate_attachment_links),
        "invoice_links_moved": int(plan.move_invoice_refs and removed.invoice_ref_count > 0),
    }


def _plans_equivalent(a: MergePlan, b: MergePlan) -> bool:
    return (
        a.leftover.id == b.leftover.id
        and a.newer.id == b.newer.id
        and a.move_attachments == b.move_attachments
        and a.move_invoice_refs == b.move_invoice_refs
        and a.drop_duplicate_attachment_links == b.drop_duplicate_attachment_links
        and a.leftover.attachment_ids == b.leftover.attachment_ids
        and a.newer.attachment_ids == b.newer.attachment_ids
        and a.leftover.row_version == b.leftover.row_version
        and a.newer.row_version == b.newer.row_version
    )


def process_expense(
    cursor,
    expense_id: int,
    *,
    apply: bool,
    holding_scc_ids: frozenset[int],
    workbook_public_ids: frozenset[str],
) -> tuple[str, Optional[str], dict[str, Any]]:
    cursor.execute(EXPENSE_CONTEXT_SQL, expense_id)
    ctx = cursor.fetchone()
    if not ctx:
        return "skipped:missing_staged_purchase", None, {}

    cursor.execute(LIVE_QBO_LINE_IDS_SQL, ctx.QboPurchaseId)
    live_ids = [row.QboLineId for row in cursor.fetchall()]
    local_lines = _load_expense_lines(cursor, expense_id)
    plan, reason, workbook_flags = classify_local_lines_for_merge(
        local_lines,
        live_ids,
        holding_sub_cost_code_ids=holding_scc_ids,
        workbook_public_ids=workbook_public_ids,
    )
    audit: dict[str, Any] = {"expense_id": expense_id, **workbook_flags}
    if plan is None:
        audit["outcome"] = reason or "skipped:unknown"
        audit["reason"] = reason or ""
        return audit["outcome"], reason, audit

    audit.update(_audit_fields_for_plan(plan))
    if not apply:
        audit["outcome"] = "would_merge"
        audit["reason"] = ""
        return "would_merge", None, audit

    locked_lines = _load_expense_lines(cursor, expense_id, lock=True)
    plan2, reason2, _ = classify_local_lines_for_merge(
        locked_lines,
        live_ids,
        holding_sub_cost_code_ids=holding_scc_ids,
        workbook_public_ids=workbook_public_ids,
    )
    if plan2 is None or not _plans_equivalent(plan, plan2):
        audit["outcome"] = "skipped:changed"
        audit["reason"] = reason2 or "changed"
        return "skipped:changed", reason2, audit

    att_moved, inv_moved = _apply_merge(cursor, plan2, expense_id)
    audit["attachments_moved"] = att_moved
    audit["invoice_links_moved"] = inv_moved
    audit["outcome"] = "merged"
    audit["reason"] = ""
    return "merged", None, audit


CSV_FIELDNAMES = [
    "expense_id",
    "kept_line_id",
    "kept_public_id",
    "removed_line_id",
    "removed_public_id",
    "before_sub_cost_code_id",
    "after_sub_cost_code_id",
    "before_project_id",
    "after_project_id",
    "before_amount",
    "after_amount",
    "before_is_billable",
    "after_is_billable",
    "before_is_billed",
    "after_is_billed",
    "removed_sub_cost_code_id",
    "removed_project_id",
    "removed_amount",
    "removed_is_billable",
    "removed_is_billed",
    "removed_billable_decided_at",
    "removed_qbo_id",
    "attachments_moved",
    "duplicate_attachment_link_dropped",
    "invoice_links_moved",
    "newer_in_workbook",
    "leftover_in_workbook",
    "outcome",
    "reason",
]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write merges (default dry-run)")
    parser.add_argument("--expense-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None, help="Max merge candidates (pre-filtered)")
    parser.add_argument("--out", required=True, help="CSV path for per-row audit")
    args = parser.parse_args(argv)

    assert_cli_system_admin()

    counts: dict[str, int] = {}

    with get_connection() as conn:
        cursor = conn.cursor()
        holding_scc_ids = _load_holding_sub_cost_code_ids(cursor)
        workbook_public_ids = _load_workbook_public_ids(cursor)

        cursor.execute(CANDIDATE_EXPENSES_SQL, args.expense_id, args.expense_id)
        expense_ids = [int(row.ExpenseId) for row in cursor.fetchall()]
        if args.limit is not None:
            expense_ids = expense_ids[: args.limit]

        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
            writer.writeheader()
            for expense_id in expense_ids:
                outcome = "error:Unknown"
                audit_row: dict[str, Any] = {"expense_id": expense_id}
                try:
                    outcome, _, audit_row = process_expense(
                        cursor,
                        expense_id,
                        apply=args.apply,
                        holding_scc_ids=holding_scc_ids,
                        workbook_public_ids=workbook_public_ids,
                    )
                    if args.apply:
                        if outcome == "merged":
                            conn.commit()
                        else:
                            conn.rollback()
                except Exception as exc:
                    conn.rollback()
                    outcome = f"error:{exc.__class__.__name__}"
                    audit_row = {"expense_id": expense_id, "outcome": outcome, "reason": outcome}
                    logger.exception("expense %s failed", expense_id)
                counts[outcome] = counts.get(outcome, 0) + 1
                audit_row.setdefault("outcome", outcome)
                writer.writerow(audit_row)
                fh.flush()

    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
