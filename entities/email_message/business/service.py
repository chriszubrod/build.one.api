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
import re
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

    def poll_invoice_sent(self, top: int = 50) -> dict:
        """Poll the Sent Items folder of the configured mailbox so our
        own outbound forwards (review notifications) get captured as
        EmailMessage rows. Audit-only — these rows never reach the agent
        runner because their ProcessingStatus is set to 'outbound', and
        ClaimNextPendingEmailMessage filters on 'pending'.

        Watermark is independent of the inbox poll's watermark. Folder
        is 'sentitems'. Attachments are not re-downloaded (the forward
        inherits the original vendor PDF, already in our blob store).
        """
        from datetime import datetime, timedelta, timezone
        FOLDER = "sentitems"
        INITIAL_BACKFILL_DAYS = 7
        settings = config.Settings()
        mailbox = settings.invoice_inbox_email
        if not mailbox:
            return {
                "status_code": 503,
                "message": "invoice_inbox_email is not configured",
                "polled": 0, "new_messages": 0,
                "attachments_uploaded": 0, "errors": [],
            }

        watermark_dt = self.message_repo.read_max_received_datetime_for_mailbox(
            mailbox_address=mailbox, folder=FOLDER,
        )
        if watermark_dt is None:
            watermark_dt = datetime.now(timezone.utc) - timedelta(
                days=INITIAL_BACKFILL_DAYS,
            )
        watermark_iso = watermark_dt.strftime("%Y-%m-%dT%H:%M:%S.") \
            + f"{watermark_dt.microsecond // 1000:03d}Z"
        filter_query = f"receivedDateTime ge {watermark_iso}"

        list_result = mail_client.list_messages(
            folder=FOLDER, top=top, filter_query=filter_query,
            order_by="receivedDateTime asc", mailbox=mailbox,
        )
        if list_result.get("status_code") != 200:
            logger.error(
                "mailbox_poll_sent.list_failed status=%s mailbox=%s filter=%r message=%s",
                list_result.get("status_code"), mailbox,
                filter_query, list_result.get("message"),
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
            messages=messages, mailbox=mailbox, folder=FOLDER,
        )
        return {
            "status_code": 200,
            "message": f"Polled {len(messages)} sent message(s); persisted {new_count} new",
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

    def backfill_sent_day(self, *, target_date_utc, top: int = 250) -> dict:
        """Sister of `backfill_day` against the Sent Items folder. Pulls
        every outbound forward `receivedDateTime` in the 24-hour window
        starting at midnight UTC. Idempotent on GraphMessageId."""
        from datetime import datetime, timedelta, timezone
        FOLDER = "sentitems"
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
            folder=FOLDER, top=top, filter_query=filter_query,
            order_by="receivedDateTime asc", mailbox=mailbox,
        )
        if list_result.get("status_code") != 200:
            logger.error(
                "mailbox_backfill_sent.list_failed status=%s mailbox=%s window=%s/%s message=%s",
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
            messages=messages, mailbox=mailbox, folder=FOLDER,
        )
        return {
            "status_code": 200,
            "message": (
                f"Backfilled sent {target_date_utc.isoformat()} "
                f"({len(messages)} msg, {new_count} new)"
            ),
            "target_date": target_date_utc.isoformat(),
            "polled": len(messages),
            "new_messages": new_count,
            "attachments_uploaded": attach_count,
            "errors": errors,
        }

    def reconcile_review_email_message_links(self) -> int:
        """Wrapper around the `ReconcileReviewEmailMessageLinks` sproc.
        Run after each Sent poll / backfill to backfill
        Review.EmailMessageId on auto-advanced rows whose forward
        EmailMessage row arrived after the Review was created."""
        return self.message_repo.reconcile_review_email_message_links()

    def _ingest_messages(self, *, messages: list[dict], mailbox: str,
                         folder: str = "inbox"
                         ) -> tuple[int, int, list[dict]]:
        """Inner ingestion loop shared by `poll_invoice_inbox` /
        `backfill_day` (folder=inbox) and `poll_invoice_sent` /
        `backfill_sent_day` (folder=sentitems). Returns
        `(new_count, attach_count, errors)`. Idempotent on
        `GraphMessageId` — re-runs do not duplicate.

        Sent-folder semantics:
          - The `from_email == mailbox` defensive filter is *off*
            (we expect outbound).
          - Attachments are not re-downloaded; the forward inherits the
            original vendor PDF which is already in our blob store from
            the inbound side.
          - Rows are persisted with `ProcessingStatus='outbound'` so
            `ClaimNextPendingEmailMessage` (keyed on 'pending') does not
            hand them to the email_specialist agent.
        """
        is_sent = (folder == "sentitems")
        default_status = "outbound" if is_sent else "pending"

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

            # Defensive (inbox only): if a tenant rule routes outbound
            # mail back into the polled folder, skip messages we
            # ourselves sent so they don't end up looking like vendor
            # invoices. Sent-folder ingestion bypasses this by design.
            if not is_sent:
                from_email = (msg_summary.get("from_email") or "").strip().lower()
                if from_email and from_email == mailbox.strip().lower():
                    continue

            # Skip if we already have it in the DB and it's past 'pending'
            # state (in flight or already done — agent runner will redrive
            # if needed). For sent-folder rows, the existing row is most
            # likely 'outbound' already (idempotent re-run), so this also
            # skips appropriately.
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
                    folder=folder,
                    default_processing_status=default_status,
                )
                if not existing:
                    new_count += 1

                # Sent-folder rows: skip attachment download. The forward
                # carries the original vendor PDF, which is already in
                # our blob store from the inbound side; re-downloading
                # would duplicate without value.
                if is_sent:
                    continue

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

    def extract_by_public_id(self, public_id: str, *, force_inline: bool = False) -> dict:
        attachment = self.attachment_repo.read_by_public_id(public_id)
        if not attachment:
            raise ValueError(f"EmailAttachment not found: {public_id}")
        return self._extract(attachment, force_inline=force_inline)

    def extract_by_id(self, attachment_id: int, *, force_inline: bool = False) -> dict:
        attachment = self.attachment_repo.read_by_id(attachment_id)
        if not attachment:
            raise ValueError(f"EmailAttachment not found: id={attachment_id}")
        return self._extract(attachment, force_inline=force_inline)

    def _extract(self, attachment: EmailAttachment, *, force_inline: bool = False) -> dict:
        # Inline attachments (email signatures, embedded screenshots) are
        # skipped by default — most are signature images that burn DI cost
        # for no signal. The agent can opt back in with force_inline=True
        # when the visible text signal is ambiguous and an embedded image
        # might carry the decisive context (e.g., a screenshot of a remit
        # advice pasted into a vendor reply).
        if attachment.is_inline and not force_inline:
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="skipped",
                last_error="inline attachment (signature image, etc.) — skipped",
            )
            return {"status": "skipped", "reason": "inline"}

        # Inline attachments are NOT persisted to blob storage (the poll
        # service only uploads non-inline). force_inline'd extractions
        # fetch the bytes from MS Graph on demand, run DI in-memory, and
        # cache the DI result on the EmailAttachment row — no blob upload.
        if attachment.is_inline and force_inline:
            return self._extract_inline_from_graph(attachment)

        if not attachment.blob_uri:
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error="No BlobUri on attachment — cannot extract",
            )
            return {"status": "failed", "reason": "no_blob_uri"}

        # Cache short-circuit — saves ~6s of DI latency + the DI charge on
        # every agent re-run of the same email.
        cached_response = self._cached_extraction_response(attachment)
        if cached_response is not None:
            return cached_response

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
        return self._run_di_and_persist(attachment, content, content_type)

    def _extract_inline_from_graph(self, attachment: EmailAttachment) -> dict:
        """Force-extract an inline attachment by fetching the bytes from
        MS Graph on demand. Inline attachments are not in blob storage
        (the poll service skips them). We cache the DI result on the row
        so re-runs don't re-call Graph or DI."""
        # Cache short-circuit (same shape as the blob path).
        cached_response = self._cached_extraction_response(attachment)
        if cached_response is not None:
            return cached_response

        if not attachment.graph_attachment_id:
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error="No GraphAttachmentId — cannot fetch inline bytes from Graph",
            )
            return {"status": "failed", "reason": "no_graph_attachment_id"}

        # Read parent EmailMessage for the GraphMessageId + MailboxAddress.
        parent = EmailMessageRepository().read_by_id(attachment.email_message_id)
        if not parent or not parent.graph_message_id or not parent.mailbox_address:
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error="Parent EmailMessage missing graph_message_id or mailbox_address",
            )
            return {"status": "failed", "reason": "parent_unavailable"}

        download_result = mail_client.download_attachment(
            message_id=parent.graph_message_id,
            attachment_id=attachment.graph_attachment_id,
            mailbox=parent.mailbox_address,
        )
        if download_result.get("status_code") != 200:
            err = download_result.get("message") or "graph download failed"
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error=f"Graph download failed: {err}",
            )
            return {"status": "failed", "reason": "graph_download_error", "error": err}

        content = download_result.get("content")
        content_type = (
            download_result.get("content_type")
            or attachment.content_type
            or "application/octet-stream"
        )
        if not content:
            self.attachment_repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                last_error="Graph returned an empty attachment body",
            )
            return {"status": "failed", "reason": "graph_empty_body"}

        return self._run_di_and_persist(attachment, content, content_type)

    def _cached_extraction_response(self, attachment: EmailAttachment) -> Optional[dict]:
        """If this attachment has a usable cached DI result, reshape it
        into the standard extraction response. Returns None on cache miss
        so the caller can run a fresh extraction."""
        if attachment.extraction_status != "extracted" or not attachment.di_result_json:
            return None
        try:
            cached_raw = json.loads(attachment.di_result_json)
        except Exception:
            return None
        if not cached_raw:
            return None
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

    def _run_di_and_persist(
        self, attachment: EmailAttachment, content: bytes, content_type: str
    ) -> dict:
        """Shared DI invocation + result persistence + response shaping.
        Used by both the blob-backed extraction path and the force-inline
        Graph-backed path."""
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


