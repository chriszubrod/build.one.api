"""
Re-drive U-652a expense recode enqueue for review-submitted expenses that never
received a CodingSuggestion row (dry-run by default).

Calls ExpenseService.enqueue_coding_recode_on_submit — no duplicate recode/gate/
confirm/outbox logic here.

Usage (from repo root):
  ./.venv/bin/python -m scripts.redrive_skipped_expense_recodes
  ./.venv/bin/python -m scripts.redrive_skipped_expense_recodes --apply
  ./.venv/bin/python scripts/redrive_skipped_expense_recodes.py --expense-id 55 --apply
  ./.venv/bin/python scripts/redrive_skipped_expense_recodes.py --resume-expense-id 55 --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from entities.expense.business.service import ExpenseService
from integrations.intuit.qbo.base.client import recode_write_gate_reason
from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref
from scripts.sync_helper import assert_cli_system_admin
from shared.authz.context import system_authz
from shared.database import get_connection

# Matches ClaimCodingSuggestion @ReclaimAfterSeconds default (dbo.coding_suggestion.sql).
_CLAIM_LEASE_SECONDS = 900

_ELIGIBLE_EXPENSE_STATUSES = (
    "N'submitted'",
    "N'in_review'",
    "N'approved'",
    "N'completed'",
)
_STATUS_FILTER = f"AND e.[Status] IN ({', '.join(_ELIGIBLE_EXPENSE_STATUSES)})\n"

_CANDIDATE_SELECT_BODY = """
    e.[Id],
    e.[Status],
    e.[QboId],
    e.[RealmId],
    (
        SELECT COUNT(*)
        FROM qbo.[Purchase] p2
        INNER JOIN qbo.[PurchaseLine] pl2
            ON pl2.[QboPurchaseId] = p2.[Id]
        WHERE p2.[QboId] = e.[QboId]
          AND p2.[RealmId] = e.[RealmId]
          AND pl2.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
          AND pl2.[ItemRefValue] IS NULL
    ) AS [UncategorizedLineCount],
    N'{source_arm}' AS [SourceArm]
