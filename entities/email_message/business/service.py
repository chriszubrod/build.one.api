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
from entities.email_message.business.categories import has_outcome
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
from shared.authz import current_user_id
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
                      agent_session_id: Optional[int] = None,
                      agent_classification: Optional[str] = None,
                      agent_classification_reason: Optional[str] = None,
                      agent_decided_action: Optional[str] = None,
                      agent_classification_confidence: Optional[Decimal] = None,
                      ) -> Optional[EmailMessage]:
        return self.repo.update_status(
            id=id,
            processing_status=processing_status,
            last_error=last_error,
            agent_session_id=agent_session_id,
            agent_classification=agent_classification,
            agent_classification_reason=agent_classification_reason,
            agent_decided_action=agent_decided_action,
            agent_classification_confidence=agent_classification_confidence,
        )

    def get_sender_history(self, from_email: str,
                           exclude_public_id: Optional[str] = None) -> dict:
        """Look up prior-context for an email sender. Used by the
        email_specialist's search_email_sender_history tool."""
        return self.repo.read_sender_history(
            from_email=from_email,
            exclude_public_id=exclude_public_id,
        )

    def claim_next_pending(self) -> Optional[EmailMessage]:
        return self.repo.claim_next_pending()

    def recover_stuck_processing(
        self, *, stale_after_minutes: int = 10, max_resets: int = 3
    ) -> dict:
        return self.repo.recover_stuck_processing(
            stale_after_minutes=stale_after_minutes,
            max_resets=max_resets,
        )

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

        # Filter: pull every inbox message newer than the last one we
        # already ingested. The agent decides relevance downstream
        # (vendor_invoice / internal_reply / non_actionable / etc.) — we
        # no longer rely on Outlook category as a pre-filter.
        #
        # Watermark = MAX(ReceivedDatetime) for this mailbox. On a fresh
        # mailbox we look back `INITIAL_BACKFILL_DAYS` (7d) to bound the
        # initial ingestion. `ge` (not `gt`) handles same-millisecond
        # ties at the boundary; the GraphMessageId upsert deduplicates
        # the boundary row.
        #
        # `$orderby=receivedDateTime asc` is load-bearing: combined with
        # `$top` truncation and per-message commits, it guarantees that
        # any messages dropped by a partial-batch crash are strictly
        # newer than `MAX(ReceivedDatetime)` and so get picked up on the
        # next poll. With `desc` we'd watermark to the newest of the
        # batch and skip the older tail forever.
        #
        # Backdated `receivedDateTime` (quarantine release, deferred-
        # delivery rules) is the residual gap; the eventual fix is
        # `/me/messages/delta`. See TODO.md.
        from datetime import datetime, timedelta, timezone
        INITIAL_BACKFILL_DAYS = 7
        watermark_dt = self.message_repo.read_max_received_datetime_for_mailbox(
            mailbox_address=mailbox,
        )
        if watermark_dt is None:
            watermark_dt = datetime.now(timezone.utc) - timedelta(
                days=INITIAL_BACKFILL_DAYS,
            )
        # Graph wants ISO 8601 with `Z`. SQL Server returns naive
        # DATETIME2; treat it as UTC (we store UTC consistently).
        watermark_iso = watermark_dt.strftime("%Y-%m-%dT%H:%M:%S.") \
            + f"{watermark_dt.microsecond // 1000:03d}Z"
        filter_query = f"receivedDateTime ge {watermark_iso}"

        list_result = mail_client.list_messages(
            folder="inbox",
            top=top,
            filter_query=filter_query,
            order_by="receivedDateTime asc",
            mailbox=mailbox,
        )
        if list_result.get("status_code") != 200:
            logger.error(
                "mailbox_poll.list_failed status=%s mailbox=%s filter=%r message=%s",
                list_result.get("status_code"),
                mailbox,
                filter_query,
                list_result.get("message"),
            )
            return {
                "status_code": list_result.get("status_code", 500),
                "message": list_result.get("message", "list_messages failed"),
                "polled": 0,
                "new_messages": 0,
                "attachments_uploaded": 0,
                "errors": [list_result.get("message")],
            }

        messages = list_result.get("messages", [])
        new_count, attach_count, errors = self._ingest_messages(
            messages=messages, mailbox=mailbox,
        )
        return {
            "status_code": 200,
            "message": f"Polled {len(messages)} message(s); persisted {new_count} new",
            "polled": len(messages),
            "new_messages": new_count,
            "attachments_uploaded": attach_count,
            "errors": errors,
        }

    def backfill_day(self, *, target_date_utc, top: int = 250) -> dict:
        """Pull every inbox message with `receivedDateTime` in the
        24-hour window starting at midnight UTC of `target_date_utc`.
        Idempotent — re-runs are safe because `GraphMessageId` is the
        upsert key. Used to walk historical mail backwards day-by-day
        without disturbing the forward poll's watermark (older rows
        don't change `MAX(ReceivedDatetime)`).
        """
        from datetime import datetime, timedelta, timezone
        settings = config.Settings()
        mailbox = settings.invoice_inbox_email
        if not mailbox:
            return {
                "status_code": 503,
                "message": "invoice_inbox_email is not configured",
                "polled": 0, "new_messages": 0,
                "attachments_uploaded": 0, "errors": [],
            }

        start = datetime(
            target_date_utc.year, target_date_utc.month, target_date_utc.day,
            0, 0, 0, tzinfo=timezone.utc,
        )
        end = start + timedelta(days=1)
        start_iso = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end_iso = end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        filter_query = (
            f"receivedDateTime ge {start_iso} and "
            f"receivedDateTime lt {end_iso}"
        )

        list_result = mail_client.list_messages(
            folder="inbox", top=top, filter_query=filter_query,
            order_by="receivedDateTime asc", mailbox=mailbox,
        )
        if list_result.get("status_code") != 200:
            logger.error(
                "mailbox_backfill.list_failed status=%s mailbox=%s window=%s/%s message=%s",
                list_result.get("status_code"), mailbox,
                start_iso, end_iso, list_result.get("message"),
            )
            return {
                "status_code": list_result.get("status_code", 500),
                "message": list_result.get("message", "list_messages failed"),
                "polled": 0, "new_messages": 0,
                "attachments_uploaded": 0,
                "errors": [list_result.get("message")],
            }

        messages = list_result.get("messages", [])
        new_count, attach_count, errors = self._ingest_messages(
            messages=messages, mailbox=mailbox,
        )
        return {
            "status_code": 200,
            "message": (
                f"Backfilled {target_date_utc.isoformat()} "
                f"({len(messages)} msg, {new_count} new)"
            ),
            "target_date": target_date_utc.isoformat(),
            "polled": len(messages),
            "new_messages": new_count,
            "attachments_uploaded": attach_count,
            "errors": errors,
        }

    def _ingest_messages(self, *, messages: list[dict], mailbox: str
                         ) -> tuple[int, int, list[dict]]:
        """Inner ingestion loop shared by `poll_invoice_inbox` (forward
        watermark) and `backfill_day` (historical 24h window). Returns
        `(new_count, attach_count, errors)`. Idempotent on
        `GraphMessageId` — re-runs do not duplicate."""
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

            # Defensive: if a tenant rule routes outbound mail back into
            # the polled folder, skip messages we ourselves sent so they
            # don't end up looking like vendor invoices.
            from_email = (msg_summary.get("from_email") or "").strip().lower()
            if from_email and from_email == mailbox.strip().lower():
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
                # Persist recipients as JSON arrays so the agent (and any
                # auditor) can see who else got the message.
                to_json = json.dumps(email.get("to_recipients") or []) if email.get("to_recipients") else None
                cc_json = json.dumps(email.get("cc_recipients") or []) if email.get("cc_recipients") else None
                persisted = self.message_repo.upsert(
                    graph_message_id=graph_message_id,
                    internet_message_id=email.get("internet_message_id"),
                    conversation_id=email.get("conversation_id"),
                    mailbox_address=mailbox,
                    from_address=email.get("from_email"),
                    from_name=email.get("from_name"),
                    to_recipients=to_json,
                    cc_recipients=cc_json,
                    subject=email.get("subject"),
                    body_preview=email.get("body_preview"),
                    body_content=email.get("body_content"),
                    body_content_type=email.get("body_content_type"),
                    received_datetime=_normalize_datetime(email.get("received_datetime")),
                    web_link=email.get("web_link"),
                    has_attachments=bool(email.get("has_attachments", False)),
                    created_by_user_id=current_user_id.get(),
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
                            created_by_user_id=current_user_id.get(),
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

        return new_count, attach_count, errors

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
            # Use the decoded byte length, NOT Graph's reported size (Graph
            # reports the base64-inflated wire size, ~33% larger).
            size_bytes=len(content),
            is_inline=False,
            blob_uri=blob_uri,
            created_by_user_id=current_user_id.get(),
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

    def record_extracted_fields_by_public_id(self, public_id: str, *,
                                             vendor_name: Optional[str] = None,
                                             invoice_number: Optional[str] = None,
                                             invoice_date: Optional[str] = None,
                                             due_date: Optional[str] = None,
                                             subtotal: Optional[Decimal] = None,
                                             total_amount: Optional[Decimal] = None,
                                             currency: Optional[str] = None) -> dict:
        """Agent-driven typed-field overlay onto EmailAttachment.Di* columns.
        Preserves the underlying DI extraction (status, raw JSON, model)
        — this is the agent's interpretation, not DI's hoist."""
        attachment = self.attachment_repo.read_by_public_id(public_id)
        if not attachment:
            raise ValueError(f"EmailAttachment not found: {public_id}")
        self.attachment_repo.update_extracted_fields(
            id=attachment.id,
            di_vendor_name=vendor_name,
            di_invoice_number=invoice_number,
            di_invoice_date=invoice_date,
            di_due_date=due_date,
            di_subtotal=subtotal,
            di_total_amount=total_amount,
            di_currency=currency,
        )
        return {
            "public_id": public_id,
            "vendor_name": vendor_name,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "due_date": due_date,
            "subtotal": _decimal_to_str(subtotal),
            "total_amount": _decimal_to_str(total_amount),
            "currency": currency,
        }

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

        # Cache-aware: if this attachment has already been DI'd successfully,
        # short-circuit and reshape the persisted DiResultJson into the same
        # response the live extraction produces. Saves ~6s of DI latency +
        # the ~$0.05 DI charge on every agent re-run of the same email.
        if (attachment.extraction_status == "extracted"
                and attachment.di_result_json):
            try:
                cached_raw = json.loads(attachment.di_result_json)
            except Exception:
                cached_raw = None
            if cached_raw:
                cached = self.di._hoist_and_validate(cached_raw)
                return {
                    "status": "extracted",
                    "cached": True,
                    "content": cached.get("content"),
                    "key_value_pairs": [
                        {
                            "key": kvp["key"],
                            "value": kvp["value"],
                            "confidence": _decimal_to_str(kvp.get("confidence")),
                        }
                        for kvp in (cached.get("key_value_pairs") or [])
                    ],
                    "tables": cached.get("tables") or [],
                    "pages_count": cached.get("pages_count"),
                    "vendor_name": attachment.di_vendor_name,
                    "invoice_number": attachment.di_invoice_number,
                    "total_amount": _decimal_to_str(attachment.di_total_amount),
                    "currency": attachment.di_currency,
                    "confidence": _decimal_to_str(attachment.di_confidence),
                    "validation": cached.get("validation") or {"is_valid": True, "issues": []},
                }

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
            di_model="prebuilt-layout",
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
            # Generic prebuilt-layout output — primary signal for the agent
            "content": extracted.get("content"),
            "key_value_pairs": [
                {
                    "key": kvp["key"],
                    "value": kvp["value"],
                    "confidence": _decimal_to_str(kvp.get("confidence")),
                }
                for kvp in (extracted.get("key_value_pairs") or [])
            ],
            "tables": extracted.get("tables") or [],
            "pages_count": extracted.get("pages_count"),
            # Backward-compat — currently None for prebuilt-layout extractions;
            # will be populated when agent-driven typed-field persistence ships.
            "vendor_name": extracted.get("vendor_name"),
            "invoice_number": extracted.get("invoice_number"),
            "total_amount": _decimal_to_str(extracted.get("total_amount")),
            "currency": extracted.get("currency"),
            "confidence": _decimal_to_str(extracted.get("confidence")),
            "validation": validation,
        }