# ─── Quoted-history isolation for reply / forward bodies ────────────────────
#
# Outlook / Gmail / Apple Mail / etc. each have their own convention for
# marking where the new content ends and the quoted prior message begins.
# We detect the first plausible boundary and split into:
#   • body_new_text       = the sender's new content (top)
#   • body_quoted_history = the quoted prior message + everything below
# Both returned as plain text. Detection is best-effort — when no
# boundary is found, the full body is treated as new_text and history
# is empty. The agent reads new_text first; quoted_history is available
# when the new-text portion alone isn't enough context.

# Plain-text reply markers. Ordered by specificity.
_TEXT_BOUNDARY_PATTERNS = [
    # "From: <person>" header lines that begin a reply quote. The four-
    # line Outlook block (From: / Sent: / To: / Subject:) typically
    # starts with this. Anchored to line start, surrounded by newlines.
    re.compile(r"\n[ \t]*From:[ \t]+.+\n[ \t]*Sent:[ \t]+", re.IGNORECASE),
    re.compile(r"\n[ \t]*From:[ \t]+.+\n[ \t]*Date:[ \t]+", re.IGNORECASE),
    re.compile(r"\n[ \t]*From:[ \t]+.+\n[ \t]*To:[ \t]+", re.IGNORECASE),
    # "On <date>, <person> wrote:" — Gmail / Apple Mail convention.
    re.compile(r"\n[ \t]*On .+ wrote:\s*\n", re.IGNORECASE),
    # Explicit Outlook separator (English locale).
    re.compile(r"\n[ \t]*-----[ \t]?Original Message[ \t]?-----", re.IGNORECASE),
    # Long horizontal rule of underscores/equals/dashes (visual separator).
    re.compile(r"\n[ \t]*[_=-]{20,}\s*\n"),
]


