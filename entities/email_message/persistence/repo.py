"""EmailMessage + EmailAttachment repositories.

Both share the same connection pattern as the rest of the entity layer
(stored procedures via pyodbc through `shared.database.call_procedure`).

EmailMessageRepository:
  - upsert (idempotent on GraphMessageId — the poll service relies on this)
  - read_by_id / read_by_public_id / read_by_graph_message_id
  - update_status (cheap state transition; preserves LastError when None)
  - claim_next_pending (atomic 'pending' → 'processing' for the agent runner)
  - read_paginated / count

EmailAttachmentRepository:
  - upsert (idempotent on EmailMessageId + GraphAttachmentId)
  - read_by_email_message_id / read_by_public_id
  - update_extraction (DI result + parsed fields + status)
"""
import base64
import logging
from decimal import Decimal
from typing import Optional

import pyodbc

from entities.email_message.business.model import EmailMessage, EmailAttachment
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class EmailMessageRepository:
    def _from_db(self, row: pyodbc.Row) -> Optional[EmailMessage]:
        if not row:
            return None
        try:
            return EmailMessage(
                id=row.Id,
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                graph_message_id=getattr(row, "GraphMessageId", None),
                internet_message_id=getattr(row, "InternetMessageId", None),
                conversation_id=getattr(row, "ConversationId", None),
                mailbox_address=getattr(row, "MailboxAddress", None),
                from_address=getattr(row, "FromAddress", None),
                from_name=getattr(row, "FromName", None),
                to_recipients=getattr(row, "ToRecipients", None),
                cc_recipients=getattr(row, "CcRecipients", None),
                subject=getattr(row, "Subject", None),
                body_preview=getattr(row, "BodyPreview", None),
                body_content=getattr(row, "BodyContent", None),
                body_content_type=getattr(row, "BodyContentType", None),
                received_datetime=getattr(row, "ReceivedDatetime", None),
                processing_status=getattr(row, "ProcessingStatus", None),
                last_error=getattr(row, "LastError", None),
                agent_session_id=getattr(row, "AgentSessionId", None),
                web_link=getattr(row, "WebLink", None),
                has_attachments=bool(getattr(row, "HasAttachments", False)),
            )
        except Exception as error:
            logger.error(f"EmailMessage mapping error: {error}")
            raise map_database_error(error)

    def upsert(self, *, graph_message_id: str, mailbox_address: str,
               internet_message_id: Optional[str] = None,
               conversation_id: Optional[str] = None,
               from_address: Optional[str] = None,
               from_name: Optional[str] = None,
               to_recipients: Optional[str] = None,
               cc_recipients: Optional[str] = None,
               subject: Optional[str] = None,
               body_preview: Optional[str] = None,
               body_content: Optional[str] = None,
               body_content_type: Optional[str] = None,
               received_datetime: Optional[str] = None,
               web_link: Optional[str] = None,
               has_attachments: bool = False) -> EmailMessage:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="UpsertEmailMessage", params={
                    "GraphMessageId": graph_message_id,
                    "InternetMessageId": internet_message_id,
                    "ConversationId": conversation_id,
                    "MailboxAddress": mailbox_address,
                    "FromAddress": from_address,
                    "FromName": from_name,
                    "ToRecipients": to_recipients,
                    "CcRecipients": cc_recipients,
                    "Subject": subject,
                    "BodyPreview": body_preview,
                    "BodyContent": body_content,
                    "BodyContentType": body_content_type,
                    "ReceivedDatetime": received_datetime,
                    "WebLink": web_link,
                    "HasAttachments": 1 if has_attachments else 0,
                })
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("UpsertEmailMessage returned no row"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error upserting email message: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[EmailMessage]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmailMessageById", params={"Id": id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading email message by id: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[EmailMessage]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmailMessageByPublicId", params={"PublicId": public_id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading email message by public_id: {error}")
            raise map_database_error(error)

    def read_by_graph_message_id(self, graph_message_id: str) -> Optional[EmailMessage]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmailMessageByGraphMessageId",
                               params={"GraphMessageId": graph_message_id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading email message by graph_message_id: {error}")
            raise map_database_error(error)

    def update_status(self, *, id: int, processing_status: str,
                      last_error: Optional[str] = None,
                      agent_session_id: Optional[int] = None) -> Optional[EmailMessage]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="UpdateEmailMessageStatus", params={
                    "Id": id,
                    "ProcessingStatus": processing_status,
                    "LastError": last_error,
                    "AgentSessionId": agent_session_id,
                })
                row = cursor.fetchone()
                if not row:
                    return None
                return EmailMessage(
                    id=row.Id,
                    public_id=str(row.PublicId) if row.PublicId else None,
                    row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                    processing_status=row.ProcessingStatus,
                    last_error=row.LastError,
                    agent_session_id=row.AgentSessionId,
                )
        except Exception as error:
            logger.error(f"Error updating email message status: {error}")
            raise map_database_error(error)

    def claim_next_pending(self) -> Optional[EmailMessage]:
        """Atomically transition the oldest pending email to 'processing'.

        Returns the claimed email, or None if nothing is pending. Safe to
        call from concurrent workers (UPDLOCK + READPAST in the sproc).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ClaimNextPendingEmailMessage", params={})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error claiming pending email message: {error}")
            raise map_database_error(error)

    def read_paginated(self, *, page_number: int = 1, page_size: int = 50,
                       search_term: Optional[str] = None,
                       processing_status: Optional[str] = None,
                       start_date: Optional[str] = None,
                       end_date: Optional[str] = None,
                       sort_by: str = "ReceivedDatetime",
                       sort_direction: str = "DESC") -> list[EmailMessage]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmailMessagesPaginated", params={
                    "PageNumber": page_number,
                    "PageSize": page_size,
                    "SearchTerm": search_term,
                    "ProcessingStatus": processing_status,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "SortBy": sort_by,
                    "SortDirection": sort_direction,
                })
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error reading email messages paginated: {error}")
            raise map_database_error(error)

    def count(self, *, search_term: Optional[str] = None,
              processing_status: Optional[str] = None,
              start_date: Optional[str] = None,
              end_date: Optional[str] = None) -> int:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="CountEmailMessages", params={
                    "SearchTerm": search_term,
                    "ProcessingStatus": processing_status,
                    "StartDate": start_date,
                    "EndDate": end_date,
                })
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error counting email messages: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> bool:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="DeleteEmailMessageById", params={"Id": id})
                row = cursor.fetchone()
                return row is not None
        except Exception as error:
            logger.error(f"Error deleting email message: {error}")
            raise map_database_error(error)


class EmailAttachmentRepository:
    def _from_db(self, row: pyodbc.Row) -> Optional[EmailAttachment]:
        if not row:
            return None
        try:
            return EmailAttachment(
                id=row.Id,
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                email_message_id=getattr(row, "EmailMessageId", None),
                graph_attachment_id=getattr(row, "GraphAttachmentId", None),
                filename=getattr(row, "Filename", None),
                content_type=getattr(row, "ContentType", None),
                size_bytes=getattr(row, "SizeBytes", None),
                is_inline=bool(getattr(row, "IsInline", False)),
                blob_uri=getattr(row, "BlobUri", None),
                extraction_status=getattr(row, "ExtractionStatus", None),
                extracted_at=getattr(row, "ExtractedAt", None),
                di_model=getattr(row, "DiModel", None),
                di_result_json=getattr(row, "DiResultJson", None),
                di_confidence=Decimal(str(row.DiConfidence)) if getattr(row, "DiConfidence", None) is not None else None,
                di_vendor_name=getattr(row, "DiVendorName", None),
                di_invoice_number=getattr(row, "DiInvoiceNumber", None),
                di_invoice_date=getattr(row, "DiInvoiceDate", None),
                di_due_date=getattr(row, "DiDueDate", None),
                di_subtotal=Decimal(str(row.DiSubtotal)) if getattr(row, "DiSubtotal", None) is not None else None,
                di_total_amount=Decimal(str(row.DiTotalAmount)) if getattr(row, "DiTotalAmount", None) is not None else None,
                di_currency=getattr(row, "DiCurrency", None),
                last_error=getattr(row, "LastError", None),
            )
        except Exception as error:
            logger.error(f"EmailAttachment mapping error: {error}")
            raise map_database_error(error)

    def upsert(self, *, email_message_id: int, graph_attachment_id: str,
               filename: str, content_type: Optional[str] = None,
               size_bytes: Optional[int] = None, is_inline: bool = False,
               blob_uri: Optional[str] = None) -> EmailAttachment:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="UpsertEmailAttachment", params={
                    "EmailMessageId": email_message_id,
                    "GraphAttachmentId": graph_attachment_id,
                    "Filename": filename,
                    "ContentType": content_type,
                    "SizeBytes": size_bytes,
                    "IsInline": 1 if is_inline else 0,
                    "BlobUri": blob_uri,
                })
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("UpsertEmailAttachment returned no row"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error upserting email attachment: {error}")
            raise map_database_error(error)

    def read_by_email_message_id(self, email_message_id: int) -> list[EmailAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmailAttachmentsByEmailMessageId",
                               params={"EmailMessageId": email_message_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error reading email attachments: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[EmailAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmailAttachmentByPublicId",
                               params={"PublicId": public_id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading email attachment by public_id: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[EmailAttachment]:
        """Single-row lookup by primary key — used by extraction code paths."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT
                        Id, PublicId, RowVersion,
                        CONVERT(VARCHAR(19), CreatedDatetime, 120) AS CreatedDatetime,
                        CONVERT(VARCHAR(19), ModifiedDatetime, 120) AS ModifiedDatetime,
                        EmailMessageId, GraphAttachmentId, Filename, ContentType,
                        SizeBytes, IsInline, BlobUri,
                        ExtractionStatus,
                        CONVERT(VARCHAR(19), ExtractedAt, 120) AS ExtractedAt,
                        DiModel, DiResultJson, DiConfidence,
                        DiVendorName, DiInvoiceNumber,
                        CONVERT(VARCHAR(10), DiInvoiceDate, 120) AS DiInvoiceDate,
                        CONVERT(VARCHAR(10), DiDueDate, 120) AS DiDueDate,
                        DiSubtotal, DiTotalAmount, DiCurrency, LastError
                    FROM dbo.[EmailAttachment]
                    WHERE Id = ?
                    """,
                    id,
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading email attachment by id: {error}")
            raise map_database_error(error)

    def update_extraction(self, *, id: int, extraction_status: str,
                          di_model: Optional[str] = None,
                          di_result_json: Optional[str] = None,
                          di_confidence: Optional[Decimal] = None,
                          di_vendor_name: Optional[str] = None,
                          di_invoice_number: Optional[str] = None,
                          di_invoice_date: Optional[str] = None,
                          di_due_date: Optional[str] = None,
                          di_subtotal: Optional[Decimal] = None,
                          di_total_amount: Optional[Decimal] = None,
                          di_currency: Optional[str] = None,
                          last_error: Optional[str] = None) -> bool:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="UpdateEmailAttachmentExtraction", params={
                    "Id": id,
                    "ExtractionStatus": extraction_status,
                    "DiModel": di_model,
                    "DiResultJson": di_result_json,
                    "DiConfidence": di_confidence,
                    "DiVendorName": di_vendor_name,
                    "DiInvoiceNumber": di_invoice_number,
                    "DiInvoiceDate": di_invoice_date,
                    "DiDueDate": di_due_date,
                    "DiSubtotal": di_subtotal,
                    "DiTotalAmount": di_total_amount,
                    "DiCurrency": di_currency,
                    "LastError": last_error,
                })
                return cursor.fetchone() is not None
        except Exception as error:
            logger.error(f"Error updating email attachment extraction: {error}")
            raise map_database_error(error)
