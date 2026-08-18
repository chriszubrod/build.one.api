"""
Operator CLI for qbo.ReconciliationIssue triage and lifecycle management (U-246, U-249).

⚠️ SQL-FIRST — the U-249 additions are NOT in prod yet. Apply
   integrations/intuit/qbo/reconciliation/sql/qbo.reconciliation_issue.sql BEFORE
   using `bulk-acknowledge`, `--severity`, `--action` or `--keep-newest-per-group`.
   Params bind BY NAME, so calling the current prod sproc with a param it does not
   declare fails hard (SQL 8145). The U-246 sprocs themselves ARE applied and
   verified byte-identical to prod as of 2026-08-18.

ACKNOWLEDGE vs RESOLVE — these are different verbs, pick deliberately:
  acknowledge = "seen; real; still awaiting human action"   (open -> acknowledged)
  resolve     = "dealt with"                                (open/ack -> resolved)
Real per-entity drift (e.g. qbo_voided) gets ACKNOWLEDGED. Recurring summary rows
that repeat one condition once per reconcile run get thinned with
`bulk-resolve --keep-newest-per-group`, which keeps the newest of each group.

Usage:
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py triage
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py triage --stale-after-days 14
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py acknowledge --id 12345
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py resolve --id 12345
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py bulk-resolve --drift-type orphaned_item_scc_mapping --created-before-days 30
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py bulk-resolve --drift-type pull_delete_reconcile --entity-type Bill --apply

  # U-249 GAP 1 — acknowledge the 46 real qbo_voided rows (26 Bill + 20 Expense).
  # Dry-run first; add --apply only after reading the preview.
  ... bulk-acknowledge --drift-type qbo_voided --entity-type Bill --severity low --action flagged
  ... bulk-acknowledge --drift-type qbo_voided --entity-type Expense --severity low --action flagged

  # U-249 GAP 2 — thin the recurring summary rows, keeping the newest of each group.
  ... bulk-resolve --drift-type qbo_missing_locally --entity-type Bill --severity low --action flagged --keep-newest-per-group
  ... bulk-resolve --drift-type invoice_draw_mismatch --entity-type Invoice --severity medium --action flagged --keep-newest-per-group

⚠️ Do NOT sweep drift-type `watermark_hold_bound_exceeded` (12 known-bogus staging
   fixture rows, severity=critical, action=manual_review). Always scope by
   --drift-type; note EntityType alone is NOT enough because SQL Server's default
   collation is case-insensitive, so `--entity-type Bill` also matches their
   lowercase 'bill'. `--severity`/`--action` give a second independent guard.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from typing import Optional

sys.path.insert(0, ".")
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from shared.database import get_connection

_MAX_BULK_ROWS = 5000


def _utc_cutoff(days: int) -> datetime:
    """Return a naive UTC cutoff datetime matching pyodbc DATETIME2 convention.

    `days == 0` is REFUSED, not merely `days < 0`. A zero cutoff resolves to
    "now", so the sproc predicate `CreatedDatetime < @CreatedBefore` matches the
    entire table — while still satisfying the blast-radius guard, which only
    checks that SOME scoping argument was supplied. That is the
    "a parameter that means match-everything on a destructive operation" hazard:
    the guard reads as satisfied precisely when nothing is actually scoped.
    """
    if days < 1:
        print(
            f"Refusing: --created-before-days must be >= 1 (got {days}). "
            "0 means 'now', which scopes nothing while still satisfying the "
            "blast-radius guard. Use an explicit --created-before-date if you "
            "really intend a cutoff inside today."
        )
        sys.exit(2)
    return datetime.utcnow() - timedelta(days=days)


def _parse_created_before_date(date_str: str) -> datetime:
    """Parse YYYY-MM-DD as naive UTC midnight (pyodbc DATETIME2 convention)."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        print(f"Refusing: --created-before-date must be YYYY-MM-DD (got {date_str!r}).")
        sys.exit(2)


def _validate_max_rows(max_rows: int) -> int:
    if max_rows < 1 or max_rows > _MAX_BULK_ROWS:
        print(
            f"Refusing: --max-rows must be between 1 and {_MAX_BULK_ROWS} (got {max_rows})."
        )
        sys.exit(2)
    return max_rows