FROM dbo.[Expense] e
WHERE EXISTS (
    SELECT 1
    FROM dbo.[Review] r
    WHERE r.[ExpenseId] = e.[Id]
)
""" + _STATUS_FILTER + """{arm_specific_predicate}
AND EXISTS (
    SELECT 1
    FROM qbo.[Purchase] p
    INNER JOIN qbo.[PurchaseLine] pl
        ON pl.[QboPurchaseId] = p.[Id]
    WHERE p.[QboId] = e.[QboId]
      AND p.[RealmId] = e.[RealmId]
      AND pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
      AND pl.[ItemRefValue] IS NULL
)
{expense_id_filter}
"""

_ARM_MISSING_PREDICATE = """
AND NOT EXISTS (
    SELECT 1
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[ExpenseId] = e.[Id]
)
AND NOT EXISTS (
    SELECT 1
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[ExpenseId] IS NULL
      AND cs.[QboPurchaseQboId] = e.[QboId]
      AND cs.[RealmId] = e.[RealmId]
)
"""

_ARM_SEEDED_PREDICATE = f"""
AND EXISTS (
    SELECT 1
    FROM dbo.[CodingSuggestion] cs
    WHERE (
        cs.[ExpenseId] = e.[Id]
        OR (
            cs.[ExpenseId] IS NULL
            AND cs.[QboPurchaseQboId] = e.[QboId]
            AND cs.[RealmId] = e.[RealmId]
        )
    )
      AND cs.[Status] = N'pending'
      AND cs.[ConfirmedAt] IS NULL
      AND (
          cs.[ClaimedByUserId] IS NULL
          OR cs.[ClaimedAt] < DATEADD(SECOND, -{_CLAIM_LEASE_SECONDS}, SYSUTCDATETIME())
      )
      AND NOT EXISTS (
          SELECT 1
          FROM qbo.[Outbox] o
          WHERE o.[EntityPublicId] = cs.[PublicId]
            AND o.[Kind] = N'recode_purchase_line'
      )
)
"""

CANDIDATE_SQL = (
    "SELECT\n"
    + _CANDIDATE_SELECT_BODY.format(
        source_arm="missing",
        arm_specific_predicate=_ARM_MISSING_PREDICATE,
        expense_id_filter="",
    )
    + "\n{expense_id_filter_outer}\n"
    + "ORDER BY [Id]\n"
)

SEEDED_CANDIDATE_SQL = (
    "SELECT\n"
    + _CANDIDATE_SELECT_BODY.format(
        source_arm="seeded-not-enqueued",
        arm_specific_predicate=_ARM_SEEDED_PREDICATE,
        expense_id_filter="",
    )
    + "\n{expense_id_filter_outer}\n"
    + "ORDER BY [Id]\n"
)


def _expense_id_filter_outer(expense_ids: list[int]) -> tuple[str, list[int]]:
    placeholders = ", ".join("?" for _ in expense_ids)
    # The candidate SELECT already has a top-level WHERE, so the id filter is one more conjunct.
    return f"AND e.[Id] IN ({placeholders})", list(expense_ids)


def _execute_candidate_query(sql_template: str, *, expense_ids: Optional[list[int]] = None) -> list[Any]:
    expense_id_filter_outer = ""
    params: list[Any] = []
    if expense_ids:
        expense_id_filter_outer, params = _expense_id_filter_outer(expense_ids)

    sql = sql_template.format(expense_id_filter_outer=expense_id_filter_outer)
    with get_connection() as conn:
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        return list(cursor.fetchall())


def fetch_candidates(
    *,
    expense_ids: Optional[list[int]] = None,
    resume_expense_ids: Optional[list[int]] = None,
) -> list[Any]:
    """Read-only candidate list (parameterized)."""
    rows = _execute_candidate_query(CANDIDATE_SQL, expense_ids=expense_ids)
    if resume_expense_ids:
        seeded = _execute_candidate_query(
            SEEDED_CANDIDATE_SQL,
            expense_ids=list(resume_expense_ids),
        )
        rows.extend(seeded)
        rows.sort(key=lambda r: int(r.Id))
    return rows


def _predict_outcome(
    *,
    gate_reason: Optional[str],
    dominant: Optional[dict],
    realm_id: Optional[str],
) -> str:
    if gate_reason is not None:
        return "would skip: gates off"
    if dominant is None:
        return "would skip: line_mapping_missing"
    project_id = dominant.get("project_id")
    sub_cost_code_id = dominant.get("sub_cost_code_id")
    if not project_id or not sub_cost_code_id:
        return "would skip: missing project or cost code"
    item_ref = resolve_qbo_item_ref(sub_cost_code_id, realm_id=realm_id)
    if item_ref is None:
        return "would skip: no QBO Item mapping for the cost code"
    return "would enqueue"


def _dominant_has_project_and_cost_code(dominant: Optional[dict]) -> bool:
    if not dominant:
        return False
    return bool(dominant.get("project_id") and dominant.get("sub_cost_code_id"))


def _format_item_reasons(items: list[dict]) -> str:
    parts: list[str] = []
    for item in items:
        bits = []
        for key in ("outcome", "reason", "status"):
            val = item.get(key)
            if val is not None:
                bits.append(f"{key}={val}")
        if bits:
            parts.append("{" + ", ".join(bits) + "}")
    return "; ".join(parts) if parts else "(none)"


def _print_summary_line(summary: dict) -> None:
    print(
        "  enqueued={enqueued} skipped={skipped} invalid={invalid} "
        "writes_disabled={writes_disabled} items={items}".format(
            enqueued=summary.get("enqueued", 0),
            skipped=summary.get("skipped", 0),
            invalid=summary.get("invalid", 0),
            writes_disabled=summary.get("writes_disabled", 0),
            items=_format_item_reasons(summary.get("items") or []),
        )
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Re-drive enqueue_coding_recode_on_submit for review-submitted expenses "
            "missing CodingSuggestion (dry-run by default)."
        )
    )
    ap.add_argument(
        "--expense-id",
        type=int,
        action="append",
        dest="expense_ids",
        metavar="N",
        help="Restrict to these expense ids (must still match candidate criteria).",
    )
    ap.add_argument(
        "--resume-expense-id",
        type=int,
        action="append",
        dest="resume_expense_ids",
        metavar="N",
        help=(
            "Include seeded-not-enqueued recovery for these expense ids only "
            "(pending unconfirmed CodingSuggestion, no recode outbox row, unclaimed)."
        ),
    )
    ap.add_argument("--limit", type=int, default=None, help="Process at most N candidates.")
    ap.add_argument(
        "--as-user-id",
        type=int,
        default=17,
        help="User id passed as created_by when seeding CodingSuggestion (default: 17).",
    )
    ap.add_argument("--apply", action="store_true", help="Enqueue recodes (default: dry-run).")
    args = ap.parse_args(argv)

    gate_reason = recode_write_gate_reason()
    gates_on = gate_reason is None

    resume_ids = args.resume_expense_ids or []
    candidates = fetch_candidates(
        expense_ids=args.expense_ids,
        resume_expense_ids=resume_ids or None,
    )
    if resume_ids:
        seeded_matched = {
            int(r.Id)
            for r in candidates
            if getattr(r, "SourceArm", None) == "seeded-not-enqueued"
        }
        for rid in resume_ids:
            if rid not in seeded_matched:
                print(f"  expense_id={rid} not resumable (skipped)")

    if args.limit is not None:
        candidates = candidates[: args.limit]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: redrive skipped expense recodes ===")
    print(f"candidates: {len(candidates)}")
    print(f"recode writes enabled: {'yes' if gates_on else 'no'}")
    if gate_reason:
        print(f"  gate reason: {gate_reason}")

    if args.apply and not gates_on:
        print("Refusing --apply while recode write gates are off.")
        return 2

    expense_svc = ExpenseService()
    failures = 0
    apply_enqueued = 0
    apply_skipped = 0

    for row in candidates:
        expense_id = int(row.Id)
        qbo_purchase_id = row.QboId
        uncategorized = int(row.UncategorizedLineCount)
        source_arm = getattr(row, "SourceArm", "missing")

        if args.apply:
            print(f"\nexpense_id={expense_id} qbo_purchase_id={qbo_purchase_id}")
            if source_arm == "seeded-not-enqueued":
                still = fetch_candidates(
                    expense_ids=[expense_id],
                    resume_expense_ids=[expense_id],
                )
            else:
                still = fetch_candidates(expense_ids=[expense_id])
            if not still:
                apply_skipped += 1
                print("  skipped: no longer a candidate")
                continue
            try:
                with system_authz():
                    summary = expense_svc.enqueue_coding_recode_on_submit(
                        expense_id=expense_id,
                        user_id=args.as_user_id,
                    )
                _print_summary_line(summary)
                apply_enqueued += int(summary.get("enqueued") or 0)
                apply_skipped += int(summary.get("skipped") or 0)
            except Exception as exc:
                failures += 1
                print(f"  error: {type(exc).__name__}")
            continue

        dominant = expense_svc._resolve_dominant_expense_coding_for_submit(
            expense_id=expense_id
        )
        has_coding = _dominant_has_project_and_cost_code(dominant)
        outcome = _predict_outcome(
            gate_reason=gate_reason,
            dominant=dominant,
            realm_id=row.RealmId,
        )
        print(
            f"  id={expense_id} arm={source_arm} status={row.Status} qbo_purchase_id={qbo_purchase_id} "
            f"uncategorized_lines={uncategorized} dominant_project_and_cost_code={has_coding} "
            f"-> {outcome}"
        )

    if args.apply:
        print(
            f"\nfinal: processed={len(candidates)} enqueued={apply_enqueued} "
            f"skipped={apply_skipped} failures={failures}"
        )
        return 1 if failures else 0

    print("DRY RUN: no writes. Re-run with --apply to enqueue recodes.")
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
