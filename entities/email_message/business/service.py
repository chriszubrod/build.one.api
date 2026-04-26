"""EmailMessageService, MailboxPollService, EmailAttachmentExtractionService.

EmailMessageService
  Thin wrapper over the repos. Read paths for the API surface
  (list/paginate, read by id/public_id), state transitions for
  the agent runner.

MailboxPollService
  Drives the poll loop. Steps:
    1. Use the MS Graph mail client to list messages from the shared
       mailbox tagged `Agent: Process` and not yet bearing an outcome
       category.
    2. For each, idempotently upsert into EmailMessage (key:
       GraphMessageId).
    3. Fetch full message body + attachment list, persist each
       attachment to Azure Blob Storage, upsert EmailAttachment rows.
    4. Return a summary `{polled, new_messages, attachments_uploaded,
       errors[]}` so the admin endpoint can surface counts.

EmailAttachmentExtractionService
  Runs Document Intelligence against a single EmailAttachment row,
  hoists DI's nested response into the strongly-typed columns
  (DiVendorName / DiInvoiceNumber / DiTotalAmount / etc.), and flips
  ExtractionStatus based on the validation outcome:
    - 'extracted' if DI succeeded and validation passed
    - 'validation_failed' if DI succeeded but checksums failed
    - 'failed' on a hard DI error
"""
import json
import logging
import uuid
from decimal import Decimal
from typing import Optional

import config
from entities.email_message.business.categories import (
    AGENT_PROCESS,
    has_outcome,
)
from entities.email_message.business.model import EmailAttachment, EmailMessage
from entities.email_message.persistence.repo import (
    EmailAttachmentRepository,
    EmailMessageRepository,
)
from integrations.azure.document_intelligence.business.service import (
    DocumentIntelligenceService,
)
from integrations.azure.document_intelligence.external.client import (
    DocumentIntelligenceConfigError,
    DocumentIntelligenceError,
)
from integrations.ms.mail.external import client as mail_client
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)


class EmailMessageService:
    def __init__(self):
        self.repo = EmailMessageRepository()
        self.attachment_repo = EmailAttachmentRepository()

    def read_by_id(self, id: int) -> Optional[EmailMessage]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[EmailMessage]:
        return self.repo.read_by_public_id(public_id)

    def read_by_graph_message_id(self, graph_message_id: str) -> Optional[EmailMessage]:
        return self.repo.read_by_graph_message_id(graph_message_id)

    def read_paginated(self, **kwargs) -> list[EmailMessage]:
        return self.repo.read_paginated(**kwargs)

    def count(self, **kwargs) -> int:
        return self.repo.count(**kwargs)

    def list_attachments(self, email_message_id: int) -> list[EmailAttachment]:
        return self.attachment_repo.read_by_email_message_id(email_message_id)

    def update_status(self, *, id: int, processing_status: str,
                      last_error: Optional[str] = None,
                      agent_session_id: Optional[int] = None) -> Optional[EmailMessage]:
        return self.repo.update_status(
            id=id,
            processing_status=processing_status,
            last_error=last_error,
            agent_session_id=agent_session_id,
        )

    def claim_next_pending(self) -> Optional[EmailMessage]:
        return self.repo.claim_next_pending()

    def delete_by_id(self, id: int) -> bool:
        return self.repo.delete_by_id(id)