def _html_to_plain_text(html: str) -> str:
    """Render an HTML fragment to whitespace-normalized plain text. Uses
    BeautifulSoup with the stdlib `html.parser` so no extra runtime deps
    beyond what's already in requirements.txt."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # Defensive: BS4 is in requirements but if it ever drops, fall
        # back to a naive tag-strip rather than 500'ing the agent.
        return re.sub(r"<[^>]+>", " ", html)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n")
    # Collapse runs of whitespace within a line, but preserve line breaks
    # — line structure is the signal we use to find quoted-history headers.
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(lines).strip()


def _split_html_at_quote_boundary(html: str) -> Optional[tuple[str, str]]:
    """Detect a quote-block boundary in HTML (Gmail blockquote, Outlook
    reply div, Apple Mail cite). Returns (before_html, after_html) when
    found, None otherwise."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    soup = BeautifulSoup(html, "html.parser")
    candidates: list = []
    candidates.extend(soup.find_all("blockquote"))
    candidates.extend(soup.find_all("div", class_="gmail_quote"))
    candidates.extend(soup.find_all("div", id=re.compile(r"^(divRplyFwdMsg|appendonsend)$")))
    candidates.extend(soup.find_all("div", attrs={"type": "cite"}))
    if not candidates:
        return None
    # Pick the candidate that appears earliest in the source.
    earliest = min(candidates, key=lambda el: html.find(str(el)) if str(el) in html else 10**9)
    if str(earliest) not in html:
        return None
    split_at = html.find(str(earliest))
    before_html = html[:split_at]
    after_html = html[split_at:]
    return before_html, after_html


def isolate_new_text(
    body_content: Optional[str],
    body_content_type: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Split a polled email body into (new_text, quoted_history) as plain
    text. When no boundary is detected, returns (full_body_as_text, None).

    The agent reads `new_text` first — that's the sender's actual new
    content on a reply/forward — and only falls back to `quoted_history`
    when the new-text portion alone is insufficient context. Cuts down
    on the size + noise of every reply email by ~60-90%.
    """
    if not body_content:
        return None, None
    is_html = (body_content_type or "").lower() == "html" or "<html" in body_content[:200].lower()

    # HTML path: try structural quote-block detection first.
    if is_html:
        split = _split_html_at_quote_boundary(body_content)
        if split is not None:
            before_html, after_html = split
            return _html_to_plain_text(before_html), _html_to_plain_text(after_html)
        # No structural boundary — flatten to text and fall through to
        # the text-pattern detector below.
        body_content = _html_to_plain_text(body_content)

    # Plain-text path: scan for any of the boundary patterns and pick the
    # earliest hit. Everything before = new text; the marker + everything
    # after = quoted history.
    earliest_match = None
    for pattern in _TEXT_BOUNDARY_PATTERNS:
        m = pattern.search(body_content)
        if m is not None and (earliest_match is None or m.start() < earliest_match.start()):
            earliest_match = m
    if earliest_match is None:
        # No quote boundary detected; the whole body is new content.
        return body_content.strip() or None, None
    new_text = body_content[: earliest_match.start()].strip()
    quoted = body_content[earliest_match.start():].strip()
    return new_text or None, quoted or None


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
