"""
Inbox Service
=============
Orchestrates the invoice inbox workflow:
  1. List emails from the configured invoice inbox
  2. Classify each email (bill / expense / credit / inquiry / statement)
  3. Download attachments and run Document Intelligence extraction
  4. Map extraction results to Bill / Expense fields
  5. Create draft records ready for user review

This service is the primary entry point for the inbox web controller.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from config import Settings
from entities.bill.business.claude_extraction_service import ClaudeExtractionService
from entities.bill.business.extraction_mapper import BillExtractionMapper, BillExtractionResult
from entities.inbox.persistence.repo import InboxRecordRepository
from integrations.azure.ai.document_intelligence import AzureDocumentIntelligence
from integrations.ms.mail.external import client as mail_client
from integrations.ms.mail.message.business.service import MsMessageService

logger = logging.getLogger(__name__)
settings = Settings()

# Attachment content types we can extract from
EXTRACTABLE_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/tiff",
    "image/bmp",
}

# Minimum attachment size (bytes) to consider during thread fallback.
# Filters out email signature images, social media icons, tracking pixels, etc.
# Invoice PDFs/scans are almost always > 20KB.
MIN_THREAD_ATTACHMENT_SIZE = 20_000  # 20KB


class InboxService:
    """
    High-level service for the Invoice Inbox feature.

    All Graph API calls are routed to the configured invoice_inbox_email
    mailbox (INVOICE_INBOX_EMAIL env var) when set, falling back to the
    primary authenticated user's mailbox otherwise.
    """

    def __init__(self):
        self._mailbox = settings.invoice_inbox_email or None
        self._extractor = AzureDocumentIntelligence()
        self._claude_extractor = ClaudeExtractionService()
        self._mapper = BillExtractionMapper()
        self._mail_svc = MsMessageService()
        self._record_repo = InboxRecordRepository()

    # ------------------------------------------------------------------
    # Inbox listing
    # ------------------------------------------------------------------

    def list_inbox(
        self,
        folder: str = "inbox",
        top: int = 50,
        skip: int = 0,
        unread_only: bool = False,
        flagged_only: bool = False,
        category: Optional[str] = None,
    ) -> dict:
        """
        Return a list of emails from the invoice inbox, each enriched with
        an AI classification (bill / expense / credit / inquiry / statement).

        Returns:
            {
                "status_code": int,
                "messages": [ {...email, "classification": {...}} ],
                "total_count": int,
                "has_more": bool,
                "mailbox": str,
            }
        """
        # Build OData $filter.  MS Graph supports filtering on categories
        # natively: categories/any(c:c eq 'Blue Category')
        filters = []
        if unread_only:
            filters.append("isRead eq false")
        if category:
            filters.append(f"categories/any(c:c eq '{category}')")
        filter_query = " and ".join(filters) if filters else None

        # When filtering flagged client-side, fetch extra messages so we have
        # enough after filtering to fill the requested page.
        fetch_top = top * 3 if flagged_only else top

        result = mail_client.list_messages(
            folder=folder,
            top=fetch_top,
            skip=skip,
            filter_query=filter_query,
            order_by="receivedDateTime desc",
            mailbox=self._mailbox,
        )

        if result.get("status_code") not in (200, 201):
            return {
                **result,
                "mailbox": self._mailbox or "primary",
            }

        messages = result.get("messages", [])

        if flagged_only:
            messages = [
                m for m in messages
                if (m.get("flag") or {}).get("flagStatus") == "flagged"
            ]

        # Batch-lookup cached InboxRecords to avoid re-classifying
        message_ids = [m.get("message_id") for m in messages if m.get("message_id")]
        cached_records: dict = {}
        if message_ids:
            try:
                records = self._record_repo.read_by_message_ids(message_ids)
                cached_records = {r.message_id: r for r in records if r and r.message_id}
            except Exception as exc:
                logger.warning("Batch cache lookup failed (non-fatal): %s", exc)

        enriched = []
        for msg in messages:
            msg_id = msg.get("message_id")
            cached = cached_records.get(msg_id)

            if cached and cached.classification_type:
                # Use persisted classification (from a prior agent run)
                classification_dict = {
                    "type": cached.classification_type,
                    "confidence": cached.classification_confidence or 0.0,
                    "label": cached.classification_type.replace("_", " ").title(),
                }
            else:
                # Heuristic-only — fast, no agent call
                cls = self._classify_message_heuristic(msg)
                classification_dict = {
                    "type": cls.classification,
                    "confidence": cls.confidence,
                    "label": cls.classification.replace("_", " ").title(),
                }

            # Eligible for lazy auto-classify: unread + no prior InboxRecord
            auto_classify = (not msg.get("is_read", False)) and (cached is None)

            enriched.append({
                **msg,
                "received_display": self._format_received(msg.get("received_datetime", "")),
                "is_flagged": (msg.get("flag") or {}).get("flagStatus") == "flagged",
                "classification": classification_dict,
                "auto_classify": auto_classify,
            })

        return {
            "status_code": 200,
            "messages": enriched,
            "total_count": result.get("total_count", len(enriched)),
            "has_more": result.get("has_more", False),
            "mailbox": self._mailbox or "primary",
        }

    # ------------------------------------------------------------------
    # Single message detail
    # ------------------------------------------------------------------

    def get_message_detail(self, message_id: str) -> dict:
        """
        Return full message details including body, attachments, and
        AI classification.
        """
        result = mail_client.get_message(
            message_id=message_id,
            include_body=True,
            mailbox=self._mailbox,
        )

        if result.get("status_code") not in (200, 201):
            return result

        email = result.get("email", {})
        classification = self._classify_message(email)

        # Normalize classification attributes (heuristic vs agent result)
        cls_type = getattr(classification, "message_type", None)
        if cls_type is not None:
            cls_type = cls_type.value if hasattr(cls_type, "value") else str(cls_type)
        else:
            cls_type = getattr(classification, "classification", "unknown")
        cls_confidence = getattr(classification, "confidence", 0.0)
        cls_signals = getattr(classification, "signals", [])
        cls_label = getattr(classification, "suggested_label", cls_type.replace("_", " ").title())

        # Capture Point A: persist classification data for ML training
        try:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
                      f"{datetime.now(timezone.utc).microsecond // 1000:03d}"
            self._record_repo.upsert(
                message_id=message_id,
                status="new",
                classification_type=cls_type,
                classification_confidence=cls_confidence,
                classification_signals=json.dumps(cls_signals),
                classified_at=now_str,
                subject=email.get("subject"),
                from_email=email.get("from_email"),
                from_name=email.get("from_name"),
                has_attachments=email.get("has_attachments"),
            )
        except Exception as exc:
            logger.warning("Failed to persist classification for %s (non-fatal): %s", message_id, exc)

        return {
            "status_code": 200,
            "email": email,
            "classification": {
                "type": cls_type,
                "confidence": cls_confidence,
                "label": cls_label,
                "signals": cls_signals,
            },
        }

    # ------------------------------------------------------------------
    # Extraction (Document Intelligence → bill/expense fields)
    # ------------------------------------------------------------------

    def extract_from_message(
        self,
        message_id: str,
        attachment_id: Optional[str] = None,
    ) -> dict:
        """
        Download attachment(s) from the message, run Document Intelligence,
        and return structured extraction results mapped to bill fields.

        If ``attachment_id`` is given, only that attachment is processed.
        Otherwise the first extractable attachment is used.  When the current
        message has no extractable attachment, the conversation thread is
        searched so that reply-based workflows (e.g. PM approval replies)
        can still reach the original invoice attachment.

        Returns:
            {
                "status_code": int,
                "extraction": BillExtractionResult (as dict),
                "attachment": { filename, content_type, size },
                "raw_extraction": { content, key_value_pairs, tables, ... },
            }
        """
        # --- Get message metadata ---
        msg_result = mail_client.get_message(
            message_id=message_id,
            include_body=True,
            mailbox=self._mailbox,
        )
        if msg_result.get("status_code") not in (200, 201):
            return msg_result

        email = msg_result.get("email", {})
        attachments = email.get("attachments") or []

        logger.debug(
            "extract_from_message %s: has_attachments=%s, attachment_count=%d, "
            "conversation_id=%s",
            message_id,
            email.get("has_attachments"),
            len(attachments),
            (email.get("conversation_id") or "")[:50],
        )

        # --- Select target attachment ---
        target_attachment = None
        # Track which message owns the attachment (may differ from the
        # opened message when falling back to a thread message).
        attachment_message_id = message_id

        if attachment_id:
            target_attachment = next(
                (a for a in attachments if a.get("id") == attachment_id), None
            )
        else:
            # Pick first non-inline extractable attachment on current message
            for att in attachments:
                if not att.get("is_inline", False) and self._is_extractable(att):
                    target_attachment = att
                    break

        # --- Thread fallback: search conversation for an attachment ---
        if not target_attachment and not attachment_id:
            target_attachment, attachment_message_id = self._find_thread_attachment(
                email=email,
                current_message_id=message_id,
            )

        if not target_attachment:
            return {
                "status_code": 422,
                "message": "No extractable attachment found on this message or "
                           "in the conversation thread. "
                           "Attach a PDF or image invoice and try again.",
            }

        # --- Download attachment bytes ---
        download = mail_client.download_attachment(
            message_id=attachment_message_id,
            attachment_id=target_attachment["id"],
            mailbox=self._mailbox,
        )
        if download.get("status_code") != 200 or not download.get("content"):
            return {
                "status_code": download.get("status_code", 500),
                "message": f"Failed to download attachment: {download.get('message')}",
            }

        file_bytes: bytes = download["content"]
        content_type: str = download.get("content_type", "application/pdf")
        filename: str = download.get("filename", "attachment.pdf")

        if attachment_message_id != message_id:
            logger.info(
                "Using attachment '%s' from thread message %s (opened message %s had none)",
                filename, attachment_message_id, message_id,
            )

        # --- Run Document Intelligence ---
        logger.info("Running Document Intelligence on %s (%d bytes)", filename, len(file_bytes))
        try:
            extraction_result = self._extractor.extract_document(
                file_content=file_bytes,
                content_type=content_type,
            )
        except Exception as exc:
            logger.exception("Document Intelligence failed for message %s", message_id)
            return {
                "status_code": 500,
                "message": f"Document Intelligence extraction failed: {exc}",
            }

        # --- Map to bill fields (Claude AI first, heuristic fallback) ---
        from entities.project.business.service import ProjectService
        from entities.sub_cost_code.business.service import SubCostCodeService

        projects = []
        sub_cost_codes = []
        try:
            projects = ProjectService().read_all()
            sub_cost_codes = SubCostCodeService().read_all()
        except Exception as exc:
            logger.warning("Failed to load projects/subcostcodes for extraction: %s", exc)

        # --- Extraction pipeline: single-call Claude → heuristic ---
        mapped = None
        body_content = email.get("body_content") or email.get("body_preview") or ""

        # Single-call Claude extraction
        try:
            mapped = self._claude_extractor.extract(
                extraction=extraction_result,
                from_email=email.get("from_email"),
                email_subject=email.get("subject"),
                attachment_filename=filename,
                projects=projects,
                sub_cost_codes=sub_cost_codes,
                email_body=body_content,
            )
        except Exception as exc:
            logger.warning("Claude extraction failed, falling back to heuristic: %s", exc)

        # Fallback: heuristic mapper
        if mapped is None:
            mapped = self._mapper.map(
                extraction=extraction_result,
                from_email=email.get("from_email"),
                email_subject=email.get("subject"),
                attachment_filename=filename,
            )

        # Attempt entity resolution (vendor/project/payment term DB lookups)
        try:
            mapped = self._mapper.resolve_entities(mapped)
        except Exception as exc:
            logger.warning("Entity resolution failed (non-fatal): %s", exc)

        return {
            "status_code": 200,
            "extraction": self._extraction_to_dict(mapped),
            "attachment": {
                "id": target_attachment["id"],
                "filename": filename,
                "content_type": content_type,
                "size": download.get("size"),
                "source_message_id": attachment_message_id,
            },
            "raw_extraction": {
                "content": extraction_result.content,
                "key_value_pairs": extraction_result.key_value_pairs,
                "tables": [
                    {"row_count": t.row_count, "column_count": t.column_count, "cells": t.cells}
                    if hasattr(t, "cells") else t
                    for t in (extraction_result.tables or [])
                ],
            },
        }

    # ------------------------------------------------------------------
    # Attachment download (for inline preview)
    # ------------------------------------------------------------------

    def download_attachment(self, message_id: str, attachment_id: str) -> dict:
        """Download an attachment's raw bytes from MS Graph for inline preview."""
        return mail_client.download_attachment(
            message_id=message_id,
            attachment_id=attachment_id,
            mailbox=self._mailbox,
        )

    # ------------------------------------------------------------------
    # Forward to PM
    # ------------------------------------------------------------------

    def forward_to_pm(
        self,
        message_id: str,
        pm_email: str,
        note: Optional[str] = None,
    ) -> dict:
        """
        Forward an inbox message to a PM for review/approval.
        Uses the invoice inbox mailbox as the sender when configured.

        Returns: { "status_code": int, "message": str }
        """
        if not pm_email:
            return {"status_code": 422, "message": "PM email address is required."}

        default_note = (
            "When you have a moment, will you please review for approval?"
        )
        comment = note.strip() if note and note.strip() else default_note

        result = self._mail_svc.forward_message(
            message_id=message_id,
            to_recipients=[{"email": pm_email}],
            comment=comment,
        )

        if result.get("status_code") in (200, 201, 202):
            # Mark the message as read so it drops out of the unread list
            try:
                self._mail_svc.mark_message_read(message_id=message_id, is_read=True)
            except Exception:
                pass  # Non-fatal

            # Capture Point B: persist forward/pending_review status
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
                      f"{datetime.now(timezone.utc).microsecond // 1000:03d}"
            try:
                self._record_repo.upsert(
                    message_id=message_id,
                    status="pending_review",
                    submitted_to_email=pm_email,
                    submitted_at=now_str,
                )
            except Exception as exc:
                logger.warning("Failed to persist forward status for %s (non-fatal): %s", message_id, exc)

        return result

    # ------------------------------------------------------------------
    # Mark processed
    # ------------------------------------------------------------------

    def mark_processed(
        self,
        message_id: str,
        record_type: Optional[str] = None,
        record_public_id: Optional[str] = None,
        processed_via: Optional[str] = None,
    ) -> None:
        """
        Mark a message as read after a draft record has been created from it.
        Persists the processing outcome to the InboxRecord table for ML training.

        If the record_type differs from the stored classification_type, the
        user_override_type is set to capture the classifier's original prediction
        (i.e. the classifier got it wrong).
        """
        try:
            self._mail_svc.mark_message_read(message_id=message_id, is_read=True)
        except Exception as exc:
            logger.warning("mark_processed mark-read failed for %s (non-fatal): %s", message_id, exc)

        # Clear the flag so the message drops out of the flagged-only view
        try:
            self._mail_svc.flag_message(message_id=message_id, flagged=False)
        except Exception as exc:
            logger.warning("mark_processed unflag failed for %s (non-fatal): %s", message_id, exc)

        # Capture Point B: persist processing outcome
        # Capture Point C: detect user override (classifier was wrong)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
                  f"{datetime.now(timezone.utc).microsecond // 1000:03d}"

        user_override = None
        if record_type:
            # Check if user's action disagrees with the classifier
            try:
                existing = self._record_repo.read_by_message_id(message_id)
                if existing and existing.classification_type and existing.classification_type != record_type:
                    user_override = existing.classification_type
                    logger.info(
                        "User override detected for %s: classifier said '%s', user created '%s'",
                        message_id, existing.classification_type, record_type,
                    )
            except Exception as exc:
                logger.warning("Override detection failed for %s (non-fatal): %s", message_id, exc)

        status = "processed" if record_type else "skipped"
        try:
            self._record_repo.upsert(
                message_id=message_id,
                status=status,
                processed_at=now_str,
                record_type=record_type,
                record_public_id=record_public_id,
                user_override_type=user_override,
                processed_via=processed_via,
            )
        except Exception as exc:
            logger.warning("Failed to persist processing outcome for %s (non-fatal): %s", message_id, exc)

    # ------------------------------------------------------------------
    # Mark read / unread
    # ------------------------------------------------------------------

    def mark_read(self, message_id: str, is_read: bool = True) -> dict:
        """Mark a message as read or unread via Graph API."""
        return self._mail_svc.mark_message_read(message_id=message_id, is_read=is_read)

    # ------------------------------------------------------------------
    # Flag / unflag
    # ------------------------------------------------------------------

    def flag_message(self, message_id: str, flagged: bool = True) -> dict:
        """Flag or unflag a message via Graph API."""
        return self._mail_svc.flag_message(message_id=message_id, flagged=flagged)

    # ------------------------------------------------------------------
    # Classify single message (AJAX — lazy or manual)
    # ------------------------------------------------------------------

    def classify_message(self, message_id: str) -> dict:
        """
        Run full classification (heuristic + LangGraph agent) on a single
        message.  Persists the result to InboxRecord and returns the
        classification data.

        Called via AJAX — either lazily (background auto-classify after page
        render) or manually (user clicks the Classify button).
        """
        msg_result = mail_client.get_message(
            message_id=message_id,
            include_body=True,
            mailbox=self._mailbox,
        )
        if msg_result.get("status_code") not in (200, 201):
            return {
                "status_code": msg_result.get("status_code", 500),
                "message": msg_result.get("message", "Could not fetch message."),
            }

        email = msg_result.get("email", {})
        classification = self._classify_message(email)

        # Persist classification for cache + ML training
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
                  f"{datetime.now(timezone.utc).microsecond // 1000:03d}"
        try:
            self._record_repo.upsert(
                message_id=message_id,
                status="new",
                classification_type=classification.message_type.value,
                classification_confidence=classification.confidence,
                classification_signals=json.dumps(classification.signals),
                classified_at=now_str,
                subject=email.get("subject"),
                from_email=email.get("from_email"),
                from_name=email.get("from_name"),
                has_attachments=email.get("has_attachments"),
            )
        except Exception as exc:
            logger.warning("Failed to persist classification for %s (non-fatal): %s", message_id, exc)

        return {
            "status_code": 200,
            "classification": {
                "type": classification.message_type.value,
                "confidence": classification.confidence,
                "label": classification.suggested_label,
            },
        }

    # ------------------------------------------------------------------
    # Category queue processing (scheduler)
    # ------------------------------------------------------------------

    def process_category_queue(self, category: str, success_category: str) -> int:
        """
        Process all inbox messages tagged with *category*.

        For each message:
          1. Skip if already processed (InboxRecord.status not in new/None)
          2. Upsert InboxRecord with message metadata
          3. Classify email (removed — pending rebuild)
          4. On success: swap category Blue → Green
          5. On failure: leave Blue so next poll retries

        Returns the number of messages successfully processed.
        """
        result = mail_client.list_messages(
            folder="inbox",
            top=50,
            filter_query=f"categories/any(c:c eq '{category}')",
            order_by="receivedDateTime asc",
            mailbox=self._mailbox,
        )

        if result.get("status_code") not in (200, 201):
            logger.warning("Category queue fetch failed: %s", result.get("message"))
            return 0

        messages = result.get("messages", [])
        if not messages:
            return 0

        logger.info("Category queue: %d message(s) with '%s'", len(messages), category)

        # Batch-lookup existing InboxRecords to skip already-processed
        message_ids = [m.get("message_id") for m in messages if m.get("message_id")]
        
        cached_records: dict = {}
        if message_ids:
            try:
                records = self._record_repo.read_by_message_ids(message_ids)
                cached_records = {r.message_id: r for r in records if r and r.message_id}
            except Exception as exc:
                logger.warning("Batch cache lookup failed (non-fatal): %s", exc)

        processed = 0

        for msg in messages:
            msg_id = msg.get("message_id")
            if not msg_id:
                continue

            # Skip if already processed beyond initial state
            cached = cached_records.get(msg_id)
            if cached and cached.status and cached.status not in ("new",):
                logger.debug("Skipping already-processed message %s (status=%s)", msg_id, cached.status)
                # Still swap to green so it doesn't show up again
                self._swap_category(msg_id, msg.get("categories", []), category, success_category)
                continue

            try:
                # ── RECEIVED: upsert record + classify + create thread ──
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
                          f"{datetime.now(timezone.utc).microsecond // 1000:03d}"
                record = self._record_repo.upsert(
                    message_id=msg_id,
                    status="new",
                    classified_at=now_str,
                    subject=msg.get("subject"),
                    from_email=msg.get("from_email"),
                    from_name=msg.get("from_name"),
                    has_attachments=msg.get("has_attachments"),
                    internet_message_id=msg.get("internet_message_id"),
                    conversation_id=msg.get("conversation_id"),
                )
                inbox_record_id = record.id if record else None

                # Classification removed — email agent deleted during rebuild
                classification = None

                cls_type = getattr(classification, "classification", "UNKNOWN")
                cls_confidence = getattr(classification, "confidence", 0.0)
                cls_signals = getattr(classification, "signals", [])
                thread_id = getattr(classification, "thread_id", None)

                logger.info(
                    "RECEIVED: %s → %s (%.0f%%) thread=%s",
                    msg_id[:20], cls_type, cls_confidence * 100, thread_id,
                )

                # ── EXTRACTING: OCR + field extraction + entity resolution ──
                extraction_data = None
                if msg.get("has_attachments") and thread_id:
                    try:
                        self._advance_thread(thread_id, "RECEIVED", "EXTRACTING", "Starting extraction")
                        extraction_data = self.extract_from_message(message_id=msg_id)
                        if extraction_data.get("status_code") == 200:
                            cls_type, cls_confidence, cls_signals = self._refine_classification(
                                cls_type, cls_confidence, cls_signals, extraction_data,
                            )
                            logger.info(
                                "EXTRACTING: completed for %s → %s (%.0f%%)",
                                msg_id[:20], cls_type, cls_confidence * 100,
                            )
                        else:
                            logger.warning("EXTRACTING: failed for %s: %s", msg_id[:20], extraction_data.get("message"))
                    except Exception as exc:
                        logger.warning("EXTRACTING: failed for %s (non-fatal): %s", msg_id[:20], exc)

                # ── ENTITY_CREATED: create bill draft ──
                bill_public_id = None
                if extraction_data and extraction_data.get("status_code") == 200 and cls_type == "BILL_DOCUMENT":
                    try:
                        bill_public_id = self._create_bill_from_extraction(
                            msg_id, extraction_data, thread_id,
                        )
                    except Exception as exc:
                        logger.warning("ENTITY_CREATED: failed for %s: %s", msg_id[:20], exc)

                # ── PENDING_APPROVAL: forward email to PM(s) ──
                if bill_public_id and extraction_data:
                    try:
                        forwarded = self._forward_to_project_pms(
                            msg_id, extraction_data, thread_id,
                        )
                        if not forwarded:
                            logger.info("PENDING_APPROVAL: no project PMs found for %s — stopping at ENTITY_CREATED", msg_id[:20])
                    except Exception as exc:
                        logger.warning("PENDING_APPROVAL: failed for %s: %s", msg_id[:20], exc)

                # Persist classification to InboxRecord
                try:
                    self._record_repo.upsert(
                        message_id=msg_id,
                        status="pending_review",
                        classification_type=cls_type,
                        classification_confidence=cls_confidence,
                        classification_signals=json.dumps(cls_signals),
                        classified_at=now_str,
                    )
                except Exception as exc:
                    logger.warning("Failed to update InboxRecord for %s: %s", msg_id, exc)

                # Swap Blue → Green
                self._swap_category(msg_id, msg.get("categories", []), category, success_category)
                processed += 1

            except Exception as exc:
                # Leave Blue category so next poll retries
                logger.error("Failed to process message %s: %s", msg_id, exc)

        return processed

    def _forward_to_project_pms(
        self,
        message_id: str,
        extraction_data: dict,
        thread_id: str,
    ) -> bool:
        """
        Look up PM(s) and Owner(s) for the matched project, create a forward
        draft with To: (PMs) and CC: (Owners), then send.
        Advances thread: ENTITY_CREATED → PENDING_APPROVAL.
        Returns True if forwarded, False if no recipients found.
        """
        from entities.project.business.service import ProjectService
        from entities.user_project.business.service import UserProjectService
        from entities.user_role.business.service import UserRoleService
        from entities.role.business.service import RoleService
        from entities.contact.business.service import ContactService

        ext = extraction_data.get("extraction", {})
        project_match = ext.get("project_match")
        if not project_match or not project_match.get("public_id"):
            return False

        # Resolve project
        project = ProjectService().read_by_public_id(public_id=project_match["public_id"])
        if not project:
            logger.warning("Project %s not found — cannot forward", project_match["public_id"])
            return False

        # Build role lookup: name → id
        all_roles = RoleService().read_all()
        role_by_name = {r.name.lower(): r.id for r in all_roles if r.name}

        pm_role_id = role_by_name.get("project manager")
        owner_role_id = role_by_name.get("owner")

        # Get users assigned to this project
        user_projects = UserProjectService().read_by_project_id(project_id=project.id)
        project_user_ids = {up.user_id for up in user_projects}

        if not project_user_ids:
            logger.info("No users assigned to project %s", project_match.get("name"))
            return False

        # Classify project users by role
        user_role_svc = UserRoleService()
        contact_svc = ContactService()

        def _get_email(user_id: int) -> str | None:
            contacts = contact_svc.read_by_user_id(user_id=user_id)
            for c in contacts:
                if c.email:
                    return c.email
            return None

        to_emails = []  # Project Managers
        cc_emails = []  # Owners

        for user_id in project_user_ids:
            user_roles = user_role_svc.read_by_user_id(user_id=user_id)
            user_role_ids = {ur.role_id for ur in user_roles}

            email = _get_email(user_id)
            if not email:
                continue

            if pm_role_id and pm_role_id in user_role_ids:
                to_emails.append(email)
            elif owner_role_id and owner_role_id in user_role_ids:
                cc_emails.append(email)

        # If no PMs found but owners exist, put owners in To instead
        if not to_emails and cc_emails:
            to_emails = cc_emails
            cc_emails = []

        if not to_emails:
            logger.info("No PM/Owner emails found for project %s", project_match.get("name"))
            return False

        # Build comment with bill details
        vendor_name = (ext.get("vendor_match") or {}).get("name") or ext.get("vendor_name") or "Unknown"
        bill_number = ext.get("bill_number") or "N/A"
        raw_amount = ext.get("total_amount")
        try:
            formatted_amount = f"${float(raw_amount):,.2f}" if raw_amount else "N/A"
        except (ValueError, TypeError):
            formatted_amount = f"${raw_amount}" if raw_amount else "N/A"
        comment = (
            "When you have a moment, will you please review for approval?\n\n"
            f"Bill Vendor: {vendor_name}\n"
            f"Bill Number: {bill_number}\n"
            f"Bill Amount: {formatted_amount}\n"
        )

        # Create forward draft → set recipients → send
        try:
            draft_result = mail_client.create_forward_draft(message_id=message_id)
            if draft_result.get("status_code") != 201:
                logger.warning("Failed to create forward draft: %s", draft_result.get("message"))
                return False

            draft_id = draft_result["draft"]["message_id"]

            update_result = mail_client.update_draft(
                message_id=draft_id,
                to_recipients=[{"email": e} for e in to_emails],
                cc_recipients=[{"email": e} for e in cc_emails] if cc_emails else None,
                body=comment,
                body_type="Text",
            )
            if update_result.get("status_code") != 200:
                logger.warning("Failed to update forward draft: %s", update_result.get("message"))
                return False

            send_result = mail_client.send_draft(message_id=draft_id)
            if send_result.get("status_code") not in (200, 202):
                logger.warning("Failed to send forward draft: %s", send_result.get("message"))
                return False

            logger.info("PENDING_APPROVAL: forwarded to To:%s CC:%s", to_emails, cc_emails)

        except Exception as exc:
            logger.warning("Forward draft flow failed: %s", exc)
            return False

        # Advance thread
        self._advance_thread(
            thread_id, "ENTITY_CREATED", "PENDING_APPROVAL",
            f"Forwarded to PM(s): {', '.join(to_emails)}" +
            (f" CC: {', '.join(cc_emails)}" if cc_emails else ""),
        )

        # Update InboxRecord
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
                  f"{datetime.now(timezone.utc).microsecond // 1000:03d}"
        try:
            self._record_repo.upsert(
                message_id=message_id,
                status="pending_review",
                submitted_to_email=", ".join(to_emails + cc_emails),
                submitted_at=now_str,
            )
        except Exception as exc:
            logger.warning("Failed to update InboxRecord forward status: %s", exc)

        return True

    def _create_bill_from_extraction(
        self,
        message_id: str,
        extraction_data: dict,
        thread_id: str,
    ) -> str | None:
        """
        Create a draft Bill + BillLineItem + attachment from extraction data.
        Advances thread: EXTRACTING → ENTITY_CREATED.
        Returns the bill public_id or None on failure.
        """
        from datetime import date as _date
        from decimal import Decimal as _Decimal
        from entities.bill.business.service import BillService
        from entities.bill_line_item.business.service import BillLineItemService
        from entities.attachment.business.service import AttachmentService
        from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
        from integrations.azure.blob.service import AzureBlobStorage

        ext = extraction_data.get("extraction", {})
        att_info = extraction_data.get("attachment", {})

        vendor_public_id = (ext.get("vendor_match") or {}).get("public_id")
        bill_number = ext.get("bill_number")
        bill_date = ext.get("bill_date") or str(_date.today())
        due_date = ext.get("due_date") or bill_date
        total_amount = ext.get("total_amount")
        memo = ext.get("memo")
        payment_term_public_id = (ext.get("payment_term_match") or {}).get("public_id")
        project_public_id = (ext.get("project_match") or {}).get("public_id")
        sub_cost_code_id = (ext.get("sub_cost_code_match") or {}).get("id")

        bill_svc = BillService()
        bill = bill_svc.create(
            vendor_public_id=vendor_public_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            payment_term_public_id=payment_term_public_id,
            is_draft=True,
        )
        logger.info("ENTITY_CREATED: bill %s for message %s", bill.public_id, message_id[:20])

        # Create a single line item
        line_item = None
        try:
            amt = _Decimal(str(total_amount)) if total_amount else None
            line_item = BillLineItemService().create(
                bill_public_id=bill.public_id,
                description=memo or "Invoice",
                quantity=1,
                rate=amt,
                amount=amt,
                is_billable=ext.get("is_billable", True),
                markup=_Decimal("0"),
                price=amt,
                project_public_id=project_public_id,
                sub_cost_code_id=int(sub_cost_code_id) if sub_cost_code_id else None,
                is_draft=True,
            )
        except Exception as exc:
            logger.warning("Failed to create line item for bill %s: %s", bill.public_id, exc)

        # Attach source document to the line item
        attachment_id = att_info.get("id")
        attachment_message_id = att_info.get("source_message_id") or message_id
        if line_item and attachment_id:
            try:
                download = mail_client.download_attachment(
                    message_id=attachment_message_id,
                    attachment_id=attachment_id,
                    mailbox=self._mailbox,
                )
                if download.get("status_code") == 200:
                    file_bytes = download["content"]
                    dl_filename = download.get("filename", "attachment.pdf")
                    dl_content_type = download.get("content_type", "application/pdf")
                    file_ext = dl_filename.rsplit(".", 1)[-1] if "." in dl_filename else "pdf"

                    storage = AzureBlobStorage()
                    blob_name = f"inbox/{bill.public_id}/{dl_filename}"
                    blob_url = storage.upload_file(
                        blob_name=blob_name,
                        file_content=file_bytes,
                        content_type=dl_content_type,
                    )

                    attachment = AttachmentService().create(
                        filename=dl_filename,
                        original_filename=dl_filename,
                        file_extension=file_ext,
                        content_type=dl_content_type,
                        file_size=len(file_bytes),
                        file_hash=AttachmentService.calculate_hash(file_bytes),
                        blob_url=blob_url,
                        description=f"Source invoice - {dl_filename}",
                        category="invoice",
                    )

                    BillLineItemAttachmentService().create(
                        bill_line_item_public_id=line_item.public_id,
                        attachment_public_id=attachment.public_id,
                    )
                    logger.info("Attached %s to bill %s", dl_filename, bill.public_id)
            except Exception as exc:
                logger.warning("Failed to attach document to bill %s: %s", bill.public_id, exc)

        # Advance thread
        self._advance_thread(thread_id, "EXTRACTING", "ENTITY_CREATED",
                             f"Bill draft created: {bill.public_id}")

        return bill.public_id

    def _refine_classification(
        self,
        cls_type: str,
        cls_confidence: float,
        cls_signals: list,
        extraction_data: dict,
    ) -> tuple[str, float, list]:
        """
        Refine classification using extraction results and raw OCR content.
        Returns updated (cls_type, cls_confidence, cls_signals).
        """
        extraction = extraction_data.get("extraction", {})
        raw = extraction_data.get("raw_extraction", {})
        ocr_content = (raw.get("content") or "").upper()
        signals = list(cls_signals)  # copy

        # --- Document type detection from OCR content ---
        credit_indicators = ["CREDIT MEMO", "CREDIT NOTE", "VENDOR CREDIT"]
        is_credit_doc = any(ind in ocr_content for ind in credit_indicators)

        # Negative total amount → credit signal
        total_str = extraction.get("total_amount")
        is_negative = False
        if total_str:
            try:
                is_negative = float(total_str) < 0
            except (ValueError, TypeError):
                pass

        if is_credit_doc or is_negative:
            cls_type = "BILL_CREDIT_DOCUMENT"
            signals.append("document contains credit memo indicators" if is_credit_doc else "negative total amount")

        # --- Confidence boost from extraction quality ---
        has_vendor = bool(extraction.get("vendor_match"))
        has_bill_number = bool(extraction.get("bill_number"))
        has_amount = bool(extraction.get("total_amount"))
        has_date = bool(extraction.get("bill_date"))
        overall = extraction.get("overall_confidence") or 0.0

        # Count how many key fields were extracted
        field_count = sum([has_vendor, has_bill_number, has_amount, has_date])

        if field_count >= 3:
            # Strong extraction — boost confidence significantly
            cls_confidence = max(cls_confidence, 0.90)
            signals.append(f"extraction matched {field_count}/4 key fields")
        elif field_count >= 2:
            cls_confidence = max(cls_confidence, 0.80)
            signals.append(f"extraction matched {field_count}/4 key fields")
        elif field_count >= 1:
            cls_confidence = max(cls_confidence, 0.70)
            signals.append(f"extraction matched {field_count}/4 key fields")

        if has_vendor:
            signals.append(f"vendor matched: {extraction['vendor_match']['name']}")

        return cls_type, cls_confidence, signals

    def _advance_thread(self, thread_public_id: str, from_stage: str, to_stage: str, notes: str = ""):
        """Advance an EmailThread from one stage to the next."""
        try:
            from entities.email_thread.persistence.repo import EmailThreadRepository
            from entities.email_thread.persistence.stage_history_repo import EmailThreadStageHistoryRepository
            from core.workflow.api.process_engine import EventType
            import uuid

            thread_repo = EmailThreadRepository()
            history_repo = EmailThreadStageHistoryRepository()

            thread = thread_repo.read_by_public_id(thread_public_id)
            if not thread:
                logger.warning("Cannot advance thread %s — not found", thread_public_id)
                return

            if thread.current_stage != from_stage:
                logger.debug(
                    "Thread %s is at %s, not %s — skipping advance to %s",
                    thread_public_id, thread.current_stage, from_stage, to_stage,
                )
                return

            # Write stage history
            history_repo.create(
                public_id=str(uuid.uuid4()),
                email_thread_id=thread.id,
                from_stage=from_stage,
                to_stage=to_stage,
                triggered_by=EventType.EMAIL_RECEIVED.value,
                notes=notes,
            )

            # Update thread stage
            thread_repo.upsert(
                public_id=thread.public_id,
                inbox_record_id=thread.inbox_record_id,
                category=thread.category,
                process_type=thread.process_type,
                current_stage=to_stage,
                is_reply=thread.is_reply or False,
                is_forward=thread.is_forward or False,
                internet_message_id=thread.internet_message_id,
                subject=thread.subject,
                classification_confidence=thread.classification_confidence,
                is_resolved=thread.is_resolved,
                requires_action=to_stage == "REVIEW_NEEDED",
            )

            logger.info("Thread %s advanced: %s → %s", thread_public_id, from_stage, to_stage)

        except Exception as exc:
            logger.error("Failed to advance thread %s from %s to %s: %s", thread_public_id, from_stage, to_stage, exc)

    def _swap_category(
        self,
        message_id: str,
        current_categories: list[str],
        old_category: str,
        new_category: str,
    ):
        """Replace old_category with new_category on a message."""
        updated = [c for c in current_categories if c != old_category]
        updated.append(new_category)
        try:
            mail_client.set_categories(
                message_id=message_id,
                categories=updated,
                mailbox=self._mailbox,
            )
        except Exception as exc:
            logger.warning("Failed to swap category on %s: %s", message_id, exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_thread_attachment(
        self,
        email: dict,
        current_message_id: str,
    ) -> tuple:
        """
        Search the conversation thread for an extractable attachment when
        the current message has none.

        This handles the common workflow where a PM replies "approved" to a
        forwarded invoice — the reply has no attachment, but the original
        forwarded message (same conversation thread) does.

        Returns:
            (target_attachment, source_message_id) or (None, current_message_id)
        """
        conversation_id = email.get("conversation_id")
        if not conversation_id:
            logger.debug("No conversation_id on message %s — cannot search thread", current_message_id)
            return None, current_message_id

        logger.info(
            "No attachment on message %s — searching conversation thread %s",
            current_message_id, conversation_id[:50],
        )

        try:
            thread_result = mail_client.search_all_messages(
                conversation_id=conversation_id,
                mailbox=self._mailbox,
            )
        except Exception as exc:
            logger.warning("Thread search failed for %s (non-fatal): %s", conversation_id, exc)
            return None, current_message_id

        if thread_result.get("status_code") != 200:
            logger.warning("Thread search returned status %s", thread_result.get("status_code"))
            return None, current_message_id

        thread_messages = thread_result.get("messages") or []
        logger.debug(
            "Thread search found %d messages for conversation %s",
            len(thread_messages), conversation_id[:50],
        )

        # Collect candidate attachments from all thread messages, then pick
        # the best one.  This avoids grabbing a small signature image when a
        # proper invoice PDF exists on an earlier message.
        candidates = []  # list of (attachment_dict, source_message_id)

        for thread_msg in reversed(thread_messages):  # newest → oldest
            thread_msg_id = thread_msg.get("message_id")
            if thread_msg_id == current_message_id:
                continue  # Already checked this one

            attachments = thread_msg.get("attachments") or []
            logger.debug(
                "Thread msg %s: has_attachments=%s, attachment_count=%d",
                thread_msg_id[:20],
                thread_msg.get("has_attachments"),
                len(attachments),
            )

            # MS Graph $expand=attachments sometimes returns empty even when
            # hasAttachments is true (especially on filtered/search queries).
            # Fetch the message individually to get its attachments.
            if not attachments and thread_msg.get("has_attachments"):
                logger.debug(
                    "Thread msg %s has_attachments=True but expand empty — fetching individually",
                    thread_msg_id[:20],
                )
                try:
                    detail = mail_client.get_message(
                        message_id=thread_msg_id,
                        include_body=False,
                        mailbox=self._mailbox,
                    )
                    if detail.get("status_code") in (200, 201):
                        attachments = detail.get("email", {}).get("attachments") or []
                except Exception as exc:
                    logger.warning("Failed to fetch attachments for thread msg %s: %s", thread_msg_id[:20], exc)

            for att in attachments:
                if att.get("is_inline", False):
                    continue
                if not self._is_extractable(att):
                    continue

                att_size = att.get("size") or 0
                att_name = (att.get("name") or "").lower()
                is_pdf = att_name.endswith(".pdf")
                # PDFs bypass the size filter — signature images are never PDFs,
                # but small invoices (< 20KB) are common.
                if not is_pdf and att_size < MIN_THREAD_ATTACHMENT_SIZE:
                    logger.debug(
                        "Skipping small attachment '%s' (%d bytes) — likely a signature/icon",
                        att.get("name"), att_size,
                    )
                    continue

                candidates.append((att, thread_msg_id))

        if not candidates:
            logger.info("No extractable attachment found in conversation thread %s", conversation_id[:50])
            return None, current_message_id

        # Prefer PDFs over images (invoices are almost always PDFs)
        def _sort_key(item):
            att = item[0]
            ct = (att.get("content_type") or "").lower()
            name = (att.get("name") or "").lower()
            is_pdf = ct == "application/pdf" or name.endswith(".pdf")
            size = att.get("size") or 0
            # PDFs first (0), then images (1); within each group, largest first
            return (0 if is_pdf else 1, -size)

        candidates.sort(key=_sort_key)
        best_att, best_msg_id = candidates[0]

        logger.info(
            "Found extractable attachment '%s' (%d bytes) on thread message %s",
            best_att.get("name"), best_att.get("size", 0), best_msg_id,
        )
        return best_att, best_msg_id

    def _format_received(self, dt_str: str) -> str:
        """
        Format a Graph API receivedDateTime string into a compact, readable form.

        Today:       "5:26 PM"
        This year:   "Feb 26,  5:26 PM"
        Older:       "2/26/25, 5:26 PM"
        """
        if not dt_str:
            return ""
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if dt.date() == now.date():
                return dt.strftime("%-I:%M %p")
            elif dt.year == now.year:
                return dt.strftime("%b %-d, %-I:%M %p")
            else:
                return dt.strftime("%-m/%-d/%y, %-I:%M %p")
        except Exception:
            return dt_str

    def _classify_message(self, email: dict, inbox_record_id: int = None, internet_message_id: str = None) -> object:
        """Classification removed — email agent deleted during rebuild."""
        return None

    def _classify_message_heuristic(self, email: dict) -> object:
        """Classification removed — email agent deleted during rebuild."""
        return None

    def _is_extractable(self, attachment: dict) -> bool:
        """Return True if Document Intelligence can process this attachment type."""
        ct = (attachment.get("content_type") or "").lower()
        name = (attachment.get("name") or "").lower()
        if ct in EXTRACTABLE_TYPES:
            return True
        if name.endswith((".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp")):
            return True
        return False

    def _extraction_to_dict(self, result: BillExtractionResult) -> dict:
        """Serialize a BillExtractionResult to a plain dict for JSON responses."""
        return {
            "vendor_name": result.vendor_name,
            "bill_number": result.bill_number,
            "bill_date": result.bill_date,
            "due_date": result.due_date,
            "total_amount": str(result.total_amount) if result.total_amount else None,
            "payment_terms_raw": result.payment_terms_raw,
            "memo": result.memo,
            "ship_to_address": result.ship_to_address,
            "line_items": [
                {
                    "description": li.description,
                    "amount": str(li.amount) if li.amount else None,
                    "quantity": li.quantity,
                    "unit_price": str(li.unit_price) if li.unit_price else None,
                    "confidence": li.confidence,
                }
                for li in result.line_items
            ],
            "vendor_match": {
                "public_id": result.vendor_match.public_id,
                "name": result.vendor_match.name,
                "confidence": result.vendor_match.confidence,
            } if result.vendor_match else None,
            "project_match": {
                "public_id": result.project_match.public_id,
                "name": result.project_match.name,
                "confidence": result.project_match.confidence,
            } if result.project_match else None,
            "payment_term_match": {
                "public_id": result.payment_term_match.public_id,
                "name": result.payment_term_match.name,
                "confidence": result.payment_term_match.confidence,
            } if result.payment_term_match else None,
            "sub_cost_code_match": {
                "id": result.sub_cost_code_match.id,
                "name": result.sub_cost_code_match.name,
                "confidence": result.sub_cost_code_match.confidence,
            } if result.sub_cost_code_match else None,
            "project_hint": result.project_hint,
            "sub_cost_code_hint": result.sub_cost_code_hint,
            "is_billable": result.is_billable,
            "vendor_confidence": result.vendor_confidence,
            "bill_number_confidence": result.bill_number_confidence,
            "date_confidence": result.date_confidence,
            "amount_confidence": result.amount_confidence,
            "memo_confidence": result.memo_confidence,
            "project_confidence": result.project_confidence,
            "sub_cost_code_confidence": result.sub_cost_code_confidence,
            "overall_confidence": result.overall_confidence,
            "extraction_notes": result.extraction_notes,
        }
