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

    def enqueue_for_expense(
        self,
        *,
        expense,
        review,
        exclude_user_id: Optional[int] = None,
    ) -> None:
        """Enqueue a review-submit notification for an expense (U-486 C1)."""
        try:
            self._do_enqueue_expense(
                expense=expense,
                review=review,
                exclude_user_id=exclude_user_id,
            )
        except Exception as error:
            logger.exception(
                "review_notification.enqueue_failed expense_public_id=%s review_id=%s: %s",
                getattr(expense, "public_id", None),
                getattr(review, "id", None),
                error,
            )

    def _do_enqueue_expense(self, *, expense, review, exclude_user_id):
        from config import Settings
        from entities.attachment.business.service import AttachmentService
        from entities.expense.business.service import ExpenseService
        from entities.expense_line_item.business.service import ExpenseLineItemService
        from entities.expense_line_item_attachment.business.service import (
            ExpenseLineItemAttachmentService,
        )
        from entities.project.business.service import ProjectService
        from entities.review.business.recipient_service import ReviewRecipientService
        from entities.review.persistence.repo import ReviewRepository
        from entities.sub_cost_code.business.service import SubCostCodeService
        from entities.user.business.service import UserService
        from entities.vendor.business.service import VendorService
        from integrations.ms.outbox.business.service import MsOutboxService
        from shared.authz.context import system_authz
        from shared.storage import AzureBlobStorage

        rows = ReviewRepository().resolve_review_recipients_by_expense_id(
            expense_id=expense.id,
            exclude_user_id=exclude_user_id,
        )
        envelope = ReviewRecipientService._bucket(rows)
        to_with_email = [r for r in envelope["to"] if r.email]
        cc_with_email = [r for r in envelope["cc"] if r.email]
        unreachable = [r for r in (envelope["to"] + envelope["cc"]) if not r.email]
        if unreachable:
            logger.warning(
                "review_notification.unreachable_recipients expense_public_id=%s "
                "user_ids=%s reason=no_contact_email",
                expense.public_id,
                [r.user_id for r in unreachable],
            )
        if not to_with_email:
            logger.warning(
                "review_notification.no_to_recipient expense_public_id=%s "
                "reason=no_project_manager_with_email — sending anyway "
                "(BCC archive still active).",
                expense.public_id,
            )
        if not cc_with_email:
            logger.info(
                "review_notification.no_cc_recipient expense_public_id=%s "
                "reason=no_owner_with_email",
                expense.public_id,
            )

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
                "review_notification.bcc_archive_skipped expense_public_id=%s "
                "reason=invoice_inbox_email_not_configured",
                expense.public_id,
            )

        if not to_with_email and not cc_with_email and not bcc_with_email:
            logger.error(
                "review_notification.skipped expense_public_id=%s "
                "reason=no_recipients_on_any_line",
                expense.public_id,
            )
            return

        vendor_name = "(unknown vendor)"
        if expense.vendor_id is not None:
            vendor = VendorService().read_by_id(expense.vendor_id)
            if vendor and vendor.name:
                vendor_name = vendor.name

        line_items = ExpenseLineItemService().read_by_expense_id(expense.id) or []
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

        attachment_payload = self._build_expense_attachment_payload(
            expense=expense,
            line_items=line_items,
            elia_service=ExpenseLineItemAttachmentService(),
            attachment_service=AttachmentService(),
            storage=AzureBlobStorage(),
        )

        scc_ids = sorted({li.sub_cost_code_id for li in line_items if li.sub_cost_code_id})
        scc_label_by_id: dict = {}
        if scc_ids:
            scs = SubCostCodeService()
            for scc_id in scc_ids:
                s = scs.read_by_id(scc_id)
                if s:
                    scc_label_by_id[scc_id] = f"{s.number} {s.name}".strip() if (s.number or s.name) else None

        qbo_url = None
        try:
            with system_authz():
                qbo_url = ExpenseService()._build_qbo_url_for_expense(expense)
        except Exception as qbo_link_error:
            logger.warning(
                "review_notification.qbo_link_failed expense_public_id=%s: %s",
                expense.public_id,
                qbo_link_error,
            )

        subject = self._build_expense_subject(
            vendor_name=vendor_name,
            reference_number=expense.reference_number,
            project_label=project_label,
            total_amount=expense.total_amount,
        )
        body_html = self._build_expense_html_body(
            expense=expense,
            vendor_name=vendor_name,
            project_label=project_label,
            submitter_name=submitter_name,
            line_items=line_items,
            scc_label_by_id=scc_label_by_id,
            to_recipients=to_with_email,
            attachment_filename=(attachment_payload or {}).get("name"),
            qbo_url=qbo_url,
        )

        mode = "draft"
        result = MsOutboxService().enqueue_send_mail(
            entity_type="Expense",
            entity_public_id=expense.public_id,
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
        )

        if result is None:
            logger.info(
                "review_notification.enqueue_refused expense_public_id=%s reason=ms_writes_gate",
                expense.public_id,
            )
            return

        logger.info(
            "review_notification.enqueued expense_public_id=%s outbox_public_id=%s "
            "mode=%s to=%d cc=%d bcc=%d attachment=%s",
            expense.public_id,
            result.public_id,
            mode,
            len(to_with_email),
            len(cc_with_email),
            len(bcc_with_email),
            (attachment_payload or {}).get("name") or "(none)",
        )

        if not (to_with_email or cc_with_email):
            return
        self._advance_expense_to_in_review(expense=expense, review=review)

    def _advance_expense_to_in_review(self, *, expense, review) -> None:
        try:
            from shared.authz import system_actor_user_id
            from entities.review.business.service import ReviewService
            from entities.review_status.business.service import ReviewStatusService

            actor = system_actor_user_id()
            status_service = ReviewStatusService()
            statuses = status_service.read_all()
            current = next(
                (s for s in statuses if s.id == review.review_status_id), None
            )
            if current is None:
                logger.info(
                    "review_notification.current_status_unresolved expense_public_id=%s "
                    "review_status_id=%s",
                    expense.public_id,
                    review.review_status_id,
                )
                return

            in_review = status_service.get_next_intermediate_status(
                current.sort_order, statuses=statuses
            )
            if in_review is None:
                logger.info(
                    "review_notification.in_review_status_missing expense_public_id=%s "
                    "no active non-declined non-final ReviewStatus after "
                    "sort_order=%s",
                    expense.public_id,
                    current.sort_order,
                )
                return

            ReviewService().create(
                review_status_id=in_review.id,
                user_id=review.user_id,
                created_by_user_id=actor,
                comments=None,
                expense_id=expense.id,
                email_message_id=None,
            )
            logger.info(
                "review_notification.in_review_advanced expense_public_id=%s",
                expense.public_id,
            )
        except Exception as in_review_error:
            logger.exception(
                "review_notification.in_review_advance_failed expense_public_id=%s: %s",
                expense.public_id,
                in_review_error,
            )

    def _do_enqueue(self, *, bill, review, exclude_user_id):
        # Lazy imports to avoid circular dependencies with BillService.
        from config import Settings
        from entities.attachment.business.service import AttachmentService
        from entities.bill_line_item.business.service import BillLineItemService
        from entities.bill_line_item_attachment.business.service import (
            BillLineItemAttachmentService,
        )
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

        # 3. Resolve the bill's primary PDF attachment. Bill creation
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

        # 4. Resolve line-item SubCostCode labels for the body table.
        scc_ids = sorted({li.sub_cost_code_id for li in line_items if li.sub_cost_code_id})
        scc_label_by_id: dict = {}
        if scc_ids:
            scs = SubCostCodeService()
            for scc_id in scc_ids:
                s = scs.read_by_id(scc_id)
                if s:
                    scc_label_by_id[scc_id] = f"{s.number} {s.name}".strip() if (s.number or s.name) else None

        # 5. Build subject + HTML body.
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
            attachment_filename=(attachment_payload or {}).get("name"),
        )

        # 6. Enqueue. Always mode="draft" — the worker dispatches
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
            "mode=%s to=%d cc=%d bcc=%d attachment=%s",
            bill.public_id,
            result.public_id,
            mode,
            len(to_with_email),
            len(cc_with_email),
            len(bcc_with_email),
            (attachment_payload or {}).get("name") or "(none)",
        )

        # 7. Advance the Review state once the notification has been enqueued
        # to at least one PM/Owner (TO or CC populated — BCC-only doesn't
        # count). Best-effort; on failure the bill stays at "Submitted" and no
        # other side effects fire.
        if not (to_with_email or cc_with_email):
            return
        self._advance_to_in_review(bill=bill, review=review)

    def _advance_to_in_review(self, *, bill, review) -> None:
        """Write the system's own "moved into review" Review row.

        Extracted from `_do_enqueue`'s step 7 in LS-01c′. It was the tail of a
        250-line method that first has to resolve recipients, build an HTML
        body, base64 a PDF and enqueue an outbox row — so the status-resolution
        and attribution rules below had no reachable test seam at all, which is
        why both of the bugs they fix survived this long.

        Never raises: a failure here leaves the bill at "Submitted" with the
        notification already sent, which is recoverable by a human. Raising
        would not un-send the email.
        """
        try:
            from shared.authz import system_actor_user_id
            from entities.review.business.service import ReviewService
            from entities.review_status.business.service import ReviewStatusService

            # Resolved by username, not hard-coded (U-453, Codex P1). Raises
            # rather than guessing; the handler below logs the miss, which
            # beats attributing machine work to whoever happens to hold an id.
            actor = system_actor_user_id()

            status_service = ReviewStatusService()
            statuses = status_service.read_all()

            # Where the review actually IS, not where a literal assumed it was.
            # The old code hardcoded `sort_order > 10` — correct only while
            # "Submitted" happens to sit at 10.
            current = next(
                (s for s in statuses if s.id == review.review_status_id), None
            )
            if current is None:
                logger.info(
                    "review_notification.current_status_unresolved bill_public_id=%s "
                    "review_status_id=%s",
                    bill.public_id, review.review_status_id,
                )
                return

            # `get_next_intermediate_status`: the next ACTIVE, NON-DECLINED,
            # NON-FINAL status after where this review actually is, resolved
            # deterministically by (sort_order, id).
            #
            # Three things the replaced list-comprehension got wrong: it keyed
            # off the literal 10, it never filtered `is_active` (a retired
            # status was selectable), and it broke ties arbitrarily —
            # `ReadReviewStatuses` orders by SortOrder but carries no `Id`
            # tie-breaker, so `next(...)` over two candidates sharing a
            # SortOrder picked whichever the engine returned first.
            #
            # And one thing the OBVIOUS fix would have gotten wrong (Codex P1):
            # `get_next_status` cannot express "non-final", so pairing it with
            # a refuse-if-final check STRANDS the review at "Submitted" — after
            # its reviewers were already emailed — whenever a final status ties
            # on sort_order with a valid non-final one. `dbo.ReviewStatus` has
            # no unique index on SortOrder and `_assert_shape` does not require
            # one. Asking for the right thing beats asking for the near thing
            # and vetoing bad answers.
            # `statuses` is the snapshot already read above: one read, one
            # snapshot. Letting the selector re-read would put `current` and
            # its candidate on two different snapshots (Codex P2).
            in_review = status_service.get_next_intermediate_status(
                current.sort_order, statuses=statuses
            )
            if in_review is None:
                logger.info(
                    "review_notification.in_review_status_missing bill_public_id=%s "
                    "no active non-declined non-final ReviewStatus after "
                    "sort_order=%s",
                    bill.public_id, current.sort_order,
                )
                return

            ReviewService().create(
                review_status_id=in_review.id,
                # ACTOR = the submitter; AUDIT SUBJECT = the pipeline.
                #
                # `dbo.Review` carries both, and U-463 stopped conflating them.
                # `user_id` is "whose decision or submission this represents"
                # and is what `ReviewTimeline` renders; `CreatedByUserId` is
                # "who wrote the row".
                #
                # LS-01c′ set BOTH to the system actor, reasoning that the
                # machine advanced the state so crediting a human would be a
                # lie. True about the row, wrong about the experience: a person
                # clicked "Submit for Review", and every bill they submitted
                # then read "In Review · by Claude Agent" at the top of its
                # timeline. Reported 2026-09-15.
                #
                # Splitting the two keeps both facts. The timeline names the
                # person who acted; the audit column still records that the
                # notification pipeline wrote the row, so "did a human move this
                # or did the system?" stays answerable in SQL.
                #
                # Safe because U-453 already decoupled the inbox: its
                # `Submitter` CTE resolves the submitter from the latest INITIAL
                # row via the FROZEN [ReviewKind] (U-455), joined independently
                # of the latest row — so this cannot drag in_review bills out of
                # their submitter's `mine_submitted` scope, which is the exact
                # P1 that forced LS-01c′ and U-453 to ship together.
                user_id=review.user_id,
                # Still the pipeline, and still passed EXPLICITLY:
                # `ReviewService.create` otherwise falls back to
                # `current_user_id`, and this path runs with no authz subject,
                # so the sproc's COALESCE(@CreatedByUserId, 17) would credit
                # Christopher for machine work.
                created_by_user_id=actor,
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

    @staticmethod
    def _build_expense_attachment_payload(
        *,
        expense,
        line_items,
        elia_service,
        attachment_service,
        storage,
    ) -> Optional[dict]:
        if not line_items:
            return None
        for li in line_items:
            elia = elia_service.read_by_expense_line_item_id(
                expense_line_item_public_id=str(li.public_id)
            )
            if not elia:
                continue
            attachment = attachment_service.read_by_id(elia.attachment_id)
            if not attachment or not attachment.blob_url:
                continue
            if (attachment.content_type or "").lower() != "application/pdf":
                logger.info(
                    "review_notification.attachment_skipped_non_pdf expense_public_id=%s "
                    "attachment_public_id=%s content_type=%s",
                    expense.public_id,
                    attachment.public_id,
                    attachment.content_type,
                )
                continue
            try:
                content_bytes, _meta = storage.download_file(attachment.blob_url)
            except Exception as e:
                logger.warning(
                    "review_notification.attachment_download_failed expense_public_id=%s "
                    "attachment_public_id=%s: %s",
                    expense.public_id,
                    attachment.public_id,
                    e,
                )
                continue
            return {
                "name": attachment.filename or "expense.pdf",
                "content_type": attachment.content_type or "application/pdf",
                "content_bytes": base64.b64encode(content_bytes).decode("ascii"),
            }
        return None

    @staticmethod
    def _build_expense_subject(
        *,
        vendor_name: str,
        reference_number: Optional[str],
        project_label: str,
        total_amount,
    ) -> str:
        ref_display = reference_number or "(no reference)"
        amount_str = ReviewNotificationService._format_amount(total_amount)
        return (
            f"[Review] {vendor_name} — Expense {ref_display} "
            f"— {project_label} — {amount_str}"
        )

    @classmethod
    def _build_expense_html_body(
        cls,
        *,
        expense,
        vendor_name: str,
        project_label: str,
        submitter_name: str,
        line_items,
        scc_label_by_id: dict,
        to_recipients: Optional[list],
        attachment_filename: Optional[str],
        qbo_url: Optional[str],
    ) -> str:
        reference_number = html.escape(expense.reference_number or "(no reference)")
        vendor = html.escape(vendor_name)
        project = html.escape(project_label)
        submitter = html.escape(submitter_name)
        amount_str = cls._format_amount(expense.total_amount)
        submitted_date = cls._format_submitted_date(expense.created_datetime)

        greeting = ""
        if to_recipients:
            firstnames = [
                html.escape(r.firstname.strip())
                for r in to_recipients
                if getattr(r, "firstname", None) and r.firstname.strip()
            ]
            if firstnames:
                greeting = f"<p>{'/'.join(firstnames)},</p>"

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

        attachment_html = ""
        if attachment_filename:
            attachment_html = (
                f"<p>Attached: <strong>{html.escape(attachment_filename)}</strong></p>"
            )

        qbo_html = ""
        if qbo_url:
            safe_url = html.escape(qbo_url, quote=True)
            qbo_html = (
                f'<p>Open in QuickBooks: <a href="{safe_url}">{safe_url}</a></p>'
            )

        return (
            f"{greeting}"
            "<p>A new expense has been submitted for review:</p>"
            "<p>"
            f"Project: {project}<br/>"
            f"Vendor: {vendor}<br/>"
            f"Reference: {reference_number}<br/>"
            f"Amount: {amount_str}"
            "</p>"
            "<p>"
            f"Submitted By: {submitter}<br/>"
            f"Submitted Date: {submitted_date}"
            "</p>"
            f"{line_rows_html}"
            f"{attachment_html}"
            f"{qbo_html}"
            "<p>When you have a moment, will you please reply for approval "
            "with Sub Cost Code and Description, or non-approval?</p>"
        )

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
        attachment_filename: Optional[str],
    ) -> str:
        """Full HTML body. Sections (in order):
            1. Greeting addressed to PM firstnames (if any).
            2. Lead-in sentence.
            3. Header table: project, vendor, bill #, amount.
            4. Submission table: submitter, date.
            5. Line items table (description, project, SCC, amount).
            6. Attachment hint (optional).
            7. Reviewer instructions.
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
