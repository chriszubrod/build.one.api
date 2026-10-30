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
from shared.authz import current_user_id
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


    # -- b2: review API ------------------------------------------------------------------------- #

    def read_runs_for_review(self) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadAttachmentBackfillRunsForReview", params={})
                return _all(cursor)
        except Exception as error:
            raise map_database_error(error)

    def read_run_facets(self, *, run_public_id: str) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor, name="ReadAttachmentBackfillRunFacets", params={"RunPublicId": run_public_id}
                )
                return _all(cursor)
        except Exception as error:
            raise map_database_error(error)

    def read_queue(
        self,
        *,
        run_public_id: str,
        evidence_group: Optional[str] = None,
        project_public_id: Optional[str] = None,
        no_project: bool = False,
        bill_year: Optional[int] = None,
        no_bill_year: bool = False,
        review_status: Optional[str] = None,
        after_public_id: Optional[str] = None,
        top: int = 50,
    ) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAttachmentBackfillQueue",
                    params={
                        "RunPublicId": run_public_id,
                        "EvidenceGroup": evidence_group,
                        "ProjectPublicId": project_public_id,
                        "NoProject": _bit(no_project),
                        "BillYear": bill_year,
                        "NoBillYear": _bit(no_bill_year),
                        "ReviewStatus": review_status,
                        "AfterPublicId": after_public_id,
                        "Top": top,
                    },
                )
                rows = _all(cursor)
        except Exception as error:
            raise map_database_error(error)
        for row in rows:
            row["TotalAmount"] = to_decimal_or_none(row.get("TotalAmount"))
        return rows

    def read_candidate_detail(self, *, public_id: str) -> Optional[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor, name="ReadAttachmentBackfillCandidateDetail", params={"PublicId": public_id}
                )
                row = _first(cursor)
        except Exception as error:
            raise map_database_error(error)
        if row is not None:
            row["TotalAmount"] = to_decimal_or_none(row.get("TotalAmount"))
        return row

    def update_candidate_review(
        self,
        *,
        public_id: str,
        decision: str,
        reject_reason: Optional[str],
        row_version: bytes,
        reviewed_by_user_id: int,
    ) -> Optional[dict]:
        """Updated review columns; a refusal is a row with Id None and a RefusedReason."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateAttachmentBackfillCandidateReview",
                    params={
                        "PublicId": public_id,
                        "Decision": decision,
                        "RejectReason": reject_reason,
                        "RowVersion": row_version,
                        "ReviewedByUserId": reviewed_by_user_id,
                    },
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def undo_candidate_review(
        self, *, public_id: str, row_version: bytes, undone_by_user_id: Optional[int] = None
    ) -> Optional[dict]:
        """Review columns back to pending; a refusal is a row with Id None and a RefusedReason.

        The proc records UndoneByUserId/UndoneDatetime (b4 audit trail). The actor is the explicit argument, else the
        request's authenticated user (shared.authz current_user_id, set by the auth dependency) -- the same ContextVar
        threading other repos use for ActorUserId.
        """
        actor = undone_by_user_id if undone_by_user_id is not None else current_user_id.get()
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UndoAttachmentBackfillCandidateReview",
                    params={"PublicId": public_id, "RowVersion": row_version, "UndoneByUserId": actor},
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)


    # -- b4: apply / undo (b4_apply.sql) -------------------------------------------------------- #

    def read_database_size(self) -> Optional[dict]:
        """{UsedBytes, MaxSizeBytes}; MaxSizeBytes None when the cap cannot be determined."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadAttachmentBackfillDatabaseSize", params={})
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def read_candidates_for_apply(self, *, run_id: int, after_id: int = 0, top: int = 100) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAttachmentBackfillCandidatesForApply",
                    params={"RunId": run_id, "AfterId": after_id, "Top": top},
                )
                return _all(cursor)
        except Exception as error:
            raise map_database_error(error)

    def apply_candidate_link(
        self,
        *,
        candidate_id: int,
        row_version: bytes,
        attachment_id: int,
        attachment_created: bool,
        created_by_user_id: Optional[int] = None,
    ) -> Optional[dict]:
        """One atomic link write; the row's RefusedReason is None when linked (b4_apply.sql documents the order)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ApplyAttachmentBackfillCandidateLink",
                    params={
                        "CandidateId": candidate_id,
                        "RowVersion": row_version,
                        "AttachmentId": attachment_id,
                        "AttachmentCreated": _bit(attachment_created),
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def read_linked_candidates(self, *, run_id: int, after_id: int = 0, top: int = 100) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAttachmentBackfillLinkedCandidates",
                    params={"RunId": run_id, "AfterId": after_id, "Top": top},
                )
                return _all(cursor)
        except Exception as error:
            raise map_database_error(error)

    def undo_candidate_link(self, *, candidate_id: int) -> Optional[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor, name="UndoAttachmentBackfillCandidateLink", params={"CandidateId": candidate_id}
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def delete_created_attachment(self, *, attachment_id: int, file_hash: str) -> Optional[dict]:
        """{Deleted, BlobUrl, ExtractedTextBlobUrl, RefusedReason}; blobs are the caller's to delete after this commits."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteAttachmentBackfillCreatedAttachment",
                    params={"AttachmentId": attachment_id, "FileHash": file_hash},
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def close_run_if_done(self, *, run_id: int) -> Optional[dict]:
        """The run row when it moved 'ready' -> 'applied', else None."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="CloseAttachmentBackfillRunIfDone", params={"RunId": run_id})
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def read_attachments_by_hash(self, *, file_hash: str) -> list[dict]:
        """Every Attachment row with this hash, lowest Id first, with the reuse facts (IsArchived, Status, ...)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor, name="ReadAttachmentBackfillAttachmentsByHash", params={"FileHash": file_hash}
                )
                return _all(cursor)
        except Exception as error:
            raise map_database_error(error)

    def register_created_attachment(
        self, *, run_id: int, candidate_id: Optional[int], attachment_id: int, file_hash: str, blob_url: Optional[str]
    ) -> Optional[dict]:
        """Record an attachment the apply created (the only kind the delete proc will remove). Idempotent."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RegisterAttachmentBackfillCreatedAttachment",
                    params={
                        "RunId": run_id,
                        "CandidateId": candidate_id,
                        "AttachmentId": attachment_id,
                        "FileHash": file_hash,
                        "BlobUrl": blob_url,
                    },
                )
                return _first(cursor)
        except Exception as error:
            raise map_database_error(error)

    def read_created_attachments_to_sweep(self, *, run_id: int) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor, name="ReadAttachmentBackfillCreatedAttachmentsToSweep", params={"RunId": run_id}
                )
                return _all(cursor)
        except Exception as error:
            raise map_database_error(error)
