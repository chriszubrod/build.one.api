# Python Standard Library Imports
import base64
import html
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

# Third-party Imports

# Local Imports
from entities.review.business.recipient_service import ReviewRecipientService

logger = logging.getLogger(__name__)


class ReviewNotificationService:
    """
    Builds + enqueues review-submit notification emails. Called from the
    auto-Submit hook in `BillService.create()` once the Review row has
    been written.

    Design (v2, 2026-05-28): purpose-built new email with the bill's
    bridged PDF attached + optional deep-link to the source vendor email.
    Replaces the v1 "forward-of-original" pattern. Rationale:

      - Bills sourced from multi-invoice emails (a vendor statement with
        N attached invoices) now get a focused per-bill email rather
        than dragging the whole vendor thread into every notification.
      - Renamed / content-type-normalized / format-converted attachments
        (HEIC -> PDF, octet-stream -> application/pdf) are what's on the
        bill. The reviewer sees that file, not whatever Graph still has
        on the original vendor email.
      - Bills with no `SourceEmailMessageId` (manual UI, bill_folder
        intake, agent-synthesized) are no longer silently skipped — they
        get the same review email as email-sourced bills.

    Failure semantics: every step is wrapped in an outer try/except. A
    notification failure NEVER propagates back to the caller — the Bill
    and Review row stand on their own. The user can manually trigger a
    notification later if needed.
    """

    def enqueue_for_bill(
        self,
        *,
        bill,
        review,
        exclude_user_id: Optional[int] = None,
    ) -> None:
        """Public surface. Resolves recipients, builds the message, and
        enqueues an `[ms].[Outbox]` `send_mail` row whose worker
        dispatches to `create_draft` or `send_message` depending on
        `Settings.review_notification_mode`."""
        try:
            self._do_enqueue(
                bill=bill,
                review=review,
                exclude_user_id=exclude_user_id,
            )
        except Exception as error:
            # Belt-and-suspenders: the inner pipeline already isolates
            # most failure modes. This catch is for anything that slipped
            # past — config import errors, missing env, etc. Never raise.
            logger.exception(
                "review_notification.enqueue_failed bill_public_id=%s review_id=%s: %s",
                getattr(bill, "public_id", None),
                getattr(review, "id", None),
                error,
            )

    def _do_enqueue(self, *, bill, review, exclude_user_id):
        # Lazy imports to avoid circular dependencies with BillService.
        from config import Settings
        from entities.attachment.business.service import AttachmentService
        from entities.bill_line_item.business.service import BillLineItemService
        from entities.bill_line_item_attachment.business.service import (
            BillLineItemAttachmentService,
        )
        from entities.email_message.business.service import EmailMessageService
        from entities.project.business.service import ProjectService
        from entities.sub_cost_code.business.service import SubCostCodeService
        from entities.user.business.service import UserService
        from entities.vendor.business.service import VendorService
        from integrations.ms.outbox.business.service import MsOutboxService
        from shared.storage import AzureBlobStorage

        # 1. Resolve recipients (PMs To, Owners Cc, invoice@ Bcc).
        envelope = ReviewRecipientService().resolve_for_bill(
            bill_id=bill.id,
            exclude_user_id=exclude_user_id,
        )
        to_with_email = [r for r in envelope["to"] if r.email]
        cc_with_email = [r for r in envelope["cc"] if r.email]
        unreachable = [r for r in (envelope["to"] + envelope["cc"]) if not r.email]
        if unreachable:
            logger.warning(
                "review_notification.unreachable_recipients bill_public_id=%s "
                "user_ids=%s reason=no_contact_email",
                bill.public_id,
                [r.user_id for r in unreachable],
            )
        if not to_with_email:
            logger.warning(
                "review_notification.no_to_recipient bill_public_id=%s "
                "reason=no_project_manager_with_email — sending anyway "
                "(BCC archive still active).",
                bill.public_id,
            )
        if not cc_with_email:
            logger.info(
                "review_notification.no_cc_recipient bill_public_id=%s "
                "reason=no_owner_with_email",
                bill.public_id,
            )

        # BCC the linked MS integration mailbox so every notification
        # leaves a copy in the same inbox the email-agent monitors. If the
        # env var is unset (local dev / misconfigured), we skip the BCC
        # gracefully rather than fail.
        settings = Settings()
        bcc_with_email = []
        if settings.invoice_inbox_email:
            bcc_with_email.append(
                {
                    "email": settings.invoice_inbox_email,
                    "name": "Invoice Inbox (archive)",
                }
            )
        else:
            logger.warning(
                "review_notification.bcc_archive_skipped bill_public_id=%s "
                "reason=invoice_inbox_email_not_configured",
                bill.public_id,
            )

        if not to_with_email and not cc_with_email and not bcc_with_email:
            logger.error(
                "review_notification.skipped bill_public_id=%s "
                "reason=no_recipients_on_any_line",
                bill.public_id,
            )
            return

        # 2. Resolve denormalized labels.
        vendor_name = "(unknown vendor)"
        if bill.vendor_id is not None:
            vendor = VendorService().read_by_id(bill.vendor_id)
            if vendor and vendor.name:
                vendor_name = vendor.name

        line_items = BillLineItemService().read_by_bill_id(bill.id) or []
        project_ids = sorted({li.project_id for li in line_items if li.project_id})
        project_label = "(no project)"
        if project_ids:
            ps = ProjectService()
            labels = []
            for pid in project_ids:
                p = ps.read_by_id(pid)
                if not p:
                    continue
                labels.append(p.abbreviation or p.name or "")
            labels = [s for s in labels if s]
            if labels:
                project_label = ", ".join(labels)

        submitter_name = f"User {review.user_id}"
        if review.user_id is not None:
            submitter = UserService().read_by_id(review.user_id)
            if submitter:
                full = f"{submitter.firstname or ''} {submitter.lastname or ''}".strip()
                if full:
                    submitter_name = full

        # 3. Look up the source-email weblink, if any. Used in the body
        # so the reviewer can click through to the original vendor thread
        # when they need that context.
        source_email_weblink: Optional[str] = None
        source_email_subject: Optional[str] = None
        source_email_message_id = getattr(bill, "source_email_message_id", None)
        if source_email_message_id:
            try:
                em = EmailMessageService().read_by_id(source_email_message_id)
                if em is not None:
                    source_email_weblink = em.web_link or None
                    source_email_subject = em.subject or None
            except Exception as e:
                logger.warning(
                    "review_notification.source_email_lookup_failed bill_public_id=%s: %s",
                    bill.public_id, e,
                )

        # 4. Resolve the bill's primary PDF attachment. Bill creation
        # mandates one PDF on the first line item; later lines may carry
        # additional attachments but we send only the canonical first one
        # to keep the email focused (reviewer can open more via the
        # bill's web UI if needed).
        attachment_payload = self._build_attachment_payload(
            bill=bill,
            line_items=line_items,
            bla_service=BillLineItemAttachmentService(),
            attachment_service=AttachmentService(),
            storage=AzureBlobStorage(),
        )

        # 5. Resolve line-item SubCostCode labels for the body table.
        scc_ids = sorted({li.sub_cost_code_id for li in line_items if li.sub_cost_code_id})
        scc_label_by_id: dict = {}
        if scc_ids:
            scs = SubCostCodeService()
            for scc_id in scc_ids:
                s = scs.read_by_id(scc_id)
                if s:
                    scc_label_by_id[scc_id] = f"{s.number} {s.name}".strip() if (s.number or s.name) else None

        # 6. Build subject + HTML body.
        subject = self._build_subject(
            vendor_name=vendor_name,
            bill_number=bill.bill_number,
            project_label=project_label,
            total_amount=bill.total_amount,
        )
        body_html = self._build_html_body(
            bill=bill,
            vendor_name=vendor_name,
            project_label=project_label,
            submitter_name=submitter_name,
            line_items=line_items,
            scc_label_by_id=scc_label_by_id,
            to_recipients=to_with_email,
            source_email_weblink=source_email_weblink,
            source_email_subject=source_email_subject,
            attachment_filename=(attachment_payload or {}).get("name"),
        )

        # 7. Enqueue. Always mode="draft" — the worker dispatches
        # create_draft, which deposits a draft in the sender mailbox's
        # Drafts folder. A human opens Outlook, reviews/edits, and sends.
        # We do NOT auto-send: the review notification is a human-in-the-
        # loop trigger, not an autonomous outbound. `Settings.review_
        # notification_mode` is intentionally bypassed here so a config
        # flip can't ever turn this into an autonomous-send path.
        mode = "draft"
        result = MsOutboxService().enqueue_send_mail(
            entity_type="Bill",
            entity_public_id=bill.public_id,
            to_addresses=[
                {"email": r.email, "name": r.display_name} for r in to_with_email
            ],
            cc_addresses=[
                {"email": r.email, "name": r.display_name} for r in cc_with_email
            ],
            bcc_addresses=bcc_with_email,
            subject=subject,
            body=body_html,
            body_type="HTML",
            attachment=attachment_payload,
            mode=mode,
            review_id=review.id,
            bill_id=bill.id,
            # forward_message_id deliberately omitted — v2 is new-email.
        )

        if result is None:
            logger.info(
                "review_notification.enqueue_refused bill_public_id=%s reason=ms_writes_gate",
                bill.public_id,
            )
            return

        logger.info(
            "review_notification.enqueued bill_public_id=%s outbox_public_id=%s "
            "mode=%s to=%d cc=%d bcc=%d attachment=%s source_email_linked=%s",
            bill.public_id,
            result.public_id,
            mode,
            len(to_with_email),
            len(cc_with_email),
            len(bcc_with_email),
            (attachment_payload or {}).get("name") or "(none)",
            bool(source_email_weblink),
        )

        # 8. Advance the Review state to "In Review" once the
        # notification has been enqueued to at least one PM/Owner (TO or
        # CC populated — BCC-only doesn't count). Best-effort; on failure
        # the bill stays at "Submitted" and no other side effects fire.
        if not (to_with_email or cc_with_email):
            return
        try:
            from entities.review.business.service import ReviewService
            from entities.review_status.business.service import ReviewStatusService
            statuses = ReviewStatusService().read_all()
            in_review = next(
                (s for s in statuses if s.sort_order > 10 and not s.is_final and not s.is_declined),
                None,
            )
            if in_review is None:
                logger.info(
                    "review_notification.in_review_status_missing bill_public_id=%s "
                    "no non-final non-declined sort>10 ReviewStatus configured",
                    bill.public_id,
                )
                return
            ReviewService().create(
                review_status_id=in_review.id,
                user_id=review.user_id,
                comments=None,
                bill_id=bill.id,
                # The BCC archive (sent back into invoice@) lands via
                # the next poll cycle — we don't have its EmailMessage
                # row at enqueue time. A follow-up backfill job links
                # the archive to this Review row by ConversationId match.
                email_message_id=None,
            )
            logger.info(
                "review_notification.in_review_advanced bill_public_id=%s",
                bill.public_id,
            )
        except Exception as in_review_error:
            logger.exception(
                "review_notification.in_review_advance_failed bill_public_id=%s: %s",
                bill.public_id, in_review_error,
            )

    # ─── attachment lookup ──────────────────────────────────────────────────

    @staticmethod
    def _build_attachment_payload(
        *,
        bill,
        line_items,
        bla_service,
        attachment_service,
        storage,
    ) -> Optional[dict]:
        """Resolve the bill's primary PDF attachment + base64-encode its
        blob bytes for the outbox payload. Returns None when no
        attachment is linked, when the blob can't be downloaded, or when
        the file isn't a PDF (defensive — bill create enforces PDF, but
        legacy data may not)."""
        if not line_items:
            return None
        # Walk line items in DB order; first one with a BLA wins.
        for li in line_items:
            bla = bla_service.read_by_bill_line_item_id(str(li.public_id))
            if not bla:
                continue
            attachment = attachment_service.read_by_id(bla.attachment_id)
            if not attachment or not attachment.blob_url:
                continue
            if (attachment.content_type or "").lower() != "application/pdf":
                logger.info(
                    "review_notification.attachment_skipped_non_pdf bill_public_id=%s "
                    "attachment_public_id=%s content_type=%s",
                    bill.public_id, attachment.public_id, attachment.content_type,
                )
                continue
            try:
                content_bytes, _meta = storage.download_file(attachment.blob_url)
            except Exception as e:
                logger.warning(
                    "review_notification.attachment_download_failed bill_public_id=%s "
                    "attachment_public_id=%s: %s",
                    bill.public_id, attachment.public_id, e,
                )
                continue
            return {
                "name":          attachment.filename or "bill.pdf",
                "content_type":  attachment.content_type or "application/pdf",
                "content_bytes": base64.b64encode(content_bytes).decode("ascii"),
            }
        return None

    # ─── subject + body builders ────────────────────────────────────────────

    @staticmethod
    def _build_subject(
        *,
        vendor_name: str,
        bill_number: Optional[str],
        project_label: str,
        total_amount,
    ) -> str:
        """`[Review] <Vendor> — Bill #<num> — <Project> — $<amt>`"""
        bill_num_display = bill_number or "(no number)"
        amount_str = ReviewNotificationService._format_amount(total_amount)
        return (
            f"[Review] {vendor_name} — Bill #{bill_num_display} "
            f"— {project_label} — {amount_str}"
        )

    @classmethod
    def _build_html_body(
        cls,
        *,
        bill,
        vendor_name: str,
        project_label: str,
        submitter_name: str,
        line_items,
        scc_label_by_id: dict,
        to_recipients: Optional[list],
        source_email_weblink: Optional[str],
        source_email_subject: Optional[str],
        attachment_filename: Optional[str],
    ) -> str:
        """Full HTML body. Sections (in order):
            1. Greeting addressed to PM firstnames (if any).
            2. Lead-in sentence.
            3. Header table: project, vendor, bill #, amount.
            4. Submission table: submitter, date.
            5. Line items table (description, project, SCC, amount).
            6. Original vendor email link (optional).
            7. Attachment hint (optional).
            8. Reviewer instructions.
        Vendor / project / submitter values are HTML-escaped so a value
        containing `<` or `&` doesn't render as broken markup."""
        bill_number = html.escape(bill.bill_number or "(no number)")
        vendor = html.escape(vendor_name)
        project = html.escape(project_label)
        submitter = html.escape(submitter_name)
        amount_str = cls._format_amount(bill.total_amount)
        submitted_date = cls._format_submitted_date(bill.created_datetime)

        greeting = ""
        if to_recipients:
            firstnames = [
                html.escape(r.firstname.strip())
                for r in to_recipients
                if getattr(r, "firstname", None) and r.firstname.strip()
            ]
            if firstnames:
                greeting = f"<p>{'/'.join(firstnames)},</p>"

        # Line-items table — only rendered when ≥1 line item exists.
        # Keeps each row to: description, project (if multi-project bill),
        # SCC label, amount.
        line_rows_html = ""
        if line_items:
            multi_project = (
                len({li.project_id for li in line_items if li.project_id}) > 1
            )
            rows = []
            for li in line_items:
                desc = html.escape(li.description or "")
                scc_label = html.escape(scc_label_by_id.get(li.sub_cost_code_id) or "")
                amt = cls._format_amount(li.amount)
                if multi_project:
                    # Look up per-line project label only when multi-project.
                    proj_lbl = ""
                    if li.project_id:
                        from entities.project.business.service import ProjectService
                        p = ProjectService().read_by_id(li.project_id)
                        if p:
                            proj_lbl = html.escape(p.abbreviation or p.name or "")
                    rows.append(
                        f"<tr><td>{desc}</td><td>{proj_lbl}</td>"
                        f"<td>{scc_label}</td><td style='text-align:right;'>{amt}</td></tr>"
                    )
                else:
                    rows.append(
                        f"<tr><td>{desc}</td><td>{scc_label}</td>"
                        f"<td style='text-align:right;'>{amt}</td></tr>"
                    )
            header = (
                "<tr><th align='left'>Description</th>"
                + ("<th align='left'>Project</th>" if multi_project else "")
                + "<th align='left'>Sub Cost Code</th>"
                "<th align='right'>Amount</th></tr>"
            )
            line_rows_html = (
                "<table cellpadding='4' cellspacing='0' border='1' "
                "style='border-collapse:collapse; margin-top:6px;'>"
                f"{header}{''.join(rows)}</table>"
            )

        # Source-email link section — only when SourceEmailMessageId is set.
        source_link_html = ""
        if source_email_weblink:
            link_label = (
                html.escape(source_email_subject)
                if source_email_subject else "Open in Outlook"
            )
            source_link_html = (
                "<p>Original vendor email: "
                f"<a href='{html.escape(source_email_weblink)}'>{link_label}</a></p>"
            )

        attachment_html = ""
        if attachment_filename:
            attachment_html = (
                f"<p>Attached: <strong>{html.escape(attachment_filename)}</strong></p>"
            )

        return (
            f"{greeting}"
            "<p>A new bill has been submitted for review:</p>"
            "<p>"
            f"Project: {project}<br/>"
            f"Vendor: {vendor}<br/>"
            f"Number: {bill_number}<br/>"
            f"Amount: {amount_str}"
            "</p>"
            "<p>"
            f"Submitted By: {submitter}<br/>"
            f"Submitted Date: {submitted_date}"
            "</p>"
            f"{line_rows_html}"
            f"{source_link_html}"
            f"{attachment_html}"
            "<p>When you have a moment, will you please reply for approval "
            "with Sub Cost Code and Description, or non-approval?</p>"
        )

    @staticmethod
    def _format_amount(total_amount) -> str:
        # Use Decimal(str(...)) per project convention; never float() on currency.
        try:
            total_decimal = Decimal(str(total_amount)) if total_amount is not None else Decimal("0")
        except Exception:
            total_decimal = Decimal("0")
        return f"${total_decimal:,.2f}"

    @staticmethod
    def _format_submitted_date(created_datetime) -> str:
        """Reformat the bill's created datetime as mm/dd/yyyy."""
        if not created_datetime:
            return ""
        if isinstance(created_datetime, datetime):
            return created_datetime.strftime("%m/%d/%Y")
        s = str(created_datetime)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%m/%d/%Y")
            except ValueError:
                continue
        return s
