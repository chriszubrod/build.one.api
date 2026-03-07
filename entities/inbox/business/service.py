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
from core.ai.email_classifier import EmailClassifier, MessageType
from core.ai.agents.email_agent.graph.agent import classify_email, classify_email_heuristic
from entities.bill.business.claude_extraction_service import ClaudeExtractionService
from core.ai.agents.extraction_agent.graph.agent import extract_from_ocr
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
        # Wire up per-sender classification overrides
        from entities.classification_override.business.service import ClassificationOverrideService
        self._override_svc = ClassificationOverrideService()
        self._classifier = EmailClassifier(override_service=self._override_svc)
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
        # MS Graph supports $filter on isRead but not on flag/flagStatus
        # (returns InefficientFilter error). Filter flagged messages client-side.
        filter_query = "isRead eq false" if unread_only else None

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
                    "type": cls.message_type.value,
                    "confidence": cls.confidence,
                    "label": cls.suggested_label,
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

        # Capture Point A: persist classification data for ML training
        try:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
                      f"{datetime.now(timezone.utc).microsecond // 1000:03d}"
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
            "email": email,
            "classification": {
                "type": classification.message_type.value,
                "confidence": classification.confidence,
                "label": classification.suggested_label,
                "signals": classification.signals,
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

        # --- Extraction pipeline: Agent → single-call Claude → heuristic ---
        mapped = None
        body_content = email.get("body_content") or email.get("body_preview") or ""

        # Try extraction agent first (multi-turn with validation)
        try:
            ocr_content = extraction_result.content or ""
            mapped = extract_from_ocr(
                tenant_id=1,
                ocr_content=ocr_content,
                from_email=email.get("from_email"),
                email_subject=email.get("subject"),
                attachment_filename=filename,
                projects=projects,
                sub_cost_codes=sub_cost_codes,
                email_body=body_content,
            )
        except Exception as exc:
            logger.warning("Extraction agent failed, trying single-call: %s", exc)

        # Fallback: single-call Claude extraction
        if mapped is None:
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

    def _classify_message(self, email: dict) -> object:
        """Run the email classifier (heuristic + LangGraph agent fallback)."""
        attachments = email.get("attachments") or []
        return classify_email(
            tenant_id=1,
            subject=email.get("subject"),
            from_email=email.get("from_email"),
            body=email.get("body_content") or email.get("body_preview") or "",
            attachments=[
                {"name": a.get("name", ""), "content_type": a.get("content_type", "")}
                for a in attachments
            ],
            override_service=self._override_svc,
        )

    def _classify_message_heuristic(self, email: dict) -> object:
        """Run heuristic-only classification (no agent fallback). Used for fast page loads."""
        attachments = email.get("attachments") or []
        return classify_email_heuristic(
            subject=email.get("subject"),
            from_email=email.get("from_email"),
            body=email.get("body_content") or email.get("body_preview") or "",
            attachments=[
                {"name": a.get("name", ""), "content_type": a.get("content_type", "")}
                for a in attachments
            ],
            override_service=self._override_svc,
        )

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
