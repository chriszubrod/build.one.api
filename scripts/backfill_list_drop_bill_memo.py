"""
U-728b: backfill Bill.Memo for pre-U-728 list-drop bills still in review.

Dry-run by default: writes a CSV plan (--out required) and prints counts only.
Uses the BillIntakePrediction attachment and cached extraction text only (no new DI/blob
writes). Apply reviewed plans with --apply-from (no extraction, no LLM).

Writes in dry run: LlmDailySpend accounting only (via run_bill_header_extraction).
Writes in apply: guarded Memo UPDATE per reviewed row only.

Usage (repo root):
  ./.venv/bin/python scripts/backfill_list_drop_bill_memo.py --out /tmp/plan.csv
  ./.venv/bin/python scripts/backfill_list_drop_bill_memo.py --out /tmp/result.csv \\
      --apply-from /tmp/plan.csv

DO NOT run against production without an explicit ops runbook step.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import logging
import sys
from dataclasses import dataclass
from typing import Any, Optional, TextIO
from uuid import UUID

from entities.attachment.business.extraction_service import AttachmentExtractionService
from entities.attachment.business.service import AttachmentService
from entities.bill.business.bill_memo import compose_bill_memo
from entities.bill.business.draft_from_attachment_service import _resolve_entered_by_for_memo
from entities.bill.business.read_attachment_service import (
    _header_from_cascade,
    read_attachment_link_state,
)
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.persistence.repo import BillLineItemAttachmentRepository
from intelligence.cascade.bill_header_extraction import run_bill_header_extraction
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_list_drop_bill_memo")

DEFAULT_LIMIT = 100
INTAKE_SOURCE_LIST_DROP = "list_drop"

CSV_COLUMNS = (
    "bill_id",
    "bill_public_id",
    "status",
    "current_bill_number",
    "proposed_memo",
    "outcome",
)

UPDATE_MEMO_SQL = """
UPDATE dbo.[Bill]
SET [Memo] = ?, [ModifiedDatetime] = SYSUTCDATETIME()
WHERE [Id] = ?
  AND [PublicId] = ?
  AND ([Memo] IS NULL OR LTRIM(RTRIM([Memo])) = '')
  AND [Status] = ?
  AND [Status] IN ('in_review', 'draft')
  AND EXISTS (
      SELECT 1 FROM dbo.[BillIntakePrediction] p
      WHERE p.[BillId] = dbo.[Bill].[Id] AND p.[IntakeSource] = 'list_drop'
  )