def _decimal_to_str(d: Optional[Decimal]) -> Optional[str]:
    return str(d) if d is not None else None


# File-extension → canonical MIME mapping. Used to recover from mail
# systems that send "application/octet-stream" regardless of the
# actual file shape (Walker Lumber's mailer is one such culprit;
# downstream validators like create_bill's PDF-only check then reject
# the bridged Attachment if the type isn't normalized).
_EXT_TO_MIME = {
    "pdf":  "application/pdf",
    "jpg":  "image/jpeg",
    "jpeg": "image/jpeg",
    "png":  "image/png",
    "tif":  "image/tiff",
    "tiff": "image/tiff",
    "heic": "image/heic",
}


def _normalize_content_type(raw_content_type: str,
                            file_extension: Optional[str]) -> str:
    """Override an unhelpful generic MIME with one inferred from the
    file extension. Returns the original type when no override applies."""
    if not raw_content_type:
        raw_content_type = "application/octet-stream"
    ext = (file_extension or "").lower().lstrip(".")
    mapped = _EXT_TO_MIME.get(ext)
    # Only override the generic catch-all and (defensively) empty types.
    if mapped and raw_content_type.lower() in (
        "application/octet-stream", "binary/octet-stream", "application/x-binary",
    ):
        logger.info(
            "bridge.normalize_content_type override: %s + ext=%s -> %s",
            raw_content_type, ext, mapped,
        )
        return mapped
    return raw_content_type


