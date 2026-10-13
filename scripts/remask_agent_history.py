"""
One-time operator script: re-mask agent history NVARCHAR columns written before U-535l.

Dry-run by default; --apply performs optimistic updates (row unchanged since read).

Usage (from repo root):
  ./.venv/bin/python -m scripts.remask_agent_history
  ./.venv/bin/python -m scripts.remask_agent_history --apply --table tool_call --limit 1000
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from intelligence.persistence.redaction import mask_agent_free_text, mask_agent_text
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

_BATCH_SIZE = 500
_MAX_CHANGED_IDS = 20
_MAX_CONSECUTIVE_FAILURES = 5


@dataclass(frozen=True)
class _ColumnTarget:
    table: str
    column: str
    group: str
    json_payload: bool = False  # tool payloads; every other column is free text


_TARGETS: tuple[_ColumnTarget, ...] = (
    _ColumnTarget("dbo.AgentToolCall", "ToolInput", "tool_call", json_payload=True),
    _ColumnTarget("dbo.AgentToolCall", "ToolOutput", "tool_call", json_payload=True),
    # ProposedInput / FinalInput are executable tool payloads (a pending approval is
    # reloaded and run), so the backfill must never rewrite them; see approval_repo.py.
    _ColumnTarget("dbo.AgentApprovalRequest", "Summary", "approval"),
    _ColumnTarget("dbo.AgentTurn", "AssistantText", "turn"),
    _ColumnTarget("dbo.AgentSession", "UserMessage", "session"),
    _ColumnTarget("dbo.AgentSession", "ErrorMessage", "session"),
)

_TABLE_GROUPS = frozenset(t.group for t in _TARGETS)
_VALID_TABLES = _TABLE_GROUPS | {"all"}


@dataclass
class _ColumnStats:
    scanned: int = 0
    would_change: int = 0
    changed: int = 0
    skipped_changed: int = 0
    failed: int = 0
    aborted: bool = False
    sample_ids: list[int] = field(default_factory=list)


def _update_sql(table: str, column: str) -> str:
    return (
        f"UPDATE {table} SET [{column}] = CAST(? AS NVARCHAR(MAX)), "
        f"ModifiedDatetime = SYSUTCDATETIME() "
        f"WHERE Id = ? AND RowVersion = ?"
    )


def _select_sql(table: str, column: str) -> str:
    return (
        f"SELECT TOP (?) [Id], [RowVersion], [{column}] "
        f"FROM {table} "
        f"WHERE [Id] > ? AND [{column}] IS NOT NULL "
        f"ORDER BY [Id]"
    )


def _record_failure(
    stats: _ColumnStats,
    consecutive_failures: int,
    exc: BaseException,
    *,
    row_id: int | None = None,
) -> tuple[int, bool]:
    stats.failed += 1
    consecutive_failures += 1
    if row_id is not None:
        print(f"  failed id={row_id} error={type(exc).__name__}")
    else:
        print(f"  read error={type(exc).__name__}")
    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
        print("  aborting column after consecutive failures")
        stats.aborted = True
        return consecutive_failures, True
    return consecutive_failures, False


def _read_batch(
    *,
    connection_factory: Callable,
    select_sql: str,
    batch_size: int,
    last_id: int,
    stats: _ColumnStats,
    consecutive_failures: int,
) -> tuple[list, int, bool]:
    """Returns (rows, consecutive_failures, should_abort)."""
    try:
        with connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute(select_sql, (batch_size, last_id))
            rows = cursor.fetchall()
    except Exception as exc:
        consecutive_failures, should_abort = _record_failure(
            stats, consecutive_failures, exc
        )
        return [], consecutive_failures, should_abort
    return rows, consecutive_failures, False


def remask_column(
    *,
    target: _ColumnTarget,
    apply: bool,
    limit: Optional[int],
    connection_factory: Callable = get_connection,
) -> _ColumnStats:
    stats = _ColumnStats()
    consecutive_failures = 0
    last_id = 0
    remaining = limit

    masker = mask_agent_text if target.json_payload else mask_agent_free_text
    update_sql = _update_sql(target.table, target.column)
    select_sql = _select_sql(target.table, target.column)

    while True:
        batch_size = _BATCH_SIZE
        if remaining is not None:
            if remaining <= 0:
                break
            batch_size = min(batch_size, remaining)

        rows, consecutive_failures, should_abort = _read_batch(
            connection_factory=connection_factory,
            select_sql=select_sql,
            batch_size=batch_size,
            last_id=last_id,
            stats=stats,
            consecutive_failures=consecutive_failures,
        )
        if should_abort:
            return stats

        if not rows:
            break

        def _process_rows(conn=None, cur=None):
            nonlocal consecutive_failures, last_id, remaining
            for row in rows:
                row_id = int(row.Id)
                last_id = row_id
                stats.scanned += 1
                if remaining is not None:
                    remaining -= 1

                row_version = row.RowVersion
                original = row[2]

                try:
                    masked = masker(original)
                except Exception as exc:
                    consecutive_failures, should_abort = _record_failure(
                        stats, consecutive_failures, exc, row_id=row_id
                    )
                    if should_abort:
                        return True
                    continue

                if masked == original:
                    consecutive_failures = 0
                    continue

                if apply:
                    assert cur is not None and conn is not None
                    try:
                        cur.execute(update_sql, (masked, row_id, row_version))
                        conn.commit()
                        if cur.rowcount == 0:
                            stats.skipped_changed += 1
                            consecutive_failures = 0
                        else:
                            stats.changed += 1
                            consecutive_failures = 0
                            if len(stats.sample_ids) < _MAX_CHANGED_IDS:
                                stats.sample_ids.append(row_id)
                    except Exception as exc:
                        consecutive_failures, should_abort = _record_failure(
                            stats, consecutive_failures, exc, row_id=row_id
                        )
                        if should_abort:
                            return True
                else:
                    consecutive_failures = 0
                    stats.would_change += 1
                    if len(stats.sample_ids) < _MAX_CHANGED_IDS:
                        stats.sample_ids.append(row_id)
            return False

        if apply:
            with connection_factory() as conn:
                cur = conn.cursor()
                if _process_rows(conn, cur):
                    return stats
        elif _process_rows():
            return stats

        if len(rows) < batch_size:
            break

    return stats


def remask_agent_history(
    *,
    apply: bool,
    table: str,
    limit: Optional[int],
    connection_factory: Callable = get_connection,
) -> dict[tuple[str, str], _ColumnStats]:
    groups = {table} if table != "all" else set(_TABLE_GROUPS)
    targets = [t for t in _TARGETS if t.group in groups]
    results: dict[tuple[str, str], _ColumnStats] = {}
    for target in targets:
        stats = remask_column(
            target=target,
            apply=apply,
            limit=limit,
            connection_factory=connection_factory,
        )
        results[(target.table, target.column)] = stats
    return results


def _print_stats(
    label: str, table: str, column: str, stats: _ColumnStats, *, apply: bool
) -> None:
    changed_key = "changed" if apply else "would_change"
    changed_val = stats.changed if apply else stats.would_change
    ids = stats.sample_ids
    id_suffix = f" ids={ids}" if ids else ""
    print(
        f"  {label} {table}.{column}: scanned={stats.scanned} "
        f"{changed_key}={changed_val} skipped_changed={stats.skipped_changed} "
        f"failed={stats.failed}{id_suffix}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-mask agent history text columns (dry-run by default)."
    )
    ap.add_argument(
        "--table",
        choices=sorted(_VALID_TABLES),
        default="all",
        help="Which table group to scan (default: all).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max rows to scan per column (default: no limit).",
    )
    ap.add_argument("--apply", action="store_true", help="Write updates (default: dry-run).")
    args = ap.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        print("--limit must be a positive integer")
        return 2

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode}: remask agent history (table={args.table}) ===")

    results = remask_agent_history(
        apply=args.apply,
        table=args.table,
        limit=args.limit,
    )
    exit_code = 0
    for (tbl, col), stats in results.items():
        _print_stats(mode, tbl, col, stats, apply=args.apply)
        if stats.failed > 0:
            exit_code = 1

    if not args.apply:
        print("DRY RUN: no rows changed. Re-run with --apply to update.")
    return exit_code


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
