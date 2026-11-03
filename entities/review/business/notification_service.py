# Python Standard Library Imports
import html
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Optional

# Third-party Imports

# Local Imports
from entities.review.business.recipient_service import ReviewRecipientService

logger = logging.getLogger(__name__)


# ─── per-entity spec ────────────────────────────────────────────────────────
#
# U-754: bills and expenses share ONE notification pipeline. Everything that
# genuinely differs between the two lives in a `_NotificationSpec`; everything
# else is the same code. The hooks below import lazily (circular deps with the
# entity services), exactly where the old per-entity methods did.


def _bill_envelope(bill, exclude_user_id) -> dict:
    return ReviewRecipientService().resolve_for_bill(
        bill_id=bill.id,
        exclude_user_id=exclude_user_id,
    )


def _expense_envelope(expense, exclude_user_id) -> dict:
    # Preserved as shipped (U-486 C1): the expense path reads through
    # `ReviewRepository`, not `ReviewRecipientService().resolve_for_expense`.
    # Both call `ResolveReviewRecipientsByExpenseId`; switching repos is a
    # behaviour change (different error handling) and out of U-754's scope.
    from entities.review.persistence.repo import ReviewRepository

    rows = ReviewRepository().resolve_review_recipients_by_expense_id(
        expense_id=expense.id,
        exclude_user_id=exclude_user_id,
    )
    return ReviewRecipientService._bucket(rows)


def _bill_line_items(bill) -> list:
    from entities.bill_line_item.business.service import BillLineItemService

    return BillLineItemService().read_by_bill_id(bill.id) or []


def _expense_line_items(expense) -> list:
    from entities.expense_line_item.business.service import ExpenseLineItemService

    return ExpenseLineItemService().read_by_expense_id(expense.id) or []


def _bill_link_service():
    from entities.bill_line_item_attachment.business.service import (
        BillLineItemAttachmentService,
    )

    return BillLineItemAttachmentService()


def _expense_link_service():
    from entities.expense_line_item_attachment.business.service import (
        ExpenseLineItemAttachmentService,
    )

    return ExpenseLineItemAttachmentService()


def _bill_read_link(link_service, line_item):
    return link_service.read_by_bill_line_item_id(str(line_item.public_id))


def _expense_read_link(link_service, line_item):
    return link_service.read_by_expense_line_item_id(
        expense_line_item_public_id=str(line_item.public_id)
    )


@dataclass(frozen=True)
class _NotificationSpec:
    """What differs between a bill and an expense review notification."""

    kind: str                      # "bill" | "expense" — log key + body lead-in noun
    entity_type: str               # outbox EntityType
    review_parent_kwarg: str       # parent-id kwarg on `ReviewService.create`
    outbox_parent_kwarg: Optional[str]  # parent-id kwarg on `enqueue_send_mail` (bill only)
    number_attr: str               # the document-number attribute on the parent
    number_placeholder: str        # shown when that number is empty
    subject_number_prefix: str     # "Bill #" | "Expense "
    body_number_label: str         # "Number" | "Reference"
    renders_memo: bool             # expense body carries the memo (U-729); bill does not
    default_attachment_name: str   # filename fallback for an unnamed PDF
    resolve_envelope: Callable[[Any, Optional[int]], dict]
    read_line_items: Callable[[Any], list]
    link_service: Callable[[], Any]
    read_link: Callable[[Any, Any], Any]

    @property
    def log_key(self) -> str:
        return f"{self.kind}_public_id"


BILL_SPEC = _NotificationSpec(
    kind="bill",
    entity_type="Bill",
    review_parent_kwarg="bill_id",
    outbox_parent_kwarg="bill_id",
    number_attr="bill_number",
    number_placeholder="(no number)",
    subject_number_prefix="Bill #",
    body_number_label="Number",
    renders_memo=False,
    default_attachment_name="bill.pdf",
    resolve_envelope=_bill_envelope,
    read_line_items=_bill_line_items,
    link_service=_bill_link_service,
    read_link=_bill_read_link,
)

