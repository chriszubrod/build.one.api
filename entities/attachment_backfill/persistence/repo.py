"""Persistence for dbo.AttachmentBackfillRun / dbo.AttachmentBackfillCandidate (U-535b b1).

Sproc wrappers only. Each call_procedure(...) names its sproc literally so
tests/test_entity_docstrings.py can match them against the service docstring.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from entities.attachment_backfill.business.matcher import BillRow
from shared.api.money import to_decimal_or_none
from shared.database import call_procedure, conn_ctx, get_connection, map_database_error

logger = logging.getLogger(__name__)


def _row_dict(cursor, row) -> Optional[dict]:
    if row is None:
        return None
    columns = [c[0] for c in cursor.description]
    return dict(zip(columns, row))


def _first(cursor) -> Optional[dict]:
    if cursor.description is None:
        return None
    return _row_dict(cursor, cursor.fetchone())


def _all(cursor) -> list[dict]:
    if cursor.description is None:
        return []
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _bit(value: Optional[bool]) -> Optional[int]:
    return None if value is None else int(bool(value))


def _project_ids(csv: Any) -> frozenset[int]:
    if not csv:
        return frozenset()
    return frozenset(int(part) for part in str(csv).split(",") if part.strip())


class AttachmentBackfillRepository:
    @staticmethod
    def connection():
        """One connection for a write loop; callers commit after every row (shared.database.conn_ctx)."""
        return get_connection()

    def read_open_runs(self, *, source: str) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadOpenAttachmentBackfillRuns", params={"Source": source})
                return _all(cursor)
        except Exception as error:
            raise map_database_error(error)

    def create_run(self, *, source: str, notes: Optional[str], created_by_user_id: Optional[int]) -> dict:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAttachmentBackfillRun",
                    params={"Source": source, "Notes": notes, "CreatedByUserId": created_by_user_id},
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def read_run_by_public_id(self, public_id: str) -> Optional[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor, name="ReadAttachmentBackfillRunByPublicId", params={"PublicId": public_id}
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def update_run_status(self, *, run_id: int, status: str) -> Optional[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateAttachmentBackfillRunStatus",
                    params={"Id": run_id, "Status": status},
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def insert_candidate(
        self,
        *,
        run_id: int,
        bill_id: int,
        source_file_id: str,
        source_file_name: str,
        source_folder_id: Optional[str],
        match_tier: str,
        source_file_size: Optional[int] = None,
        conn=None,
    ) -> Optional[dict]:
        """Row with Inserted=True/False; a refusal is a row with Id None and a RefusedReason."""
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="InsertAttachmentBackfillCandidate",
                    params={
                        "RunId": run_id,
                        "BillId": bill_id,
                        "SourceFileId": source_file_id,
                        "SourceFileName": source_file_name,
                        "SourceFolderId": source_folder_id,
                        "SourceFileSize": source_file_size,
                        "MatchTier": match_tier,
                    },
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def delete_stale_candidates(self, *, run_id: int, keep: list[tuple[str, int]], conn=None) -> int:
        """Delete this generating run's pending, unchecked, unlinked candidates not in `keep`; returns the count."""
        keep_json = json.dumps([{"f": str(file_id), "b": int(bill_id)} for file_id, bill_id in keep])
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteStaleAttachmentBackfillCandidates",
                    params={"RunId": run_id, "KeepJson": keep_json},
                )
                row = _first(cursor)
        except Exception as error:
            raise map_database_error(error)
        return int(row["Deleted"]) if row else 0

    def read_candidates_for_verification(
        self, *, run_id: int, top: int, after_id: int = 0, include_errors: bool = False
    ) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAttachmentBackfillCandidatesForVerification",
                    params={
                        "RunId": run_id,
                        "Top": top,
                        "AfterId": after_id,
                        "IncludeErrors": _bit(include_errors),
                    },
                )
                rows = _all(cursor)
        except Exception as error:
            raise map_database_error(error)
        for row in rows:
            row["TotalAmount"] = to_decimal_or_none(row.get("TotalAmount"))
        return rows

    def update_candidate_verification(
        self,
        *,
        candidate_id: int,
        source_sha256: Optional[str],
        check_status: str,
        invoice_seen_count: Optional[int] = None,
        amount_seen_count: Optional[int] = None,
        vendor_seen: Optional[bool] = None,
        other_invoice_label_count: Optional[int] = None,
        statement_like: Optional[bool] = None,
        used_ocr: Optional[bool] = None,
        conn=None,
    ) -> Optional[dict]:
        """Updated row; a refusal is a row with Id None and a RefusedReason (run not ready, reviewed/linked)."""
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateAttachmentBackfillCandidateVerification",
                    params={
                        "Id": candidate_id,
                        "SourceSha256": source_sha256,
                        "CheckStatus": check_status,
                        "InvoiceSeenCount": invoice_seen_count,
                        "AmountSeenCount": amount_seen_count,
                        "VendorSeen": _bit(vendor_seen),
                        "OtherInvoiceLabelCount": other_invoice_label_count,
                        "StatementLike": _bit(statement_like),
                        "UsedOcr": _bit(used_ocr),
                    },
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def read_bills_for_backfill(self, *, company_id: int) -> list[BillRow]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor, name="ReadBillsForAttachmentBackfill", params={"CompanyId": company_id}
                )
                rows = _all(cursor)
        except Exception as error:
            raise map_database_error(error)
        return [
            BillRow(
                id=int(row["Id"]),
                bill_number=row.get("BillNumber"),
                total_amount=to_decimal_or_none(row.get("TotalAmount")),
                project_ids=_project_ids(row.get("ProjectIds")),
            )
            for row in rows
        ]

    def read_run_summary(self, *, run_id: int) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadAttachmentBackfillRunSummary", params={"RunId": run_id})
                return _all(cursor)
        except Exception as error:
            raise map_database_error(error)