class MailboxPollService:
    """Polls the configured shared invoice inbox for messages tagged
    `Agent: Process` and persists them (with attachments) for the agent
    to pick up.
    """

    def __init__(self):
        self.message_repo = EmailMessageRepository()
        self.attachment_repo = EmailAttachmentRepository()
        self.storage = AzureBlobStorage()

    def poll_invoice_inbox(self, top: int = 50) -> dict:
        settings = config.Settings()
        mailbox = settings.invoice_inbox_email
        if not mailbox:
            return {
                "status_code": 503,
                "message": "invoice_inbox_email is not configured",
                "polled": 0,
                "new_messages": 0,
                "attachments_uploaded": 0,
                "errors": [],
            }

        # Filter: messages categorized 'Agent: Process'. We page through
        # the result and skip any that already carry an outcome category
        # (re-tagged after a previous run) — categories aren't mutually
        # exclusive in Graph, so client-side filtering keeps this honest.
        filter_query = f"categories/any(c:c eq '{AGENT_PROCESS}')"

        list_result = mail_client.list_messages(
            folder="inbox",
            top=top,
            filter_query=filter_query,
            mailbox=mailbox,
        )
        if list_result.get("status_code") != 200:
            return {
                "status_code": list_result.get("status_code", 500),
                "message": list_result.get("message", "list_messages failed"),
                "polled": 0,
                "new_messages": 0,
                "attachments_uploaded": 0,
                "errors": [list_result.get("message")],
            }

        messages = list_result.get("messages", [])
        new_count = 0
        attach_count = 0
        errors: list[dict] = []

        for msg_summary in messages:
            graph_message_id = msg_summary.get("message_id")
            if not graph_message_id:
                continue

            # Skip messages that already have an outcome category — those
            # have been processed before (or during a prior partial run)
            # and the poll doesn't re-touch them.
            if has_outcome(msg_summary.get("categories") or []):
                continue

            # Skip if we already have it in the DB and it's past 'pending'
            # state (in flight or already done — agent runner will redrive
            # if needed).
            existing = self.message_repo.read_by_graph_message_id(graph_message_id)
            if existing and existing.processing_status not in (None, "pending", "failed"):
                continue

            try:
                full = mail_client.get_message(
                    message_id=graph_message_id,
                    include_body=True,
                    mailbox=mailbox,
                )
                if full.get("status_code") != 200:
                    errors.append({
                        "graph_message_id": graph_message_id,
                        "error": full.get("message"),
                    })
                    continue

                email = full.get("email", {}) or {}
                persisted = self.message_repo.upsert(
                    graph_message_id=graph_message_id,
                    internet_message_id=email.get("internet_message_id"),
                    conversation_id=email.get("conversation_id"),
                    mailbox_address=mailbox,
                    from_address=email.get("from_email"),
                    from_name=email.get("from_name"),
                    subject=email.get("subject"),
                    body_preview=email.get("body_preview"),
                    body_content=email.get("body_content"),
                    body_content_type=email.get("body_content_type"),
                    received_datetime=_normalize_datetime(email.get("received_datetime")),
                    web_link=email.get("web_link"),
                    has_attachments=bool(email.get("has_attachments", False)),
                )
                if not existing:
                    new_count += 1

                # Walk attachments — download each, push to blob, persist row.
                for att in (email.get("attachments") or []):
                    if att.get("is_inline"):
                        # inline attachments (signature images, etc.) are
                        # noise — persist the row but leave blob_uri NULL
                        self.attachment_repo.upsert(
                            email_message_id=persisted.id,
                            graph_attachment_id=att.get("id"),
                            filename=att.get("name") or "unnamed",
                            content_type=att.get("content_type"),
                            size_bytes=att.get("size"),
                            is_inline=True,
                            blob_uri=None,
                        )
                        continue

                    attachment_persisted = self._persist_attachment(
                        email_message_id=persisted.id,
                        graph_message_id=graph_message_id,
                        attachment=att,
                        mailbox=mailbox,
                    )
                    if attachment_persisted:
                        attach_count += 1
                    else:
                        errors.append({
                            "graph_message_id": graph_message_id,
                            "attachment_id": att.get("id"),
                            "error": "attachment download or upload failed",
                        })

            except Exception as e:
                logger.exception(f"Error processing message {graph_message_id}: {e}")
                errors.append({"graph_message_id": graph_message_id, "error": str(e)})

        return {
            "status_code": 200,
            "message": f"Polled {len(messages)} message(s); persisted {new_count} new",
            "polled": len(messages),
            "new_messages": new_count,
            "attachments_uploaded": attach_count,
            "errors": errors,
        }

    def _persist_attachment(self, *, email_message_id: int, graph_message_id: str,
                            attachment: dict, mailbox: str) -> bool:
        graph_attachment_id = attachment.get("id")
        if not graph_attachment_id:
            return False

        # If we already have this attachment with a blob_uri set, skip the download.
        existing = self.attachment_repo.read_by_email_message_id(email_message_id)
        for prior in existing:
            if prior.graph_attachment_id == graph_attachment_id and prior.blob_uri:
                return True  # already uploaded; nothing to do

        download = mail_client.download_attachment(
            message_id=graph_message_id,
            attachment_id=graph_attachment_id,
            mailbox=mailbox,
        )
        if download.get("status_code") != 200 or not download.get("content"):
            return False

        content = download["content"]
        filename = download.get("filename") or attachment.get("name") or "unnamed"
        content_type = download.get("content_type") or attachment.get("content_type") or "application/octet-stream"

        # Blob path — namespaced under email-attachments/ so they don't
        # collide with the Attachment-entity blobs in the same container.
        blob_name = f"email-attachments/{graph_message_id}/{uuid.uuid4()}-{_safe_filename(filename)}"

        try:
            blob_uri = self.storage.upload_file(
                blob_name=blob_name,
                file_content=content,
                content_type=content_type,
            )
        except Exception as e:
            logger.error(f"Blob upload failed for {graph_message_id}/{graph_attachment_id}: {e}")
            return False

        self.attachment_repo.upsert(
            email_message_id=email_message_id,
            graph_attachment_id=graph_attachment_id,
            filename=filename,
            content_type=content_type,
            size_bytes=attachment.get("size"),
            is_inline=False,
            blob_uri=blob_uri,
        )
        return True