def _format_dt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _print_triage_summary(rows: list[dict]) -> None:
    if not rows:
        print("No reconciliation issues found.")
        return
    print(
        f"{'DriftType':<36} {'EntityType':<16} {'Severity':<10} {'Action':<14} "
        f"{'Status':<14} {'RowCount':>8} {'UniqueKeys':>10} {'FirstSeen':<20} {'LastSeen':<20}"
    )
    print("-" * 160)
    for r in rows:
        print(
            f"{r['drift_type']:<36} {r['entity_type']:<16} {r['severity']:<10} "
            f"{r['action']:<14} {r['status']:<14} {r['row_count']:>8} "
            f"{r['unique_key_count']:>10} {_format_dt(r['first_seen']):<20} "
            f"{_format_dt(r['last_seen']):<20}"
        )


def _repeat_analysis_by_drift_type(stale_after_days: int) -> None:
    cutoff = _utc_cutoff(stale_after_days)
    sql = """
    SELECT
        DriftType,
        EntityType,
        QboId,
        COUNT(*) AS RepCount,
        MAX(CreatedDatetime) AS LastSeen
    FROM qbo.ReconciliationIssue
    GROUP BY DriftType, EntityType, QboId
    """

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql)
        all_rows = cur.fetchall()

    by_drift_type: dict[str, list] = {}
    for row in all_rows:
        by_drift_type.setdefault(row.DriftType, []).append(row)

    groups = sorted(
        by_drift_type.items(),
        key=lambda item: sum(r.RepCount for r in item[1]),
        reverse=True,
    )

    print()
    print(f"Repeat-count analysis by DriftType (stale-after={stale_after_days} days):")
    print(
        f"{'DriftType':<36} {'TotalRows':>10} {'UniqueKeys':>11} "
        f"{'AvgReps':>8} {'MaxReps':>8} {'ActiveKeys':>11} {'StaleKeys':>10}"
    )
    print("-" * 100)

    for drift_type, key_rows in groups:
        total_rows = sum(r.RepCount for r in key_rows)
        unique_keys = len(key_rows)
        rep_counts = [r.RepCount for r in key_rows if r.RepCount]
        max_reps = max(rep_counts) if rep_counts else 0
        avg_reps = (total_rows / unique_keys) if unique_keys else 0.0

        active_keys = sum(
            1 for r in key_rows
            if r.LastSeen is not None and r.LastSeen >= cutoff
        )
        stale_keys = unique_keys - active_keys

        print(
            f"{drift_type:<36} {total_rows:>10} {unique_keys:>11} "
            f"{avg_reps:>8.2f} {max_reps:>8} {active_keys:>11} {stale_keys:>10}"
        )


def cmd_triage(args: argparse.Namespace) -> int:
    repo = ReconciliationIssueRepository()
    rows = repo.triage_summary()
    print("Triage summary (grouped by DriftType/EntityType/Severity/Action/Status):")
    _print_triage_summary(rows)
    _repeat_analysis_by_drift_type(args.stale_after_days)
    return 0


def _read_issue_status(issue_id: int) -> Optional[str]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT Status FROM qbo.ReconciliationIssue WHERE Id = ?",
            issue_id,
        )
        row = cur.fetchone()
        return row.Status if row else None


def _no_op_reason(before: Optional[str], after: str, issue_id: int, target: str) -> str:
    if before is None:
        return f"Id={issue_id} not found"
    if before == target:
        return f"Id={issue_id} already {target}"
    if target == "acknowledged" and before == "resolved":
        return f"Id={issue_id} already resolved (cannot acknowledge)"
    return f"Id={issue_id} status unchanged ({before!r} -> {after!r})"


def _cmd_transition(
    args: argparse.Namespace,
    repo_method,
    target_status: str,
    timestamp_attr: str,
) -> int:
    before = _read_issue_status(args.id)
    result = repo_method(args.id)
    if not result:
        print(f"No row returned for Id={args.id} (missing or sproc not applied).")
        return 1
    ts_label = "".join(part.capitalize() for part in timestamp_attr.split("_"))
    print(
        f"Id={result.id} Status: {before!r} -> {result.status!r} "
        f"{ts_label}={getattr(result, timestamp_attr)!r}"
    )
    if before != target_status and result.status == target_status:
        return 0
    print(f"no-op: {_no_op_reason(before, result.status, args.id, target_status)}")
    return 1


def cmd_acknowledge(args: argparse.Namespace) -> int:
    repo = ReconciliationIssueRepository()
    return _cmd_transition(args, repo.acknowledge, "acknowledged", "acknowledged_at")


def cmd_resolve(args: argparse.Namespace) -> int:
    repo = ReconciliationIssueRepository()
    return _cmd_transition(args, repo.resolve, "resolved", "resolved_at")


