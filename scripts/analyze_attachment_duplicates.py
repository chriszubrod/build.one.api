"""
Read-only report: byte-identical dbo.Attachment rows duplicated within the same bill.

Groups Attachment rows by (FileHash, FileSize) scoped to each Bill via
BillLineItemAttachment → BillLineItem → Bill. Never writes to the DB.

Usage:
  PYTHONPATH=. ./.venv/bin/python scripts/analyze_attachment_duplicates.py
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from scripts.manage_qbo_reconciliation_issues import _format_dt as _format_dt_base
from shared.database import get_connection

_UUID4_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_SAME_ACTION_WINDOW_SECONDS = 5

_DUPLICATE_ROWS_SQL = """
WITH scoped AS (
    SELECT DISTINCT
        b.Id AS bill_id,
        b.BillNumber AS bill_number,
        a.FileHash AS file_hash,
        a.FileSize AS file_size,
        a.Id AS attachment_id,
        a.Filename AS filename,
        a.CreatedDatetime AS created_datetime
    FROM dbo.Attachment a
    JOIN dbo.BillLineItemAttachment blia ON blia.AttachmentId = a.Id
    JOIN dbo.BillLineItem bli ON bli.Id = blia.BillLineItemId
    JOIN dbo.Bill b ON b.Id = bli.BillId
    WHERE a.FileHash IS NOT NULL
      AND LTRIM(RTRIM(a.FileHash)) <> ''
      AND a.FileSize IS NOT NULL
)
SELECT bill_id, bill_number, file_hash, file_size, attachment_id, filename, created_datetime
FROM (
    SELECT *, COUNT(*) OVER (PARTITION BY bill_id, file_hash, file_size) AS group_count
    FROM scoped
) ranked
WHERE group_count > 1
ORDER BY bill_id, file_hash, attachment_id
"""


def _classify_filename_shape(filename: str) -> str:
    """Proxy signal only, NOT proof of which endpoint created the row: whole basename
    is UUID4-shaped (matches SharePoint bill-folder auto-intake's `bills/{uuid}.pdf`
    naming) vs anything else (matches the manual upload endpoints' human-supplied
    filenames). Confounded in principle by any other path that mints a UUID-named
    blob -- e.g. contract_labor's generated bill PDF
    (entities/contract_labor/business/bill_service.py:635, `f"{bill.public_id}.pdf"`)
    uses the same shape, though it always passes file_hash=None so it can never appear
    as a member of a group this script finds.
    """
    stem = Path(filename or "").stem
    return "uuid-shaped" if _UUID4_PATTERN.fullmatch(stem) else "human-shaped"


@dataclass(frozen=True)
class DuplicateMember:
    attachment_id: int
    filename: str | None
    created_datetime: datetime | None
    filename_shape: str


@dataclass(frozen=True)
class DuplicateGroup:
    bill_id: int
    bill_number: str | None
    file_hash: str
    file_size: int
    members: tuple[DuplicateMember, ...]
    timing: str


def _coerce_datetime(value: Any) -> datetime | None:
    """pyodbc returns DATETIME2 columns as native datetime objects (no output
    converter is registered anywhere in this repo) -- this only guards against a
    non-datetime input rather than parsing any real wire format."""
    return value if isinstance(value, datetime) else None


def _classify_duplicate_timing(
    created_datetimes: Sequence[datetime | None],
    *,
    window_seconds: int = _SAME_ACTION_WINDOW_SECONDS,
) -> str:
    """burst = within a few seconds (one user action); spread = separate ingestion events."""
    stamps = sorted(dt for dt in created_datetimes if dt is not None)
    if len(stamps) <= 1:
        return "burst"
    span = stamps[-1] - stamps[0]
    if span <= timedelta(seconds=window_seconds):
        return "burst"
    return "spread"


def _group_duplicates(rows: list[dict[str, Any]]) -> list[DuplicateGroup]:
    """
    Pure grouping over flat rows already fetched from the DB (post-DISTINCT: an
    attachment_id appears once per (bill_id, file_hash, file_size) key in `rows`).

    Each input row must expose: bill_id, bill_number, file_hash, file_size,
    attachment_id, filename, created_datetime.
    """
    buckets: dict[tuple[int, str, int], list[DuplicateMember]] = {}
    bill_numbers: dict[tuple[int, str, int], str | None] = {}

    for row in rows:
        bill_id = int(row["bill_id"])
        file_hash = str(row["file_hash"])
        file_size = int(row["file_size"])
        filename = row.get("filename")
        key = (bill_id, file_hash, file_size)

        bill_numbers[key] = row.get("bill_number")
        buckets.setdefault(key, []).append(
            DuplicateMember(
                attachment_id=int(row["attachment_id"]),
                filename=filename,
                created_datetime=_coerce_datetime(row.get("created_datetime")),
                filename_shape=_classify_filename_shape(filename or ""),
            )
        )

    groups: list[DuplicateGroup] = []
    for key, members in sorted(buckets.items()):
        if len(members) <= 1:
            continue
        ordered = tuple(
            sorted(members, key=lambda m: (m.created_datetime or datetime.min, m.attachment_id))
        )
        timing = _classify_duplicate_timing([m.created_datetime for m in ordered])
        groups.append(
            DuplicateGroup(
                bill_id=key[0],
                bill_number=bill_numbers[key],
                file_hash=key[1],
                file_size=key[2],
                members=ordered,
                timing=timing,
            )
        )
    return groups


def _summarize_groups(groups: Sequence[DuplicateGroup]) -> dict[str, int]:
    excess_rows = sum(len(g.members) - 1 for g in groups)
    reclaimable_bytes = sum((len(g.members) - 1) * g.file_size for g in groups)
    burst_groups = sum(1 for g in groups if g.timing == "burst")
    spread_groups = sum(1 for g in groups if g.timing == "spread")
    return {
        "duplicate_groups": len(groups),
        "excess_attachment_rows": excess_rows,
        "reclaimable_blob_bytes": reclaimable_bytes,
        "burst_groups": burst_groups,
        "spread_groups": spread_groups,
    }


def _fetch_duplicate_rows(conn) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(_DUPLICATE_ROWS_SQL)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _print_section_header(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def _format_dt(value: datetime | None) -> str:
    return _format_dt_base(value) or "(null)"


def main() -> int:
    with get_connection() as conn:
        flat_rows = _fetch_duplicate_rows(conn)

    groups = _group_duplicates(flat_rows)
    summary = _summarize_groups(groups)

    _print_section_header("BILL ATTACHMENT DUPLICATE REPORT (read-only)")
    print(f"Duplicate groups:            {summary['duplicate_groups']}")
    print(f"Excess Attachment rows:      {summary['excess_attachment_rows']}")
    print(f"Reclaimable blob bytes:      {summary['reclaimable_blob_bytes']:,}")
    print(f"Timing — burst (≤{_SAME_ACTION_WINDOW_SECONDS}s): {summary['burst_groups']}")
    print(f"Timing — spread:             {summary['spread_groups']}")

    if not groups:
        print("\nNo byte-identical duplicate Attachment groups found within any bill.")
        return 0

    _print_section_header("DUPLICATE GROUPS")
    print("(filename_shape is a proxy signal only -- inferred from filename convention,")
    print(" not proof of which endpoint created the row.)")
    for group in groups:
        print()
        print(
            f"BillId={group.bill_id} BillNumber={group.bill_number!r} "
            f"FileHash={group.file_hash} FileSize={group.file_size} "
            f"count={len(group.members)} timing={group.timing}"
        )
        for member in group.members:
            print(
                f"  AttachmentId={member.attachment_id} "
                f"Filename={member.filename!r} "
                f"CreatedDatetime={_format_dt(member.created_datetime)} "
                f"filename_shape={member.filename_shape}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
