# Python Standard Library Imports
import base64
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
        """
        Resolve recipients → look up source-summary attachment → build
        subject/body → enqueue an `[ms].[Outbox]` `send_mail` row whose
        worker dispatches to `create_draft` or `send_message` depending
        on `Settings.review_notification_mode`.

        `bill` and `review` are dataclasses already in hand on the caller
        side, avoiding redundant DB reads.
        """
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
        from entities.bill_line_item_attachment.persistence.repo import (
            BillLineItemAttachmentRepository,
        )
        from entities.project.business.service import ProjectService
        from entities.user.business.service import UserService
        from entities.vendor.business.service import VendorService
        from integrations.ms.outbox.business.service import MsOutboxService
        from shared.storage import AzureBlobStorage

        # 1. Resolve recipients.
        envelope = ReviewRecipientService().resolve_for_bill(
            bill_id=bill.id,
            exclude_user_id=exclude_user_id,
        )
        to_with_email = [r for r in envelope["to"] if r.email]
        cc_with_email = [r for r in envelope["cc"] if r.email]
        unreachable = [
            r for r in (envelope["to"] + envelope["cc"]) if not r.email
        ]
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

        # Fail-safe: if literally every recipient line is empty, the
        # outbox worker will reject the row. Skip noisily so the operator
        # can investigate the misconfiguration.
        if not to_with_email and not cc_with_email and not bcc_with_email:
            logger.error(
                "review_notification.skipped bill_public_id=%s "
                "reason=no_recipients_on_any_line",
                bill.public_id,
            )
            return

        # 2. Resolve denormalized labels for the email body.
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
                # Prefer the abbreviation column; fall back to name when
                # abbreviation is NULL so the email always shows something.
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

        # 3. Look up the source-summary attachment. First line item by Id
        #    ASC is the summary line for email-agent flows; for human-
        #    created bills it's whichever line was created first. Walk
        #    line items in id order and grab the first one that has an
        #    Attachment link.
        attachment_dict = None
        sorted_lis = sorted(line_items, key=lambda li: li.id or 0)
        bli_attachment_repo = BillLineItemAttachmentRepository()
        attachment_service = AttachmentService()
        blob_storage = AzureBlobStorage()
        for li in sorted_lis:
            if li.id is None:
                continue
            link = bli_attachment_repo.read_by_bill_line_item_id(
                bill_line_item_id=li.id
            )
            if link is None or link.attachment_id is None:
                continue
            attachment = attachment_service.read_by_id(link.attachment_id)
            if attachment is None or not attachment.blob_url:
                continue
            try:
                content, _meta = blob_storage.download_file(attachment.blob_url)
            except Exception as blob_error:
                logger.warning(
                    "review_notification.blob_download_failed bill_public_id=%s "
                    "attachment_id=%s blob_url=%s: %s",
                    bill.public_id,
                    attachment.id,
                    attachment.blob_url,
                    blob_error,
                )
                break
            attachment_dict = {
                "name": attachment.original_filename or attachment.filename or "bill.pdf",
                "content_type": attachment.content_type or "application/pdf",
                "content_bytes": base64.b64encode(content).decode("ascii"),
            }
            break

        # 4. Build subject + body.
        subject = self._build_subject(
            bill=bill,
            vendor_name=vendor_name,
            project_label=project_label,
        )
        body = self._build_body(
            bill=bill,
            vendor_name=vendor_name,
            project_label=project_label,
            submitter_name=submitter_name,
        )

        # 5. Enqueue.
        mode = settings.review_notification_mode
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
            body=body,
            body_type="HTML",
            attachment=attachment_dict,
            mode=mode,
            review_id=review.id,
            bill_id=bill.id,
        )

        if result is None:
            logger.info(
                "review_notification.enqueue_refused bill_public_id=%s reason=ms_writes_gate",
                bill.public_id,
            )
            return

        logger.info(
            "review_notification.enqueued bill_public_id=%s outbox_public_id=%s "
            "mode=%s to=%d cc=%d bcc=%d has_attachment=%s",
            bill.public_id,
            result.public_id,
            mode,
            len(to_with_email),
            len(cc_with_email),
            len(bcc_with_email),
            attachment_dict is not None,
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
        # Sprocs return string-formatted DATETIME2 (`YYYY-MM-DD HH:MM:SS`).
        # Be defensive: tolerate either a string or a real datetime.
        if isinstance(created_datetime, datetime):
            return created_datetime.strftime("%m/%d/%Y")
        s = str(created_datetime)
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).strftime("%m/%d/%Y")
            except ValueError:
                continue
        # Last resort: the raw string.
        return s

    @classmethod
    def _build_subject(cls, *, bill, vendor_name: str, project_label: str) -> str:
        bill_number = bill.bill_number or "(no number)"
        amount_str = cls._format_amount(bill.total_amount)
        return f"{project_label} - {vendor_name} - {bill_number} - {amount_str}"

    @classmethod
    def _build_body(cls, *, bill, vendor_name: str, project_label: str, submitter_name: str) -> str:
        bill_number = bill.bill_number or "(no number)"
        amount_str = cls._format_amount(bill.total_amount)
        submitted_date = cls._format_submitted_date(bill.created_datetime)
        return (
            "<p>A new bill has been submitted for review:</p>"
            "<p>"
            f"Project: {project_label}<br/>"
            f"Vendor: {vendor_name}<br/>"
            f"Number: {bill_number}<br/>"
            f"Amount: {amount_str}"
            "</p>"
            "<p>"
            f"Submitted By: {submitter_name}<br/>"
            f"Submitted Date: {submitted_date}"
            "</p>"
            "<p>When you have a moment, will you please reply for approval "
            "with Sub Cost Code and Description or non-approval?</p>"
        )
