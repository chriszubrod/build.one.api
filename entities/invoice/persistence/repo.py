# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.invoice.business.model import Invoice
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


def _bit(flag):
    """SQL Server BIT params take 0/1, not Python bool."""
    if flag is None:
        return None
    return 1 if flag else 0


def _partition_bindings_by_source_type(
    bindings: list[tuple[str, int]],
) -> tuple[list[int], list[int], list[int]]:
    bill_ids = [sid for st, sid in bindings if st == "BillLineItem"]
    expense_ids = [sid for st, sid in bindings if st == "ExpenseLineItem"]
    credit_ids = [sid for st, sid in bindings if st == "BillCreditLineItem"]
    return bill_ids, expense_ids, credit_ids


class InvoiceRepository:
    """
    Repository for Invoice persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Invoice]:
        if not row:
            return None

        try:
            return Invoice(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                project_id=getattr(row, "ProjectId", None),
                payment_term_id=getattr(row, "PaymentTermId", None),
                invoice_date=getattr(row, "InvoiceDate", None),
                due_date=getattr(row, "DueDate", None),
                invoice_number=getattr(row, "InvoiceNumber", None),
                total_amount=Decimal(str(getattr(row, "TotalAmount", None))) if getattr(row, "TotalAmount", None) is not None else None,
                memo=getattr(row, "Memo", None),
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during invoice mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during invoice mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, project_id: Optional[int] = None, payment_term_id: Optional[int] = None, invoice_date: Optional[str] = None, due_date: Optional[str] = None, invoice_number: Optional[str] = None, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True, created_by_user_id: Optional[int] = None) -> Invoice:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateInvoice",
                    params={
                        "ProjectId": project_id,
                        "PaymentTermId": payment_term_id,
                        "InvoiceDate": invoice_date,
                        "DueDate": due_date,
                        "InvoiceNumber": invoice_number,
                        "TotalAmount": Decimal(str(total_amount)) if total_amount is not None else None,
                        "Memo": memo,
                        "IsDraft": 1 if is_draft else 0,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateInvoice did not return a row.")
                    raise map_database_error(Exception("CreateInvoice failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create invoice: {error}")
            raise map_database_error(error)

    def read_all(
        self,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list[Invoice]:
        """Read invoices, scoped by UserProject for non-admin actors."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadInvoices",
                    params={
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all invoices: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceById", params={"Id": id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice by public ID: {error}")
            raise map_database_error(error)

    def read_by_invoice_number(self, invoice_number: str) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceByInvoiceNumber", params={"InvoiceNumber": invoice_number})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice by invoice number: {error}")
            raise map_database_error(error)

    def read_by_invoice_number_and_project_id(self, invoice_number: str, project_id: int) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadInvoiceByInvoiceNumberAndProjectId",
                    params={"InvoiceNumber": invoice_number, "ProjectId": project_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice by invoice number and project ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, invoice: Invoice) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": invoice.id,
                    "RowVersion": invoice.row_version_bytes,
                    "ProjectId": invoice.project_id,
                    "PaymentTermId": invoice.payment_term_id,
                    "InvoiceDate": invoice.invoice_date,
                    "DueDate": invoice.due_date,
                    "InvoiceNumber": invoice.invoice_number,
                    "TotalAmount": Decimal(str(invoice.total_amount)) if invoice.total_amount is not None else None,
                    "Memo": invoice.memo,
                }
                if invoice.is_draft is not None:
                    params["IsDraft"] = 1 if invoice.is_draft else 0

                call_procedure(cursor=cursor, name="UpdateInvoiceById", params=params)
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateInvoiceById returned no row (id=%s); possible row-version conflict.",
                        invoice.id,
                    )
                    raise map_database_error(
                        Exception("Update did not match any row; the invoice may have been modified by another process.")
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update invoice by ID: {error}")
            raise map_database_error(error)

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        project_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        sort_by: str = "InvoiceDate",
        sort_direction: str = "DESC",
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list[Invoice]:
        """Read invoices with pagination + filters, scoped by UserProject."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PageNumber": page_number,
                    "PageSize": page_size,
                    "SearchTerm": search_term,
                    "ProjectId": project_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                    "SortBy": sort_by,
                    "SortDirection": sort_direction,
                    "ActorUserId": actor_user_id,
                    "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                }
                call_procedure(cursor=cursor, name="ReadInvoicesPaginated", params=params)
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read paginated invoices: {error}")
            raise map_database_error(error)

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        project_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> int:
        """Count invoices matching filter criteria, scoped by UserProject."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "SearchTerm": search_term,
                    "ProjectId": project_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                    "ActorUserId": actor_user_id,
                    "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                }
                call_procedure(cursor=cursor, name="CountInvoices", params=params)
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count invoices: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="DeleteInvoiceById", params={"Id": id})
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete invoice by ID: {error}")
            raise map_database_error(error)

    def read_source_link_lines(self, invoice_id: int) -> list:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadInvoiceSourceLinkLines",
                    params={"InvoiceId": invoice_id},
                )
                return cursor.fetchall()
        except Exception as error:
            logger.error(f"Error during ReadInvoiceSourceLinkLines: {error}")
            raise map_database_error(error)

    def propose_invoice_source_links(self, invoice_id: int) -> list:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ProposeInvoiceSourceLinks",
                    params={"InvoiceId": invoice_id},
                )
                return cursor.fetchall()
        except Exception as error:
            logger.error(f"Error during ProposeInvoiceSourceLinks: {error}")
            raise map_database_error(error)

    def read_source_line_coverage(
        self,
        bindings: list[tuple[str, int]],
    ) -> dict[tuple[str, int], dict]:
        """Attachment count + SubCostCodeId per (SourceType, source_line_item_id) (read-only).

        Inline read SQL with dynamic IN-list (no sproc/TVP), same convention as
        entities.invoice.business.enrichment.enrich_line_items — intentional; do not convert.
        """
        if not bindings:
            return {}
        bill_ids, expense_ids, credit_ids = _partition_bindings_by_source_type(bindings)
        out: dict[tuple[str, int], dict] = {}
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                if bill_ids:
                    placeholders = ",".join("?" for _ in bill_ids)
                    cursor.execute(
                        f"""
                        SELECT
                            bli.[Id],
                            bli.[SubCostCodeId],
                            (
                                SELECT COUNT(*)
                                FROM dbo.[BillLineItemAttachment] blia
                                WHERE blia.[BillLineItemId] = bli.[Id]
                            ) AS [AttachmentCount]
                        FROM dbo.[BillLineItem] bli
                        WHERE bli.[Id] IN ({placeholders})
                        """,
                        *bill_ids,
                    )
                    for row in cursor.fetchall():
                        out[("BillLineItem", row.Id)] = {
                            "attachment_count": int(row.AttachmentCount or 0),
                            "sub_cost_code_id": getattr(row, "SubCostCodeId", None),
                        }
                if expense_ids:
                    placeholders = ",".join("?" for _ in expense_ids)
                    cursor.execute(
                        f"""
                        SELECT
                            eli.[Id],
                            eli.[SubCostCodeId],
                            (
                                SELECT COUNT(*)
                                FROM dbo.[ExpenseLineItemAttachment] elia
                                WHERE elia.[ExpenseLineItemId] = eli.[Id]
                            ) AS [AttachmentCount]
                        FROM dbo.[ExpenseLineItem] eli
                        WHERE eli.[Id] IN ({placeholders})
                        """,
                        *expense_ids,
                    )
                    for row in cursor.fetchall():
                        out[("ExpenseLineItem", row.Id)] = {
                            "attachment_count": int(row.AttachmentCount or 0),
                            "sub_cost_code_id": getattr(row, "SubCostCodeId", None),
                        }
                if credit_ids:
                    placeholders = ",".join("?" for _ in credit_ids)
                    cursor.execute(
                        f"""
                        SELECT
                            bcli.[Id],
                            bcli.[SubCostCodeId],
                            (
                                SELECT COUNT(*)
                                FROM dbo.[BillCreditLineItemAttachment] bclia
                                WHERE bclia.[BillCreditLineItemId] = bcli.[Id]
                            ) AS [AttachmentCount]
                        FROM dbo.[BillCreditLineItem] bcli
                        WHERE bcli.[Id] IN ({placeholders})
                        """,
                        *credit_ids,
                    )
                    for row in cursor.fetchall():
                        out[("BillCreditLineItem", row.Id)] = {
                            "attachment_count": int(row.AttachmentCount or 0),
                            "sub_cost_code_id": getattr(row, "SubCostCodeId", None),
                        }
            return out
        except Exception as error:
            logger.error(f"Error during read source line coverage: {error}")
            raise map_database_error(error)

    def read_duplicate_projects_by_project_id(self, project_id: int) -> list:
        """Step 1b — other dbo.Project rows with the same Name (read-only).

        Inline read SQL with dynamic IN-list (no sproc/TVP), same convention as
        entities.invoice.business.enrichment.enrich_line_items — intentional; do not convert.
        """
        sql = """
            SELECT
                p2.[Id],
                p2.[Name],
                p2.[Abbreviation],
                CONVERT(VARCHAR(19), p2.[CreatedDatetime], 120) AS [CreatedDatetime],
                (
                    SELECT COUNT(*)
                    FROM qbo.[CustomerProject] cp
                    WHERE cp.[ProjectId] = p2.[Id]
                ) AS [QboMappings]
            FROM dbo.[Project] p1
            INNER JOIN dbo.[Project] p2
                ON p2.[Name] = p1.[Name]
                AND p2.[Id] <> p1.[Id]
            WHERE p1.[Id] = ?
            ORDER BY p2.[Id] ASC
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, project_id)
                return cursor.fetchall()
        except Exception as error:
            logger.error(f"Error during duplicate project screen: {error}")
            raise map_database_error(error)

    def read_vendor_ids_for_source_lines(
        self,
        bindings: list[tuple[str, int]],
    ) -> dict[tuple[str, int], int]:
        """Resolve VendorId for (SourceType, source_line_item_id) pairs (read-only).

        Inline read SQL with dynamic IN-list (no sproc/TVP), same convention as
        entities.invoice.business.enrichment.enrich_line_items — intentional; do not convert.
        """
        if not bindings:
            return {}
        bill_ids, expense_ids, credit_ids = _partition_bindings_by_source_type(bindings)
        out: dict[tuple[str, int], int] = {}
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                if bill_ids:
                    placeholders = ",".join("?" for _ in bill_ids)
                    cursor.execute(
                        f"""
                        SELECT bli.[Id], b.[VendorId]
                        FROM dbo.[BillLineItem] bli
                        INNER JOIN dbo.[Bill] b ON b.[Id] = bli.[BillId]
                        WHERE bli.[Id] IN ({placeholders})
                        """,
                        *bill_ids,
                    )
                    for row in cursor.fetchall():
                        out[("BillLineItem", row.Id)] = row.VendorId
                if expense_ids:
                    placeholders = ",".join("?" for _ in expense_ids)
                    cursor.execute(
                        f"""
                        SELECT eli.[Id], e.[VendorId]
                        FROM dbo.[ExpenseLineItem] eli
                        INNER JOIN dbo.[Expense] e ON e.[Id] = eli.[ExpenseId]
                        WHERE eli.[Id] IN ({placeholders})
                        """,
                        *expense_ids,
                    )
                    for row in cursor.fetchall():
                        out[("ExpenseLineItem", row.Id)] = row.VendorId
                if credit_ids:
                    placeholders = ",".join("?" for _ in credit_ids)
                    cursor.execute(
                        f"""
                        SELECT bcli.[Id], bc.[VendorId]
                        FROM dbo.[BillCreditLineItem] bcli
                        INNER JOIN dbo.[BillCredit] bc ON bc.[Id] = bcli.[BillCreditId]
                        WHERE bcli.[Id] IN ({placeholders})
                        """,
                        *credit_ids,
                    )
                    for row in cursor.fetchall():
                        out[("BillCreditLineItem", row.Id)] = row.VendorId
            return out
        except Exception as error:
            logger.error(f"Error during read vendor ids for source lines: {error}")
            raise map_database_error(error)

    # (SourceType, line_table, link_table, link_column) for the three linkable
    # source-line families — the hop from a source line to its dbo.Attachment(s).
    _SOURCE_ATTACHMENT_SPECS = (
        ("BillLineItem", "BillLineItem", "BillLineItemAttachment", "BillLineItemId"),
        ("ExpenseLineItem", "ExpenseLineItem", "ExpenseLineItemAttachment", "ExpenseLineItemId"),
        ("BillCreditLineItem", "BillCreditLineItem", "BillCreditLineItemAttachment", "BillCreditLineItemId"),
    )

    def read_vendor_invoice_numbers_for_source_lines(
        self,
        bindings: list[tuple[str, int]],
    ) -> dict[tuple[str, int], str]:
        """U-187 — resolve the sync-proof VendorInvoiceNumber per
        (SourceType, source_line_item_id), hopping source line → *LineItemAttachment
        → dbo.Attachment (read-only). One number per line (MAX over its attachments,
        NULLs/blank ignored) — the KI-41 hard double-bill cross-compare signal.

        Inline read SQL with dynamic IN-list (no sproc/TVP), same convention as
        read_vendor_ids_for_source_lines / enrich_line_items — intentional; do not convert.
        (Table/column names below are fixed literals from _SOURCE_ATTACHMENT_SPECS,
        never caller input — no injection surface.)
        """
        if not bindings:
            return {}
        bill_ids, expense_ids, credit_ids = _partition_bindings_by_source_type(bindings)
        by_type = {
            "BillLineItem": bill_ids,
            "ExpenseLineItem": expense_ids,
            "BillCreditLineItem": credit_ids,
        }
        out: dict[tuple[str, int], str] = {}
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                for source_type, line_table, link_table, link_col in self._SOURCE_ATTACHMENT_SPECS:
                    ids = by_type.get(source_type) or []
                    if not ids:
                        continue
                    placeholders = ",".join("?" for _ in ids)
                    cursor.execute(
                        f"""
                        SELECT li.[Id] AS [LineId],
                               MAX(a.[VendorInvoiceNumber]) AS [VendorInvoiceNumber]
                        FROM dbo.[{line_table}] li
                        INNER JOIN dbo.[{link_table}] lnk ON lnk.[{link_col}] = li.[Id]
                        INNER JOIN dbo.[Attachment] a ON a.[Id] = lnk.[AttachmentId]
                        WHERE li.[Id] IN ({placeholders})
                          -- Completed extractions only: a 'pending'/'failed' row is a
                          -- requeued/changed doc whose stored number may be stale, and
                          -- this reader feeds the HARD KI-41 double-bill halt (U-187 P1).
                          AND a.[ExtractionStatus] = 'completed'
                          AND a.[VendorInvoiceNumber] IS NOT NULL
                          AND LTRIM(RTRIM(a.[VendorInvoiceNumber])) <> ''
                        GROUP BY li.[Id]
                        """,
                        *ids,
                    )
                    for row in cursor.fetchall():
                        if row.VendorInvoiceNumber:
                            out[(source_type, row.LineId)] = row.VendorInvoiceNumber
            return out
        except Exception as error:
            logger.error(f"Error during read vendor invoice numbers for source lines: {error}")
            raise map_database_error(error)

    def read_extraction_refs_for_source_lines(
        self,
        bindings: list[tuple[str, int]],
    ) -> dict[tuple[str, int], dict]:
        """U-187 (KI-40) — per (SourceType, source_line_item_id), the linked
        dbo.Attachment carrying a completed text extraction: its id, ExtractionStatus,
        and ExtractedTextBlobUrl (read-only). Only attachments with a non-NULL
        ExtractedTextBlobUrl are considered; the highest attachment Id wins per line.

        Inline read SQL, same convention as read_source_line_coverage — intentional.
        """
        if not bindings:
            return {}
        bill_ids, expense_ids, credit_ids = _partition_bindings_by_source_type(bindings)
        by_type = {
            "BillLineItem": bill_ids,
            "ExpenseLineItem": expense_ids,
            "BillCreditLineItem": credit_ids,
        }
        out: dict[tuple[str, int], dict] = {}
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                for source_type, line_table, link_table, link_col in self._SOURCE_ATTACHMENT_SPECS:
                    ids = by_type.get(source_type) or []
                    if not ids:
                        continue
                    placeholders = ",".join("?" for _ in ids)
                    cursor.execute(
                        f"""
                        SELECT lnk.[{link_col}] AS [LineId],
                               a.[Id] AS [AttachmentId],
                               a.[ExtractionStatus] AS [ExtractionStatus],
                               a.[ExtractedTextBlobUrl] AS [ExtractedTextBlobUrl]
                        FROM dbo.[{link_table}] lnk
                        INNER JOIN dbo.[Attachment] a ON a.[Id] = lnk.[AttachmentId]
                        WHERE lnk.[{link_col}] IN ({placeholders})
                          AND a.[ExtractedTextBlobUrl] IS NOT NULL
                        ORDER BY lnk.[{link_col}] ASC, a.[Id] ASC
                        """,
                        *ids,
                    )
                    for row in cursor.fetchall():
                        # ORDER BY a.[Id] ASC → last write per line keeps the highest id.
                        out[(source_type, row.LineId)] = {
                            "attachment_id": row.AttachmentId,
                            "extraction_status": row.ExtractionStatus,
                            "extracted_text_blob_url": row.ExtractedTextBlobUrl,
                        }
            return out
        except Exception as error:
            logger.error(f"Error during read extraction refs for source lines: {error}")
            raise map_database_error(error)

    def read_other_project_tokens(self, project_id: int) -> list[str]:
        """U-187 (KI-40) — distinct abbreviations of every OTHER project, the
        foreign-project token set for the multi-page attachment leak scan (read-only)."""
        sql = """
            SELECT DISTINCT [Abbreviation]
            FROM dbo.[Project]
            WHERE [Id] <> ?
              AND [Abbreviation] IS NOT NULL
              AND LTRIM(RTRIM([Abbreviation])) <> ''
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, project_id)
                return [row.Abbreviation for row in cursor.fetchall() if row.Abbreviation]
        except Exception as error:
            logger.error(f"Error during read other project tokens: {error}")
            raise map_database_error(error)

    def read_source_lines_missing_readable_blob(self, invoice_id: int) -> list:
        """Source-linked ILIs with no attachment carrying a non-NULL BlobUrl (read-only).

        Inline read SQL (no sproc/TVP), same convention as
        read_source_line_coverage / enrich_line_items — intentional; do not convert.
        """
        sql = """
            SELECT
                ili.[Id] AS [InvoiceLineItemId],
                ili.[SourceType],
                ili.[BillLineItemId],
                ili.[ExpenseLineItemId],
                ili.[BillCreditLineItemId]
            FROM dbo.[InvoiceLineItem] ili
            WHERE ili.[InvoiceId] = ?
              AND ili.[SourceType] IN (
                  N'BillLineItem', N'ExpenseLineItem', N'BillCreditLineItem'
              )
              AND (
                  (
                      ili.[SourceType] = N'BillLineItem'
                      AND ili.[BillLineItemId] IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM dbo.[BillLineItemAttachment] blia
                          INNER JOIN dbo.[Attachment] a ON a.[Id] = blia.[AttachmentId]
                          WHERE blia.[BillLineItemId] = ili.[BillLineItemId]
                            AND a.[BlobUrl] IS NOT NULL
                      )
                  )
                  OR (
                      ili.[SourceType] = N'ExpenseLineItem'
                      AND ili.[ExpenseLineItemId] IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM dbo.[ExpenseLineItemAttachment] elia
                          INNER JOIN dbo.[Attachment] a ON a.[Id] = elia.[AttachmentId]
                          WHERE elia.[ExpenseLineItemId] = ili.[ExpenseLineItemId]
                            AND a.[BlobUrl] IS NOT NULL
                      )
                  )
                  OR (
                      ili.[SourceType] = N'BillCreditLineItem'
                      AND ili.[BillCreditLineItemId] IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM dbo.[BillCreditLineItemAttachment] bclia
                          INNER JOIN dbo.[Attachment] a ON a.[Id] = bclia.[AttachmentId]
                          WHERE bclia.[BillCreditLineItemId] = ili.[BillCreditLineItemId]
                            AND a.[BlobUrl] IS NOT NULL
                      )
                  )
              )
            ORDER BY ili.[Id] ASC
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, invoice_id)
                rows = cursor.fetchall()
                return [
                    {
                        "invoice_line_item_id": row.InvoiceLineItemId,
                        "source_type": row.SourceType,
                        "bill_line_item_id": getattr(row, "BillLineItemId", None),
                        "expense_line_item_id": getattr(row, "ExpenseLineItemId", None),
                        "bill_credit_line_item_id": getattr(
                            row, "BillCreditLineItemId", None
                        ),
                    }
                    for row in rows
                ]
        except Exception as error:
            logger.error(
                f"Error during read_source_lines_missing_readable_blob: {error}"
            )
            raise map_database_error(error)

    def compute_invoice_draw_matrix(self, invoice_id: int) -> dict:
        """Aggregate QBO/dbo draw-push invariant counts for one invoice."""
        empty = {
            "qbo_line_count": 0,
            "qbo_total_amt": None,
            "dbo_line_count": 0,
            "dbo_line_sum": None,
            "dbo_total_amount": None,
            "sourced_line_count": 0,
            "billed_source_count": 0,
        }
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ComputeInvoiceDrawMatrix",
                    params={"InvoiceId": invoice_id},
                )
                row = cursor.fetchone()
                if not row:
                    return empty
                return {
                    "qbo_line_count": row.QboLineCount,
                    "qbo_total_amt": row.QboTotalAmt,
                    "dbo_line_count": row.DboLineCount,
                    "dbo_line_sum": row.DboLineSum,
                    "dbo_total_amount": row.DboTotalAmount,
                    "sourced_line_count": row.SourcedLineCount,
                    "billed_source_count": row.BilledSourceCount,
                }
        except Exception as error:
            logger.error(f"Error during ComputeInvoiceDrawMatrix: {error}")
            raise map_database_error(error)

    def backfill_linked_source_project_id(
        self,
        *,
        source_type: str,
        source_line_item_id: int,
        project_id: int,
    ) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="BackfillLinkedSourceProjectId",
                    params={
                        "SourceType": source_type,
                        "Id": source_line_item_id,
                        "ProjectId": project_id,
                    },
                )
        except Exception as error:
            logger.error(f"Error during BackfillLinkedSourceProjectId: {error}")
            raise map_database_error(error)