def _build_bulk_filters(args: argparse.Namespace, verb: str):
    """Shared filter builder for bulk-resolve and bulk-acknowledge.

    @Severity/@Action are NARROWING-only and deliberately do not satisfy the
    at-least-one-filter guard — '--severity low' alone would match every
    low-severity row of every drift type in the table.
    """
    if (
        not args.drift_type
        and not args.entity_type
        and args.created_before_days is None
        and args.created_before_date is None
    ):
        print(
            f"Refusing {verb}: at least one of --drift-type, --entity-type, "
            "--created-before-days, or --created-before-date is required."
        )
        sys.exit(2)

    max_rows = _validate_max_rows(args.max_rows)

    created_before: Optional[datetime] = None
    if args.created_before_date is not None:
        created_before = _parse_created_before_date(args.created_before_date)
    elif args.created_before_days is not None:
        created_before = _utc_cutoff(args.created_before_days)

    bulk_kwargs = {
        "drift_type": args.drift_type,
        "entity_type": args.entity_type,
        "created_before": created_before,
        "realm_id": args.realm_id,
        "severity": args.severity,
        "action": args.action,
        "status": args.status,
        "max_rows": max_rows,
    }
    return bulk_kwargs, max_rows


def _print_bulk_preview(preview_rows: list[dict], total: int, verb: str, max_rows: int) -> None:
    print(f"Matched {total} row(s) (max {verb} batch: {max_rows}):")
    for r in preview_rows:
        print(
            f"  Id={r['id']:>6}  DriftType={r['drift_type']:<32} EntityType={r['entity_type']:<12} "
            f"QboId={r['qbo_id'] or '':<12} Sev={(r.get('severity') or ''):<9} "
            f"Action={(r.get('action') or ''):<13} Created={r['created_datetime']}"
        )
    if total > len(preview_rows):
        print(f"  ... and {total - len(preview_rows)} more")


def cmd_bulk_resolve(args: argparse.Namespace) -> int:
    resolve_kwargs, max_rows = _build_bulk_filters(args, "bulk-resolve")
    resolve_kwargs["keep_newest_per_group"] = args.keep_newest_per_group

    repo = ReconciliationIssueRepository()
    preview_rows = repo.preview_bulk_resolve(**resolve_kwargs)
    total = preview_rows[0]["total_match_count"] if preview_rows else 0

    _print_bulk_preview(preview_rows, total, "resolve", max_rows)

    if args.keep_newest_per_group:
        GROUP_KEY = "(RealmId, DriftType, EntityType, QboId, EntityPublicId, Severity, Action)"
        if not preview_rows:
            # The counters ride on the preview row set, which is built from the
            # post-withholding candidate temp table. So when keep-newest withholds
            # EVERY matched row, that set is empty and both counters read 0 —
            # in exactly the case that proves the operation is a safe no-op.
            # Printing a bare "Matched 0" here invites the operator to widen the
            # filter, which is the opposite of the correct reaction. Say what
            # actually happened instead.
            print(
                "  keep-newest-per-group ON: NOTHING would be resolved — every matched "
                f"row is the newest of its own {GROUP_KEY} group.\n"
                "  This is the expected, SAFE result for per-entity drift (each row "
                "carries its own QboId, so it groups alone). It does NOT mean the "
                "filter matched nothing — do not widen the filter on the strength of "
                "this message."
            )
        else:
            kept = preview_rows[0].get("total_kept_count")
            print(
                f"  keep-newest-per-group ON: withholding {kept if kept is not None else 0} "
                f"row(s) — the newest of each {GROUP_KEY} group."
            )
    print()

    if not args.apply:
        print("DRY-RUN: no rows modified. Re-run with --apply to bulk-resolve.")
        return 0

    resolved_ids = repo.bulk_resolve(**resolve_kwargs)
    print(f"Resolved {len(resolved_ids)} row(s).")
    return 0