class EmailAttachmentBridgeService:
    """Bridges an EmailAttachment into an Attachment row.

    The new Attachment shares the EmailAttachment's BlobUri (no blob copy),
    so cost stays the same and there's only one canonical bytes location.
    Bill creation requires `attachment_public_id`; the email agent calls
    this bridge to obtain one before delegating to bill_specialist.

    Hash-based dedup: re-bridging the same EmailAttachment (or a different
    one whose blob has identical bytes) returns the existing Attachment
    rather than creating a duplicate row.
    """

    def __init__(self):
        self.attachment_repo = EmailAttachmentRepository()
        self.storage = AzureBlobStorage()

    def bridge(self, email_attachment_public_id: str):
        # Lazy import — Attachment service has its own dependency tree
        from entities.attachment.business.service import AttachmentService

        ea = self.attachment_repo.read_by_public_id(email_attachment_public_id)
        if not ea:
            raise ValueError(f"EmailAttachment '{email_attachment_public_id}' not found")
        if ea.is_inline:
            raise ValueError("Inline attachments cannot be bridged (no blob)")
        if not ea.blob_uri:
            raise ValueError("EmailAttachment has no blob_uri — cannot bridge")

        attachment_service = AttachmentService()

        # Compute the hash from blob bytes for dedup. Downloading is the
        # only authoritative way; we don't trust Graph-reported sizes.
        content, metadata = self.storage.download_file(ea.blob_uri)
        file_hash = attachment_service.calculate_hash(content)

        existing = attachment_service.read_by_hash(file_hash)
        if existing:
            logger.info(
                f"Bridge: attachment with hash {file_hash[:16]}... already exists "
                f"({existing.public_id}); reusing"
            )
            return existing

        # Resolve content type + extension. Mail systems frequently send
        # PDFs (and images) as the generic "application/octet-stream" MIME
        # when they don't sniff the type — Walker Lumber's mailer is one
        # such culprit ("IN125AAC.pdf" with content_type=octet-stream).
        # Fall back to the file extension as the source of truth so
        # downstream validators (e.g. create_bill's PDF-only check) get
        # a usable type.
        raw_content_type = (
            metadata.get("content_type")
            or ea.content_type
            or "application/octet-stream"
        )
        file_extension = attachment_service.extract_extension(ea.filename or "")
        content_type = _normalize_content_type(raw_content_type, file_extension)

        attachment = attachment_service.create(
            filename=ea.filename,
            original_filename=ea.filename,
            file_extension=file_extension,
            content_type=content_type,
            file_size=len(content),
            file_hash=file_hash,
            blob_url=ea.blob_uri,
            description=f"Bridged from EmailAttachment {ea.public_id}",
            category="email_intake",
            is_archived=False,
        )
        logger.info(
            f"Bridge: created Attachment {attachment.public_id} from EmailAttachment {ea.public_id}"
        )
        return attachment


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