"""


def _field_value(fields: dict, key: str) -> Optional[str]:
    block = fields.get(key) or {}
    val = block.get("value")
    if val is None or val == "":
        return None
    return str(val)


def _statuses_from_args(include_status: list[str]) -> list[str]:
    statuses = ["in_review"]
    if include_status:
        for s in include_status:
            if s not in statuses:
                statuses.append(s)
    return statuses


def _candidate_sql(
    *,
    statuses: list[str],
    bill_ids: Optional[list[int]],
    limit: int,
) -> tuple[str, tuple[Any, ...]]:
    status_placeholders = ", ".join("?" for _ in statuses)
    params: list[Any] = [limit, INTAKE_SOURCE_LIST_DROP, *statuses]
    bill_filter = ""
    if bill_ids:
        bill_placeholders = ", ".join("?" for _ in bill_ids)
        bill_filter = f" AND b.[Id] IN ({bill_placeholders})"
        params.extend(bill_ids)
    sql = f"""
    SELECT TOP (?)
        b.[Id] AS BillId,
        b.[PublicId] AS BillPublicId,
        b.[Status] AS BillStatus,
        b.[BillNumber] AS BillNumber,
        COALESCE(p.[CreatedByUserId], b.[CreatedByUserId]) AS EnteredByUserId,
        p.[AttachmentId] AS PredictionAttachmentId
    FROM dbo.[Bill] b
    CROSS APPLY (
        SELECT TOP 1
            p.[CreatedByUserId],
            p.[AttachmentId]
        FROM dbo.[BillIntakePrediction] p
        WHERE p.[BillId] = b.[Id]
          AND p.[IntakeSource] = ?
        ORDER BY p.[Id]
    ) p
    WHERE b.[Status] IN ({status_placeholders})
      AND (b.[Memo] IS NULL OR LTRIM(RTRIM(b.[Memo])) = '')
      {bill_filter}
    ORDER BY b.[Id]
    """
    return sql, tuple(params)


@dataclass(frozen=True)
class CandidateRow:
    bill_id: int
    bill_public_id: str
    status: str
    bill_number: Optional[str]
    entered_by_user_id: Optional[int]
    prediction_attachment_id: Optional[int]


def list_candidates(
    conn,
    *,
    statuses: list[str],
    bill_ids: Optional[list[int]] = None,
    limit: int = DEFAULT_LIMIT,
) -> list[CandidateRow]:
    sql, params = _candidate_sql(statuses=statuses, bill_ids=bill_ids, limit=limit)
    cur = conn.cursor()
    cur.execute(sql, params)
    rows: list[CandidateRow] = []
    for r in cur.fetchall():
        pred_att = getattr(r, "PredictionAttachmentId", None)
        rows.append(
            CandidateRow(
                bill_id=int(r.BillId),
                bill_public_id=str(UUID(str(r.BillPublicId))),
                status=str(r.BillStatus),
                bill_number=str(r.BillNumber) if r.BillNumber is not None else None,
                entered_by_user_id=int(r.EnteredByUserId) if r.EnteredByUserId is not None else None,
                prediction_attachment_id=int(pred_att) if pred_att is not None else None,
            )
        )
    return rows


def _attachment_linked_to_bill(bill_id: int, attachment_id: int) -> bool:
    bli_svc = BillLineItemService()
    blia_repo = BillLineItemAttachmentRepository()
    for line in bli_svc.read_by_bill_id(bill_id=bill_id):
        if line.id is None:
            continue
        link = blia_repo.read_by_bill_line_item_id(bill_line_item_id=int(line.id))
        if link is not None and link.attachment_id is not None:
            if int(link.attachment_id) == int(attachment_id):
                return True
    return False


def _cached_masked_text_for_bill_drop(
    attachment: Any,
    *,
    is_taxpayer_document: bool,
) -> Optional[str]:
    """Reuse-only bill-drop masked text (mirrors extract_for_bill_drop_prefill cache path)."""
    ext_svc = AttachmentExtractionService()
    aggressive = bool(is_taxpayer_document)
    if not attachment.blob_url:
        return None
    att = attachment
    reused = ext_svc._bill_drop_reusable_payload(att, aggressive=aggressive)
    if reused is None:
        fresh = ext_svc.repo.read_by_id(int(att.id))
        if fresh is not None:
            att = fresh
            reused = ext_svc._bill_drop_reusable_payload(att, aggressive=aggressive)
    if reused is None:
        return None
    outcome = ext_svc._completed_outcome_from_payload(att, reused, reused=True)
    if outcome.get("status") != "completed":
        return None
    masked = outcome.get("masked_text") or ""
    if not str(masked).strip():
        return None
    return str(masked)


@dataclass
class RowOutcome:
    bill_id: int
    bill_public_id: str
    status: str
    current_bill_number: Optional[str]
    proposed_memo: Optional[str]
    outcome: str
    stop_daily_cap: bool = False


async def process_candidate(candidate: CandidateRow) -> RowOutcome:
    base = RowOutcome(
        bill_id=candidate.bill_id,
        bill_public_id=candidate.bill_public_id,
        status=candidate.status,
        current_bill_number=candidate.bill_number,
        proposed_memo=None,
        outcome="skipped_no_attachment",
    )
    try:
        attachment_id = candidate.prediction_attachment_id
        if attachment_id is None:
            return base

        attachment = await asyncio.to_thread(
            AttachmentService().read_by_id,
            id=int(attachment_id),
        )
        if attachment is None:
            return base

        linked = await asyncio.to_thread(
            _attachment_linked_to_bill,
            candidate.bill_id,
            int(attachment_id),
        )
        if not linked:
            return RowOutcome(
                bill_id=base.bill_id,
                bill_public_id=base.bill_public_id,
                status=base.status,
                current_bill_number=base.current_bill_number,
                proposed_memo=None,
                outcome="skipped_attachment_unlinked",
            )

        link_state = await asyncio.to_thread(read_attachment_link_state, int(attachment_id))
        masked_text = await asyncio.to_thread(
            _cached_masked_text_for_bill_drop,
            attachment,
            is_taxpayer_document=link_state.is_taxpayer,
        )
        if masked_text is None:
            return RowOutcome(
                bill_id=base.bill_id,
                bill_public_id=base.bill_public_id,
                status=base.status,
                current_bill_number=base.current_bill_number,
                proposed_memo=None,
                outcome="skipped_no_cached_extraction",
            )

        header_cascade = await run_bill_header_extraction({"bill_text": masked_text})
        if header_cascade.stopped_reason == "daily_cap":
            return RowOutcome(
                bill_id=base.bill_id,
                bill_public_id=base.bill_public_id,
                status=base.status,
                current_bill_number=base.current_bill_number,
                proposed_memo=None,
                outcome="stopped_daily_cap",
                stop_daily_cap=True,
            )

        fields, header_ok, _model_id, _vendor_match = await asyncio.to_thread(
            _header_from_cascade,
            header_cascade,
        )
        if not header_ok:
            return RowOutcome(
                bill_id=base.bill_id,
                bill_public_id=base.bill_public_id,
                status=base.status,
                current_bill_number=base.current_bill_number,
                proposed_memo=None,
                outcome="skipped_no_read",
            )

        read_bill_number = _field_value(fields, "bill_number")
        entered_by = await _resolve_entered_by_for_memo(candidate.entered_by_user_id)
        memo = compose_bill_memo(
            entered_by=entered_by,
            method="PDF drop",
            long_description=_field_value(fields, "long_description"),
            reference=_field_value(fields, "reference"),
            read_bill_number=read_bill_number,
            current_bill_number=candidate.bill_number,
        )
        return RowOutcome(
            bill_id=base.bill_id,
            bill_public_id=base.bill_public_id,
            status=base.status,
            current_bill_number=base.current_bill_number,
            proposed_memo=memo,
            outcome="would_write",
        )
    except Exception as error:
        return RowOutcome(
            bill_id=base.bill_id,
            bill_public_id=base.bill_public_id,
            status=base.status,
            current_bill_number=base.current_bill_number,
            proposed_memo=None,
            outcome=f"error:{type(error).__name__}",
        )


def apply_memo_update(conn, *, bill_id: int, bill_public_id: str, status: str, memo: str) -> int:
    """Guarded: same bill (Id AND PublicId), memo still empty, status unchanged and
    in_review/draft, and the bill really is a list drop - a stale or edited plan can
    never write elsewhere."""
    cur = conn.cursor()
    cur.execute(UPDATE_MEMO_SQL.strip(), (memo, bill_id, bill_public_id, status))
    rowcount = int(cur.rowcount)
    conn.commit()
    return rowcount


def _row_to_csv_cells(row: RowOutcome) -> list[Any]:
    return [
        row.bill_id,
        row.bill_public_id,
        row.status,
        row.current_bill_number or "",
        row.proposed_memo or "",
        row.outcome,
    ]


class _StreamingCsvWriter:
    def __init__(self, path: str) -> None:
        self._fh: TextIO = open(path, "w", newline="", encoding="utf-8")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(CSV_COLUMNS)
        self._fh.flush()

    def write_row(self, row: RowOutcome) -> None:
        self._writer.writerow(_row_to_csv_cells(row))
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _validate_review_csv_header(fieldnames: Optional[list[str]]) -> Optional[str]:
    if not fieldnames:
        return "missing CSV header"
    missing = [c for c in CSV_COLUMNS if c not in fieldnames]
    if missing:
        return f"missing columns: {', '.join(missing)}"
    return None


def _tally_outcomes(rows: list[RowOutcome]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    return counts


def print_summary(*, candidates: int, counts: dict[str, int]) -> None:
    print(f"candidates={candidates}")
    for key in sorted(counts):
        print(f"{key}={counts[key]}")


async def run_backfill(
    candidates: list[CandidateRow],
    *,
    out_path: str,
) -> tuple[list[RowOutcome], bool]:
    """Dry-run only. Returns (rows, daily_cap_hit)."""
    outcomes: list[RowOutcome] = []
    daily_cap_hit = False
    writer = _StreamingCsvWriter(out_path)
    try:
        for candidate in candidates:
            row = await process_candidate(candidate)
            if row.stop_daily_cap:
                daily_cap_hit = True
                row = RowOutcome(
                    bill_id=row.bill_id,
                    bill_public_id=row.bill_public_id,
                    status=row.status,
                    current_bill_number=row.current_bill_number,
                    proposed_memo=None,
                    outcome="stopped_daily_cap",
                    stop_daily_cap=True,
                )
                writer.write_row(row)
                outcomes.append(row)
                break
            writer.write_row(row)
            outcomes.append(row)
    finally:
        writer.close()
    return outcomes, daily_cap_hit


def apply_from_reviewed_csv(*, reviewed_path: str, out_path: str) -> tuple[list[RowOutcome], int]:
    """Apply rows with outcome would_write from a dry-run CSV. No extraction or LLM."""
    header_err = None
    if os.path.realpath(reviewed_path) == os.path.realpath(out_path):
        raise SystemExit("--out must differ from --apply-from (it would overwrite the reviewed plan)")
    # utf-8-sig: a plan re-saved by Excel gains a BOM.
    with open(reviewed_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header_err = _validate_review_csv_header(
            list(reader.fieldnames) if reader.fieldnames else None
        )
        if header_err:
            return [], 2

        outcomes: list[RowOutcome] = []
        writer = _StreamingCsvWriter(out_path)
        try:
            with get_connection() as conn:
                for raw in reader:
                    if (raw.get("outcome") or "").strip() != "would_write":
                        continue
                    memo = raw.get("proposed_memo") or ""
                    if not memo.strip():
                        continue
                    try:
                        bill_id = int(raw["bill_id"])
                        status = str(raw["status"])
                    except (KeyError, ValueError, TypeError):
                        row = RowOutcome(
                            bill_id=0,
                            bill_public_id=str(raw.get("bill_public_id") or ""),
                            status=str(raw.get("status") or ""),
                            current_bill_number=raw.get("current_bill_number") or None,
                            proposed_memo=memo or None,
                            outcome="error:ValueError",
                        )
                        writer.write_row(row)
                        outcomes.append(row)
                        continue

                    row = RowOutcome(
                        bill_id=bill_id,
                        bill_public_id=str(raw.get("bill_public_id") or ""),
                        status=status,
                        current_bill_number=raw.get("current_bill_number") or None,
                        proposed_memo=memo,
                        outcome="would_write",
                    )
                    try:
                        n = apply_memo_update(
                            conn,
                            bill_id=row.bill_id,
                            bill_public_id=row.bill_public_id,
                            status=row.status,
                            memo=memo,
                        )
                        row.outcome = "written" if n == 1 else "skipped_changed"
                    except Exception as error:
                        row.outcome = f"error:{type(error).__name__}"
                    writer.write_row(row)
                    outcomes.append(row)
        finally:
            writer.close()
    return outcomes, 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        required=True,
        help="CSV output path (dry-run plan or apply result audit)",
    )
    parser.add_argument(
        "--apply-from",
        dest="apply_from",
        metavar="REVIEWED.csv",
        help="Apply Memo from a prior dry-run CSV (would_write rows only)",
    )
    parser.add_argument(
        "--include-status",
        action="append",
        choices=["draft"],
        default=[],
        help="Also include draft bills (default: in_review only)",
    )
    parser.add_argument(
        "--bill-id",
        action="append",
        type=int,
        default=[],
        help="Restrict to specific bill Id (repeatable)",
    )
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args(argv)

    assert_cli_system_admin()

    if args.apply_from:
        outcomes, code = apply_from_reviewed_csv(
            reviewed_path=args.apply_from,
            out_path=args.out,
        )
        if code != 0:
            print(f"error={code}", file=sys.stderr)
            return code
        counts = _tally_outcomes(outcomes)
        print_summary(candidates=len(outcomes), counts=counts)
        return 0

    statuses = _statuses_from_args(args.include_status)
    bill_ids = args.bill_id or None
    limit = max(1, int(args.limit))

    with get_connection() as conn:
        candidates = list_candidates(
            conn,
            statuses=statuses,
            bill_ids=bill_ids,
            limit=limit,
        )

    outcomes, daily_cap_hit = asyncio.run(run_backfill(candidates, out_path=args.out))

    counts = _tally_outcomes(outcomes)
    print_summary(candidates=len(candidates), counts=counts)

    if daily_cap_hit:
        print("stopped_reason=daily_cap", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
