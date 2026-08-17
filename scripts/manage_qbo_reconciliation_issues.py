"""
Operator CLI for qbo.ReconciliationIssue triage and lifecycle management (U-246).

IMPORTANT: The four new sprocs appended in U-246
(integrations/intuit/qbo/reconciliation/sql/qbo.reconciliation_issue.sql —
AcknowledgeQboReconciliationIssue, ResolveQboReconciliationIssue,
BulkResolveQboReconciliationIssuesByFilter, ReadQboReconciliationIssueTriageSummary)
have NOT been applied to prod yet. All subcommands that call them (triage,
acknowledge, resolve, bulk-resolve) will fail until a human applies that SQL file.

Usage:
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py triage
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py triage --stale-after-days 14
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py acknowledge --id 12345
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py resolve --id 12345
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py bulk-resolve --drift-type orphaned_item_scc_mapping --created-before-days 30
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py bulk-resolve --drift-type pull_delete_reconcile --entity-type Bill --apply
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
    """Return a naive UTC cutoff datetime matching pyodbc DATETIME2 convention."""
    if days < 0:
        print(f"Refusing: days must be >= 0 (got {days}).")
        sys.exit(2)
    return datetime.utcnow() - timedelta(days=days)


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


def _build_bulk_resolve_filters(args: argparse.Namespace):
    if not args.drift_type and not args.entity_type and args.created_before_days is None:
        print(
            "Refusing bulk-resolve: at least one of --drift-type, --entity-type, "
            "or --created-before-days is required."
        )
        sys.exit(2)

    max_rows = _validate_max_rows(args.max_rows)

    created_before: Optional[datetime] = None
    if args.created_before_days is not None:
        created_before = _utc_cutoff(args.created_before_days)

    resolve_kwargs = {
        "drift_type": args.drift_type,
        "entity_type": args.entity_type,
        "created_before": created_before,
        "realm_id": args.realm_id,
        "status": args.status,
        "max_rows": max_rows,
    }
    return resolve_kwargs, max_rows


def cmd_bulk_resolve(args: argparse.Namespace) -> int:
    resolve_kwargs, max_rows = _build_bulk_resolve_filters(args)

    repo = ReconciliationIssueRepository()
    preview_rows = repo.preview_bulk_resolve(**resolve_kwargs)
    total = preview_rows[0]["total_match_count"] if preview_rows else 0

    print(f"Matched {total} row(s) (max resolve batch: {max_rows}):")
    for r in preview_rows:
        print(
            f"  Id={r['id']:>6}  DriftType={r['drift_type']:<32} EntityType={r['entity_type']:<12} "
            f"QboId={r['qbo_id'] or '':<12} Created={r['created_datetime']}"
        )
    if total > len(preview_rows):
        print(f"  ... and {total - len(preview_rows)} more")
    print()

    if not args.apply:
        print("DRY-RUN: no rows modified. Re-run with --apply to bulk-resolve.")
        return 0

    resolved_ids = repo.bulk_resolve(**resolve_kwargs)
    print(f"Resolved {len(resolved_ids)} row(s).")
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

    bulk_parser = subparsers.add_parser(
        "bulk-resolve",
        help="Bulk-resolve issues matching filters (dry-run by default).",
    )
    bulk_parser.add_argument("--drift-type", default=None)
    bulk_parser.add_argument("--entity-type", default=None)
    bulk_parser.add_argument(
        "--created-before-days",
        type=int,
        default=None,
        help="Only rows created more than N days ago.",
    )
    bulk_parser.add_argument("--realm-id", default=None)
    bulk_parser.add_argument(
        "--status",
        default="open",
        choices=["open", "acknowledged"],
    )
    bulk_parser.add_argument("--max-rows", type=int, default=1000)
    bulk_parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually resolve rows. Without this flag the script is read-only.",
    )
    bulk_parser.set_defaults(func=cmd_bulk_resolve)

    args = parser.parse_args()
    if args.command is None:
        args.command = "triage"
        args.stale_after_days = 7
        args.func = cmd_triage
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