EXPENSE_SPEC = _NotificationSpec(
    kind="expense",
    entity_type="Expense",
    review_parent_kwarg="expense_id",
    outbox_parent_kwarg=None,
    number_attr="reference_number",
    number_placeholder="(no reference)",
    subject_number_prefix="Expense ",
    body_number_label="Reference",
    renders_memo=True,
    default_attachment_name="expense.pdf",
    resolve_envelope=_expense_envelope,
    read_line_items=_expense_line_items,
    link_service=_expense_link_service,
    read_link=_expense_read_link,
)


class ReviewNotificationService:
    """
    Builds + enqueues review-submit notification emails for bills and expenses.
    Called from `ReviewService.create` on the initial submit, once the Review
    row has been written.

    Design (v2, 2026-05-28): purpose-built new email with the parent's
    bridged PDF attached + optional deep-link to the source vendor email.
    Replaces the v1 "forward-of-original" pattern. Rationale:

      - Bills sourced from multi-invoice emails (a vendor statement with
        N attached invoices) now get a focused per-bill email rather
        than dragging the whole vendor thread into every notification.
      - Renamed / content-type-normalized / format-converted attachments
        (HEIC -> PDF, octet-stream -> application/pdf) are what's on the
        bill. The reviewer sees that file, not whatever Graph still has
        on the original vendor email.
      - Bills with no `SourceEmailMessageId` (manual UI, the retired
        bill_folder intake, agent-synthesized) are no longer silently skipped — they
        get the same review email as email-sourced bills.

    Bills and expenses run ONE pipeline (`_do_enqueue`), parameterised by a
    `_NotificationSpec` (U-754). Expense was added by U-486 C1 as a near-copy.

    Failure semantics: every step is wrapped in an outer try/except. A
    notification failure NEVER propagates back to the caller — the parent
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
        enqueues an `[ms].[Outbox]` `send_mail` row (always mode="draft")."""
        try:
            self._do_enqueue(
                spec=BILL_SPEC,
                parent=bill,
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
            self._do_enqueue(
                spec=EXPENSE_SPEC,
                parent=expense,
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

    def _do_enqueue(self, *, spec: _NotificationSpec, parent, review, exclude_user_id):
        # Lazy imports to avoid circular dependencies with the entity services.
        from config import Settings
        from entities.attachment.business.service import AttachmentService
        from entities.project.business.service import ProjectService
        from entities.sub_cost_code.business.service import SubCostCodeService
        from entities.user.business.service import UserService
        from entities.vendor.business.service import VendorService
        from integrations.ms.outbox.business.service import MsOutboxService

        key = spec.log_key

        # 1. Resolve recipients (PMs To, Owners Cc, invoice@ Bcc).
        envelope = spec.resolve_envelope(parent, exclude_user_id)
        to_with_email = [r for r in envelope["to"] if r.email]
        cc_with_email = [r for r in envelope["cc"] if r.email]
        unreachable = [r for r in (envelope["to"] + envelope["cc"]) if not r.email]
        if unreachable:
            logger.warning(
                f"review_notification.unreachable_recipients {key}=%s "
                "user_ids=%s reason=no_contact_email",
                parent.public_id,
                [r.user_id for r in unreachable],
            )
        if not to_with_email:
            logger.warning(
                f"review_notification.no_to_recipient {key}=%s "
                "reason=no_project_manager_with_email — sending anyway "
                "(BCC archive still active).",
                parent.public_id,
            )
        if not cc_with_email:
            logger.info(
                f"review_notification.no_cc_recipient {key}=%s "
                "reason=no_owner_with_email",
                parent.public_id,
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
                f"review_notification.bcc_archive_skipped {key}=%s "
                "reason=invoice_inbox_email_not_configured",
                parent.public_id,
            )

        if not to_with_email and not cc_with_email and not bcc_with_email:
            logger.error(
                f"review_notification.skipped {key}=%s "
                "reason=no_recipients_on_any_line",
                parent.public_id,
            )
            return

        # 2. Resolve denormalized labels.
        vendor_name = "(unknown vendor)"
        if parent.vendor_id is not None:
            vendor = VendorService().read_by_id(parent.vendor_id)
            if vendor and vendor.name:
                vendor_name = vendor.name

        line_items = spec.read_line_items(parent)
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

        # 3. Resolve the primary PDF attachment. Bill creation mandates one
        # PDF on the first line item (expenses carry the receipt the same
        # way, via ExpenseLineItemAttachment); later lines may carry additional
        # attachments but we send only the canonical first one to keep the
        # email focused (reviewer can open more via the web UI if needed).
        attachment_payload = self._build_attachment_payload(
            spec=spec,
            parent=parent,
            line_items=line_items,
            link_service=spec.link_service(),
            attachment_service=AttachmentService(),
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
            spec=spec,
            vendor_name=vendor_name,
            number=getattr(parent, spec.number_attr),
            project_label=project_label,
            total_amount=parent.total_amount,
        )
        body_html = self._build_html_body(
            spec=spec,
            parent=parent,
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
        parent_link = (
            {spec.outbox_parent_kwarg: parent.id} if spec.outbox_parent_kwarg else {}
        )
        result = MsOutboxService().enqueue_send_mail(
            entity_type=spec.entity_type,
            entity_public_id=parent.public_id,
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
            **parent_link,
            # forward_message_id deliberately omitted — v2 is new-email.
        )

        if result is None:
            logger.info(
                f"review_notification.enqueue_refused {key}=%s reason=ms_writes_gate",
                parent.public_id,
            )
            return

        logger.info(
            f"review_notification.enqueued {key}=%s outbox_public_id=%s "
            "mode=%s to=%d cc=%d bcc=%d attachment=%s",
            parent.public_id,
            result.public_id,
            mode,
            len(to_with_email),
            len(cc_with_email),
            len(bcc_with_email),
            (attachment_payload or {}).get("name") or "(none)",
        )

        # 7. Advance the Review state once the notification has been enqueued
        # to at least one PM/Owner (TO or CC populated — BCC-only doesn't
        # count). Best-effort; on failure the parent stays at "Submitted" and
        # no other side effects fire.
        if not (to_with_email or cc_with_email):
            return
        self._advance_to_in_review(spec=spec, parent=parent, review=review)

    def _advance_to_in_review(self, *, spec: _NotificationSpec, parent, review) -> None:
        """Write the system's own "moved into review" Review row.

        Extracted from `_do_enqueue`'s step 7 in LS-01c′. It was the tail of a
        250-line method that first has to resolve recipients, build an HTML
        body, resolve a PDF attachment and enqueue an outbox row — so the
        status-resolution and attribution rules below had no reachable test
        seam at all, which is why both of the bugs they fix survived this long.

        Never raises: a failure here leaves the parent at "Submitted" with the
        notification already sent, which is recoverable by a human. Raising
        would not un-send the email.
        """
        key = spec.log_key
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
                    f"review_notification.current_status_unresolved {key}=%s "
                    "review_status_id=%s",
                    parent.public_id, review.review_status_id,
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
                    f"review_notification.in_review_status_missing {key}=%s "
                    "no active non-declined non-final ReviewStatus after "
                    "sort_order=%s",
                    parent.public_id, current.sort_order,
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
                # bill_id= or expense_id=, per spec.
                **{spec.review_parent_kwarg: parent.id},
                # The BCC archive (sent back into invoice@) lands via
                # the next poll cycle — we don't have its EmailMessage
                # row at enqueue time. A follow-up backfill job links
                # the archive to this Review row by ConversationId match.
                email_message_id=None,
            )
            logger.info(
                f"review_notification.in_review_advanced {key}=%s",
                parent.public_id,
            )
        except Exception as in_review_error:
            logger.exception(
                f"review_notification.in_review_advance_failed {key}=%s: %s",
                parent.public_id, in_review_error,
            )

    # ─── attachment lookup ──────────────────────────────────────────────────

    @staticmethod
    def _build_attachment_payload(
        *,
        spec: _NotificationSpec,
        parent,
        line_items,
        link_service,
        attachment_service,
        storage=None,
    ) -> Optional[dict]:
        """Resolve the parent's primary PDF attachment as an outbox *reference*.

        Returns ``{"name", "content_type", "blob_url"}`` so the ``send_mail``
        worker can fetch bytes at drain time. Does not download or base64-encode
        the blob — that was the submit-button latency. Returns None when no
        attachment is linked or when the file isn't a PDF (defensive — bill
        create enforces PDF, but legacy data may not).

        ``storage`` is accepted so tests can inject a double and assert it is
        never called. Enqueue must not touch Azure Blob Storage. A linked
        attachment with no ``blob_url`` is skipped — a ``blob_url: None``
        reference would ValueError-dead-letter at drain on attempt 1.
        """
        if not line_items:
            return None
        # Walk line items in DB order; first one with a linked PDF wins.
        for li in line_items:
            link = spec.read_link(link_service, li)
            if not link:
                continue
            attachment = attachment_service.read_by_id(link.attachment_id)
            if not attachment or not attachment.blob_url:
                continue
            if (attachment.content_type or "").lower() != "application/pdf":
                logger.info(
                    f"review_notification.attachment_skipped_non_pdf {spec.log_key}=%s "
                    "attachment_public_id=%s content_type=%s",
                    parent.public_id, attachment.public_id, attachment.content_type,
                )
                continue
            return {
                "name":         attachment.filename or spec.default_attachment_name,
                "content_type": attachment.content_type or "application/pdf",
                "blob_url":     attachment.blob_url,
            }
        return None

    # ─── subject + body builders ────────────────────────────────────────────

    @staticmethod
    def _build_subject(
        *,
        spec: _NotificationSpec,
        vendor_name: str,
        number: Optional[str],
        project_label: str,
        total_amount,
    ) -> str:
        """`[Review] <Vendor> — Bill #<num> — <Project> — $<amt>` (bill) or
        `[Review] <Vendor> — Expense <ref> — <Project> — $<amt>` (expense)."""
        number_display = number or spec.number_placeholder
        amount_str = ReviewNotificationService._format_amount(total_amount)
        return (
            f"[Review] {vendor_name} — {spec.subject_number_prefix}{number_display} "
            f"— {project_label} — {amount_str}"
        )

    @classmethod
    def _build_html_body(
        cls,
        *,
        spec: _NotificationSpec,
        parent,
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
            3. Header block: project, vendor, number/reference, amount
               (+ memo, expense only — U-729).
            4. Submission block: submitter, date.
            5. Line items table (description, project, SCC, amount).
            6. Attachment hint (optional).
            7. Reviewer instructions.
        Vendor / project / submitter values are HTML-escaped so a value
        containing `<` or `&` doesn't render as broken markup."""
        number = html.escape(getattr(parent, spec.number_attr) or spec.number_placeholder)
        vendor = html.escape(vendor_name)
        project = html.escape(project_label)
        submitter = html.escape(submitter_name)
        amount_str = cls._format_amount(parent.total_amount)
        submitted_date = cls._format_submitted_date(parent.created_datetime)

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
        # Keeps each row to: description, project (if multi-project parent),
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

        memo_line = ""
        if spec.renders_memo:
            memo = (getattr(parent, "memo", None) or "").strip()
            if memo:
                memo_html = (
                    html.escape(memo).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
                )
                memo_line = f"<br/>Memo: {memo_html}"

        return (
            f"{greeting}"
            f"<p>A new {spec.kind} has been submitted for review:</p>"
            "<p>"
            f"Project: {project}<br/>"
            f"Vendor: {vendor}<br/>"
            f"{spec.body_number_label}: {number}<br/>"
            f"Amount: {amount_str}{memo_line}"
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
        """Reformat the parent's created datetime as mm/dd/yyyy."""
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
