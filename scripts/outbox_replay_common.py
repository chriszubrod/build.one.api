"""Shared helpers for dead-letter outbox replay CLI scripts."""

from __future__ import annotations

import argparse
from typing import Optional, Sequence


def build_replay_parser(
    description: str,
    *,
    max_limit: Optional[int] = None,
    kind_help: Optional[str] = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate rows. Without this flag the script is read-only.",
    )
    parser.add_argument(
        "--kind",
        action="append",
        default=[],
        help=kind_help or "Only reset rows with the given Kind. Can be repeated.",
    )
    limit_help = "Cap the number of rows reset (default 1000"
    if max_limit is not None:
        limit_help += f", max {max_limit}"
    limit_help += ")."
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help=limit_help,
    )
    return parser


def validate_replay_limit(limit: int, *, max_limit: Optional[int] = None) -> bool:
    """Return True when limit is out of the allowed range."""
    if limit < 1:
        return True
    if max_limit is not None and limit > max_limit:
        return True
    return False


def _build_dead_letter_where(kinds: Sequence[str]) -> tuple[str, list]:
    where_clauses = ["Status = 'dead_letter'"]
    params: list = []
    if kinds:
        placeholders = ",".join("?" for _ in kinds)
        where_clauses.append(f"Kind IN ({placeholders})")
        params.extend(kinds)
    return " AND ".join(where_clauses), params


def fetch_dead_letter_rows(
    cursor,
    *,
    schema_table: str,
    limit: int,
    kinds: Sequence[str],
) -> list:
    where, params = _build_dead_letter_where(kinds)
    cursor.execute(
        f"""
        SELECT TOP ({limit})
            Id, PublicId, Kind, EntityType, EntityPublicId, Attempts,
            CONVERT(VARCHAR(19), DeadLetteredAt, 120) AS DeadLetteredAt,
            LEFT(LastError, 120) AS LastError
        FROM {schema_table}
        WHERE {where}
        ORDER BY Id
        """,
        *params,
    )
    return cursor.fetchall()


def print_dead_letter_preview(
    rows: Sequence,
    kinds: Sequence[str],
    *,
    qbo_blind_reset_warning: bool = False,
) -> None:
    if qbo_blind_reset_warning:
        scope = f" matching filter {list(kinds)!r}" if kinds else " (ALL kinds)"
    else:
        scope = " matching filter" if kinds else ""
    print(f"Found {len(rows)} dead-letter row(s){scope}:")
    if qbo_blind_reset_warning and not kinds:
        print()
        print(
            "WARNING: No --kind filter. A blind full reset can re-issue a write that "
            "already landed in QBO. Prefer --kind sync_bill_to_qbo (or the specific "
            "kind) after confirming LastError and entity state."
        )
    print()
    for row in rows:
        print(
            f"  Id={row[0]:>6}  Kind={row[2]:<25} Entity={row[3]:<15} "
            f"EntityPID={str(row[4])[:8]} Attempts={row[5]} "
            f"DL={row[6]} Err={row[7]}"
        )
    print()


def preview_dead_letters(
    cursor,
    *,
    schema_table: str,
    limit: int,
    kinds: Sequence[str],
    apply: bool,
    qbo_blind_reset_warning: bool = False,
) -> tuple[Optional[list], int]:
    """
    Fetch dead-letter rows, print preview, and handle empty-set / dry-run exit.

    Returns (rows, exit_code). rows is None when the caller should return
    exit_code immediately (nothing matched, or dry-run).
    """
    rows = fetch_dead_letter_rows(
        cursor,
        schema_table=schema_table,
        limit=limit,
        kinds=kinds,
    )
    if not rows:
        print("No dead-letter rows matched the filter. Nothing to do.")
        return None, 0

    print_dead_letter_preview(
        rows,
        kinds,
        qbo_blind_reset_warning=qbo_blind_reset_warning,
    )

    if not apply:
        print("DRY-RUN: no rows modified. Re-run with --apply to reset these rows.")
        return None, 0

    return rows, 0
