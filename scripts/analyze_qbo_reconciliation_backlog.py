"""
Read-only triage analysis for qbo.ReconciliationIssue backlog (U-249).

Strictly SELECT-only — never writes to qbo.ReconciliationIssue or calls lifecycle
sprocs (U-246 sprocs may not be applied to prod yet).

Usage:
  PYTHONPATH=. ./.venv/bin/python scripts/analyze_qbo_reconciliation_backlog.py
"""
from __future__ import annotations

from datetime import datetime

from scripts.manage_qbo_reconciliation_issues import _format_dt
from shared.database import get_connection

_MAX_BULK_ROWS = 5000
_DEAD_CANARY_CUTOFF = datetime(2026, 6, 21, 0, 0, 0)
_CLI = "PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py"

BreakdownKey = tuple[str, str | None, str, str, str]
BreakdownCounts = dict[BreakdownKey, int]


def _dedup_delta(total: int) -> int:
    return max(total - 1, 0)


def _breakdown_count(
    breakdown: BreakdownCounts,
    *,
    drift_type: str,
    entity_type: str | None = None,
    severity: str | None = None,
    action: str | None = None,
    status: str = "open",
) -> int:
    total = 0
    for (dt, et, sev, act, stat), cnt in breakdown.items():
        if dt != drift_type or stat != status:
            continue
        if entity_type is not None and et != entity_type:
            continue
        if severity is not None and sev != severity:
            continue
        if action is not None and act != action:
            continue
        total += cnt
    return total


def _breakdown_counts_by_entity_type(
    breakdown: BreakdownCounts,
    *,
    drift_type: str,
    status: str = "open",
) -> list[tuple[str | None, int]]:
    by_entity: dict[str | None, int] = {}
    for (dt, et, _sev, _act, stat), cnt in breakdown.items():
        if dt != drift_type or stat != status:
            continue
        by_entity[et] = by_entity.get(et, 0) + cnt
    return sorted(by_entity.items(), key=lambda item: item[1], reverse=True)


def _open_counts_by_drift_type(breakdown: BreakdownCounts) -> list[tuple[str, int]]:
    by_drift: dict[str, int] = {}
    for (dt, _et, _sev, _act, stat), cnt in breakdown.items():
        if stat != "open":
            continue
        by_drift[dt] = by_drift.get(dt, 0) + cnt
    return sorted(by_drift.items(), key=lambda item: item[1], reverse=True)