def cmd_bulk_acknowledge(args: argparse.Namespace) -> int:
    ack_kwargs, max_rows = _build_bulk_filters(args, "bulk-acknowledge")

    repo = ReconciliationIssueRepository()
    preview_rows = repo.preview_bulk_acknowledge(**ack_kwargs)
    total = preview_rows[0]["total_match_count"] if preview_rows else 0

    _print_bulk_preview(preview_rows, total, "acknowledge", max_rows)
    print()

    if not args.apply:
        print("DRY-RUN: no rows modified. Re-run with --apply to bulk-acknowledge.")
        return 0

    acknowledged_ids = repo.bulk_acknowledge(**ack_kwargs)
    print(f"Acknowledged {len(acknowledged_ids)} row(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Triage and manage qbo.ReconciliationIssue rows."
    )
    subparsers = parser.add_subparsers(dest="command")

    triage_parser = subparsers.add_parser(
        "triage",
        help="Read-only grouped summary + repeat-count analysis (default command).",
    )
    triage_parser.add_argument(
        "--stale-after-days",
        type=int,
        default=7,
        help="Keys whose most-recent occurrence is older than this many days are 'stale' (default 7).",
    )
    triage_parser.set_defaults(func=cmd_triage)

    ack_parser = subparsers.add_parser("acknowledge", help="Acknowledge one open issue by Id.")
    ack_parser.add_argument("--id", type=int, required=True)
    ack_parser.set_defaults(func=cmd_acknowledge)

    resolve_parser = subparsers.add_parser("resolve", help="Resolve one issue by Id.")
    resolve_parser.add_argument("--id", type=int, required=True)
    resolve_parser.set_defaults(func=cmd_resolve)

    def _add_bulk_filter_args(p: argparse.ArgumentParser, verb: str) -> None:
        """Filter flags shared by bulk-resolve and bulk-acknowledge."""
        p.add_argument(
            "--drift-type",
            default=None,
            help="Scope to one DriftType. STRONGLY RECOMMENDED — it is the only "
                 "filter that reliably excludes the known-bogus "
                 "watermark_hold_bound_exceeded fixture rows.",
        )
        p.add_argument(
            "--entity-type",
            default=None,
            help="Scope to one EntityType. NOTE: SQL Server's default collation is "
                 "case-INSENSITIVE, so 'Bill' also matches rows stored as 'bill'.",
        )
        cutoff = p.add_mutually_exclusive_group()
        cutoff.add_argument(
            "--created-before-days",
            type=int,
            default=None,
            help="Only rows created more than N days ago.",
        )
        cutoff.add_argument(
            "--created-before-date",
            default=None,
            help="Only rows created before this UTC date (YYYY-MM-DD). Mutually exclusive with --created-before-days.",
        )
        p.add_argument("--realm-id", default=None)
        p.add_argument(
            "--severity",
            default=None,
            choices=["low", "medium", "high", "critical"],
            help="Narrowing filter (U-249). Does NOT by itself satisfy the "
                 "at-least-one-filter guard. Use --severity low/medium to keep "
                 "critical rows out of the blast radius.",
        )
        p.add_argument(
            "--action",
            default=None,
            choices=["auto_fixed", "flagged", "manual_review"],
            help="Narrowing filter (U-249). Does NOT by itself satisfy the "
                 "at-least-one-filter guard. Use --action flagged to exclude "
                 "manual_review rows.",
        )
        p.add_argument("--max-rows", type=int, default=1000)
        p.add_argument(
            "--apply",
            action="store_true",
            help=f"Actually {verb} rows. Without this flag the script is read-only.",
        )

    bulk_parser = subparsers.add_parser(
        "bulk-resolve",
        help="Bulk-resolve issues matching filters (dry-run by default).",
    )
    _add_bulk_filter_args(bulk_parser, "resolve")
    bulk_parser.add_argument(
        "--status",
        default="open",
        choices=["open", "acknowledged"],
    )
    bulk_parser.add_argument(
        "--keep-newest-per-group",
        action="store_true",
        help=(
            "Thin RECURRING summary rows: resolve every matching row EXCEPT the "
            "newest of each group, where a group is "
            "(RealmId, DriftType, EntityType, QboId, EntityPublicId, Severity, Action) "
            "and 'newest' is max CreatedDatetime, tie-broken by max Id. Exactly one "
            "row per group survives, so the live signal is preserved. Rows carrying a "
            "distinct QboId (real per-entity drift such as qbo_voided) each form their "
            "OWN group, so this flag is a no-op on them and cannot collapse them."
        ),
    )
    bulk_parser.set_defaults(func=cmd_bulk_resolve)

    ack_bulk_parser = subparsers.add_parser(
        "bulk-acknowledge",
        help=(
            "Bulk-acknowledge issues matching filters (dry-run by default). "
            "Acknowledge = 'seen, awaiting human action'; use this for REAL drift "
            "such as qbo_voided. Use bulk-resolve for things already dealt with."
        ),
    )
    _add_bulk_filter_args(ack_bulk_parser, "acknowledge")
    # No --status flag: 'open' is the only legal source state for open->acknowledged
    # (mirrors the single-row acknowledge). Pinned here so the sproc receives it.
    ack_bulk_parser.set_defaults(func=cmd_bulk_acknowledge, status="open")

    args = parser.parse_args()
    if args.command is None:
        args.command = "triage"
        args.stale_after_days = 7
        args.func = cmd_triage
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