class EmailAttachmentExtractionService:
    """Runs DI against a single EmailAttachment + persists the result.

    The agent layer doesn't call DI directly — it calls
    `extract_attachment(public_id)` and reads back the
    `DiResultJson` / strongly-typed columns from the resulting row.
    """

    def __init__(self):
        self.attachment_repo = EmailAttachmentRepository()
        self.storage = AzureBlobStorage()
        self.di = DocumentIntelligenceService()

    def extract_by_public_id(self, public_id: str) -> dict:
        attachment = self.attachment_repo.read_by_public_id(public_id)
        if not attachment:
            raise ValueError(f"EmailAttachment not found: {public_id}")
        return self._extract(attachment)

    def extract_by_id(self, attachment_id: int) -> dict:
        attachment = self.attachment_repo.read_by_id(attachment_id)
        if not attachment:
            raise ValueError(f"EmailAttachment not found: id={attachment_id}")
        return self._extract(attachment)

    def _extract(self, attachment: EmailAttachment) -> dict:
        if attachment.is_inline:
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="skipped",
                last_error="inline attachment (signature image, etc.) — skipped",
            )
            return {"status": "skipped", "reason": "inline"}

        if not attachment.blob_uri:
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error="No BlobUri on attachment — cannot extract",
            )
            return {"status": "failed", "reason": "no_blob_uri"}

        # Pull the bytes from blob (avoids needing a SAS-signed URL).
        try:
            content, metadata = self.storage.download_file(attachment.blob_uri)
        except Exception as e:
            logger.exception(f"Blob download failed for attachment {attachment.public_id}: {e}")
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error=f"Blob download failed: {e}",
            )
            return {"status": "failed", "reason": "blob_download_error", "error": str(e)}

        content_type = metadata.get("content_type") or attachment.content_type or "application/pdf"

        # Run DI.
        try:
            extracted = self.di.extract_invoice(content, content_type=content_type)
        except DocumentIntelligenceConfigError as e:
            # Surface configuration errors loudly — never silently skip.
            logger.error(f"DI not configured: {e}")
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error=f"DI not configured: {e}",
            )
            raise
        except DocumentIntelligenceError as e:
            logger.error(f"DI extraction failed for attachment {attachment.public_id}: {e}")
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error=str(e)[:1000],
            )
            return {"status": "failed", "reason": "di_error", "error": str(e)}

        # Persist hoisted fields + DI raw JSON. ExtractionStatus reflects validation.
        validation = extracted.get("validation") or {}
        is_valid = bool(validation.get("is_valid"))
        new_status = "extracted" if is_valid else "validation_failed"

        self.attachment_repo.update_extraction(
            id=attachment.id,
            extraction_status=new_status,
            di_model="prebuilt-invoice",
            di_result_json=json.dumps(extracted.get("raw") or {}),
            di_confidence=extracted.get("confidence"),
            di_vendor_name=extracted.get("vendor_name"),
            di_invoice_number=extracted.get("invoice_number"),
            di_invoice_date=extracted.get("invoice_date"),
            di_due_date=extracted.get("due_date"),
            di_subtotal=extracted.get("subtotal"),
            di_total_amount=extracted.get("total_amount"),
            di_currency=extracted.get("currency"),
            last_error="; ".join(validation.get("issues") or []) if not is_valid else None,
        )

        return {
            "status": new_status,
            "vendor_name": extracted.get("vendor_name"),
            "invoice_number": extracted.get("invoice_number"),
            "total_amount": _decimal_to_str(extracted.get("total_amount")),
            "currency": extracted.get("currency"),
            "confidence": _decimal_to_str(extracted.get("confidence")),
            "validation": validation,
        }


def _decimal_to_str(d: Optional[Decimal]) -> Optional[str]:
    return str(d) if d is not None else None


def _normalize_datetime(raw: Optional[str]) -> Optional[str]:
    """Graph returns ISO 8601 like '2026-04-26T15:00:00Z'. Trim to the
    DATETIME2(3) friendly format expected by the sproc.
    """
    if not raw:
        return None
    # SQL Server's DATETIME2 happily takes 'YYYY-MM-DDTHH:MM:SS' or with fractional seconds;
    # strip the trailing 'Z' if present.
    cleaned = raw.rstrip("Z")
    return cleaned


def _safe_filename(name: str) -> str:
    """Strip path separators and quote-y chars so the blob name is valid."""
    cleaned = name.replace("/", "_").replace("\\", "_").replace("?", "_").replace("#", "_")
    return cleaned[:200]