def _scalar(conn, sql: str, params=()) -> int:
    cur = conn.cursor()
    cur.execute(sql, params)
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _fetchall(conn, sql: str, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def _print_section_header(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def _section1_triage(conn) -> tuple[list, int, int, BreakdownCounts]:
    _print_section_header("SECTION 1 — Full triage breakdown")

    breakdown_sql = """
    SELECT
        DriftType,
        EntityType,
        Severity,
        Action,
        Status,
        COUNT(*) AS Cnt,
        MIN(CreatedDatetime) AS FirstSeen,
        MAX(CreatedDatetime) AS LastSeen
    FROM qbo.ReconciliationIssue
    GROUP BY DriftType, EntityType, Severity, Action, Status
    ORDER BY Cnt DESC
    """
    rows = _fetchall(conn, breakdown_sql)
    breakdown_counts: BreakdownCounts = {
        (r.DriftType, r.EntityType, r.Severity, r.Action, r.Status): r.Cnt
        for r in rows
    }

    unique_key_sql = """
    SELECT
        DriftType,
        EntityType,
        Severity,
        Action,
        Status,
        COUNT(DISTINCT QboId) AS UniqueKeyCount
    FROM qbo.ReconciliationIssue
    GROUP BY DriftType, EntityType, Severity, Action, Status
    """
    unique_rows = _fetchall(conn, unique_key_sql)
    unique_by_group = {
        (r.DriftType, r.EntityType, r.Severity, r.Action, r.Status): r.UniqueKeyCount
        for r in unique_rows
    }

    if not rows:
        print("No reconciliation issues found.")
    else:
        print(
            f"{'DriftType':<36} {'EntityType':<16} {'Severity':<10} {'Action':<14} "
            f"{'Status':<14} {'RowCount':>8} {'UniqueKeys':>10} {'FirstSeen':<20} {'LastSeen':<20}"
        )
        print("-" * 160)
        for r in rows:
            unique_keys = unique_by_group.get(
                (r.DriftType, r.EntityType, r.Severity, r.Action, r.Status), 0
            )
            print(
                f"{r.DriftType:<36} {r.EntityType or '':<16} {r.Severity:<10} "
                f"{r.Action:<14} {r.Status:<14} {r.Cnt:>8} "
                f"{unique_keys:>10} {_format_dt(r.FirstSeen):<20} "
                f"{_format_dt(r.LastSeen):<20}"
            )

    total = _scalar(conn, "SELECT COUNT(*) FROM qbo.ReconciliationIssue")
    non_open = _scalar(
        conn,
        "SELECT COUNT(*) FROM qbo.ReconciliationIssue WHERE Status <> 'open'",
    )
    print()
    print(f"Total rows: {total}")
    if non_open:
        print(f"*** NON-OPEN ROWS: {non_open} (U-246 lifecycle sprocs may be applied) ***")
    else:
        print("Non-open rows: 0 (all rows are Status='open')")

    return rows, total, non_open, breakdown_counts


def _section2_bogus_fixtures(conn) -> list:
    _print_section_header("SECTION 2 — Bogus fixture rows (watermark_hold_bound_exceeded)")

    sql = """
    SELECT Id, PublicId, QboId, Details, CreatedDatetime
    FROM qbo.ReconciliationIssue
    WHERE DriftType = 'watermark_hold_bound_exceeded'
    ORDER BY CreatedDatetime
    """
    rows = _fetchall(conn, sql)
    if not rows:
        print("No watermark_hold_bound_exceeded rows found.")
    else:
        print(f"{'Id':>8}  {'PublicId':<38}  {'QboId':<14}  {'CreatedDatetime':<20}  Details")
        print("-" * 120)
        for r in rows:
            details = (r.Details or "")[:80]
            print(
                f"{r.Id:>8}  {str(r.PublicId):<38}  {r.QboId or '':<14}  "
                f"{_format_dt(r.CreatedDatetime):<20}  {details}"
            )
    print(f"\nRow count: {len(rows)}")
    return rows


def _section3_dead_canary(conn) -> datetime | None:
    _print_section_header("SECTION 3 — Dead-historical-row canary")

    sql = """
    SELECT MAX(CreatedDatetime)
    FROM qbo.ReconciliationIssue
    WHERE DriftType = 'qbo_missing_locally'
      AND EntityType = 'Bill'
      AND Severity = 'high'
      AND Action = 'flagged'
      AND Status = 'open'
    """
    cur = conn.cursor()
    cur.execute(sql)
    row = cur.fetchone()
    max_ts = row[0] if row else None

    print(
        "Max CreatedDatetime for open high-severity qbo_missing_locally Bill flagged rows: "
        f"{_format_dt(max_ts) or '(none)'}"
    )
    print(f"Canary cutoff (gate deploy + 1 day): {_format_dt(_DEAD_CANARY_CUTOFF)}")

    if max_ts is not None and max_ts > _DEAD_CANARY_CUTOFF:
        print()
        print("*** WARNING: New high-severity Bill missing-locally rows detected AFTER the")
        print("*** 2026-06-20 gate deploy cutoff. QBO_RECONCILE_BILL_AUTOFIX may have been")
        print("*** re-enabled and the vendor_ref_value bug may have recurred.")
    else:
        print("Canary OK: no high-severity Bill flagged rows newer than cutoff.")

    return max_ts


def _bulk_command(*parts: str) -> str:
    return f"{_CLI} {' '.join(parts)}"


def _max_rows_arg(count: int) -> str:
    return f"--max-rows {min(count, _MAX_BULK_ROWS)}"


def _print_batch_note(count: int) -> None:
    if count <= _MAX_BULK_ROWS:
        return
    batches = (count + _MAX_BULK_ROWS - 1) // _MAX_BULK_ROWS
    print(
        f"   NOTE: count exceeds --max-rows cap of {_MAX_BULK_ROWS}; "
        f"run this command {batches} times (or increase filter precision), "
        f"re-checking remaining row count between runs."
    )


def _section4_bulk_policy(
    conn,
    total: int,
    bogus_count: int,
    breakdown_counts: BreakdownCounts,
) -> dict:
    _print_section_header("SECTION 4 — Bulk-resolve policy (TEXT ONLY — do not run until U-246 applied)")

    policy: dict = {}
    cutoff = _DEAD_CANARY_CUTOFF

    combined_sql = """
    SELECT COUNT(*) FROM qbo.ReconciliationIssue
    WHERE DriftType = 'qbo_missing_locally'
      AND EntityType = 'Bill'
      AND Status = 'open'
      AND CreatedDatetime < ?
    """
    combined_count = _scalar(conn, combined_sql, (cutoff,))

    dead_sql = """
    SELECT COUNT(*) FROM qbo.ReconciliationIssue
    WHERE DriftType = 'qbo_missing_locally'
      AND EntityType = 'Bill'
      AND Severity = 'high'
      AND Action = 'flagged'
      AND Status = 'open'
      AND CreatedDatetime < ?
    """
    dead_count = _scalar(conn, dead_sql, (cutoff,))

    stale_sql = """
    SELECT COUNT(*) FROM qbo.ReconciliationIssue
    WHERE DriftType = 'qbo_missing_locally'
      AND EntityType = 'Bill'
      AND Severity = 'low'
      AND Action = 'auto_fixed'
      AND Status = 'open'
      AND CreatedDatetime < ?
    """
    stale_count = _scalar(conn, stale_sql, (cutoff,))

    cutoff_date = cutoff.strftime("%Y-%m-%d")
    merged_cmd = _bulk_command(
        "bulk-resolve",
        "--drift-type qbo_missing_locally",
        "--entity-type Bill",
        f"--created-before-date {cutoff_date}",
        "--status open",
        _max_rows_arg(combined_count),
        "--apply",
    )
    policy["dead-and-stale-missing-locally-bill"] = combined_count
    print("a. dead-and-stale-missing-locally-bill")
    print(f"   Count: {combined_count}")
    print(
        f"   Sub-breakdown (CreatedDatetime < {_format_dt(cutoff)}): "
        f"{dead_count} high/flagged (dead crash-path), "
        f"{stale_count} low/auto_fixed (successful historical pulls)"
    )
    if dead_count + stale_count != combined_count:
        print(
            f"   *** MISMATCH: sub-breakdown sum {dead_count + stale_count} "
            f"!= combined count {combined_count} ***"
        )
    print(
        "   Rationale: pre-cutoff Bill missing-locally rows — dead crash-path noise "
        "AND successful historical auto-fixes. BulkResolve has no @Severity/@Action "
        "params, so one date-scoped filter resolves both buckets together "
        "(see Section 3 canary)."
    )
    print(f"   Command: {merged_cmd}")
    _print_batch_note(combined_count)
    print()

    dedup_delta = 0
    print("b. ongoing-low-severity-summary-dedup")
    for entity_type in ("Bill", "Expense", "BillCredit"):
        low_count = _breakdown_count(
            breakdown_counts,
            drift_type="qbo_missing_locally",
            entity_type=entity_type,
            severity="low",
            action="flagged",
        )
        resolve_delta = _dedup_delta(low_count)
        dedup_delta += resolve_delta
        policy[f"low-summary-{entity_type}"] = low_count
        print(
            f"   EntityType={entity_type}: total={low_count}, "
            f"keep-newest=1, resolve-delta={resolve_delta}"
        )
    print(
        "   Rationale: deduped running summaries — only the latest per EntityType is current."
    )
    print(
        "   GAP: no bulk-resolve filter for Severity/Action combo; "
        "manual per-row resolve or future sproc param needed."
    )
    print(f"   Total dedup delta across Bill/Expense/BillCredit: {dedup_delta}")
    print()

    void_by_entity = _breakdown_counts_by_entity_type(
        breakdown_counts,
        drift_type="qbo_voided",
    )
    void_total = sum(cnt for _entity_type, cnt in void_by_entity)
    policy["qbo-voided-acknowledge"] = void_total
    print("c. qbo-voided-acknowledge")
    print(f"   Total open qbo_voided rows: {void_total}")
    for entity_type, cnt in void_by_entity:
        print(f"     EntityType={entity_type}: {cnt}")
    print(
        "   Rationale: real, distinct, recent drift — acknowledge (not resolve) "
        "so it stays visible until a human confirms."
    )
    print(
        "   No bulk-acknowledge sproc shipped (only AcknowledgeQboReconciliationIssue "
        "by Id). Must run per-row:"
    )
    id_sql = """
    SELECT Id FROM qbo.ReconciliationIssue
    WHERE DriftType = 'qbo_voided' AND Status = 'open'
    ORDER BY Id
    """
    void_ids = _fetchall(conn, id_sql)
    if len(void_ids) <= 10:
        for row in void_ids:
            print(f"     {_bulk_command('acknowledge', f'--id {row.Id}')}")
    else:
        print(f"     ({len(void_ids)} rows — run acknowledge --id <id> for each; showing first 5)")
        for row in void_ids[:5]:
            print(f"     {_bulk_command('acknowledge', f'--id {row.Id}')}")
        print(f"     ... and {len(void_ids) - 5} more")
    print()

    draw_total = _breakdown_count(
        breakdown_counts,
        drift_type="invoice_draw_mismatch",
    )
    draw_dedup_delta = _dedup_delta(draw_total)
    newest_draw_sql = """
    SELECT TOP 1 Details, CreatedDatetime
    FROM qbo.ReconciliationIssue
    WHERE DriftType = 'invoice_draw_mismatch'
      AND Status = 'open'
    ORDER BY CreatedDatetime DESC
    """
    newest_draw_rows = _fetchall(conn, newest_draw_sql)
    policy["ongoing-invoice-draw-summary-dedup"] = draw_total
    print("d. ongoing-invoice-draw-summary-dedup")
    print(f"   Total open rows: {draw_total}")
    print(f"   keep-newest=1, resolve-delta={draw_dedup_delta}")
    if newest_draw_rows:
        newest = newest_draw_rows[0]
        details = newest.Details or ""
        details_preview = details if len(details) <= 300 else details[:300] + "..."
        print(
            f"   Current drift state as of {_format_dt(newest.CreatedDatetime)}:"
        )
        print(f"     {details_preview}")
    print(
        "   Rationale: same per-run-full-rewrite pattern as (b) — each row is a "
        "complete fresh re-summary of currently-drifting invoices, not a repeat of "
        "one stuck invoice (EntityPublicId/QboId are NULL for this drift type; "
        "verify by reading Details). Safe to dedupe to newest like (b)."
    )
    print(
        "   GAP: BulkResolveQboReconciliationIssuesByFilter still has no way to "
        "filter to only non-newest rows (same limitation as (b)); unlike (a)/(e), "
        "filtering by DriftType alone would also catch the one row to KEEP — needs "
        "per-row resolve or a future date-scoped resolve."
    )
    print()

    bogus_open = _breakdown_count(
        breakdown_counts,
        drift_type="watermark_hold_bound_exceeded",
    )
    policy["bogus-watermark-fixtures"] = bogus_open
    print("e. bogus-watermark-fixtures")
    print(f"   Count (open): {bogus_open}  (Section 2 row count: {bogus_count})")
    if bogus_open != bogus_count:
        print("   *** MISMATCH between Section 2 and open-status count ***")
    print(
        "   Rationale: confirmed bogus test-session artifacts (fake QboIds, one 53-minute window)."
    )
    bogus_cmd = _bulk_command(
        "bulk-resolve",
        "--drift-type watermark_hold_bound_exceeded",
        "--status open",
        _max_rows_arg(bogus_open),
        "--apply",
    )
    print(f"   Command: {bogus_cmd}")
    _print_batch_note(bogus_open)
    print()

    clear_resolve = combined_count + bogus_open
    net_after = total - clear_resolve - dedup_delta - draw_dedup_delta
    print("Net effect if all filters above were applied:")
    print(f"  Total rows today: {total}")
    print(
        f"  Minus clear-resolve (a)+(e): {clear_resolve}  "
        f"[(a)={combined_count} (dead {dead_count} + stale {stale_count}), (e)={bogus_open}]"
    )
    print(f"  Minus (b) low-summary dedup delta: {dedup_delta}")
    print(f"  Minus (d) invoice-draw-summary dedup delta: {draw_dedup_delta}")
    print(f"  Estimated rows remaining open: {net_after}")
    print()
    print("What stays genuinely actionable (not auto-resolved by filters above):")
    print("  - (c) qbo_voided: acknowledge-only, stays visible")
    print(
        "  - (d) 1 remaining invoice-draw summary (the newest, current-state row) — "
        "needs human/product review of its actual drift content, not the "
        f"{draw_dedup_delta} historical duplicate summaries (safe to bulk-resolve like (b))"
    )
    print("  - (b) 3 kept low-summary rows (1 per Bill/Expense/BillCredit)")
    print("  - Any drift types not covered by filters (a)/(b)/(d)/(e)")

    print()
    print("Current open rows by DriftType:")
    for drift_type, cnt in _open_counts_by_drift_type(breakdown_counts):
        print(f"  {drift_type}: {cnt}")

    return policy


def main() -> int:
    with get_connection() as conn:
        _, total, _, breakdown_counts = _section1_triage(conn)
        bogus_rows = _section2_bogus_fixtures(conn)
        _section3_dead_canary(conn)
        _section4_bulk_policy(conn, total, len(bogus_rows), breakdown_counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
