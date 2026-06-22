# Python Standard Library Imports
import html
import logging
from typing import Optional

# Local Imports

logger = logging.getLogger(__name__)


class AgentInquiryService:
    """Sends a self-forward of a polled email to invoice@ with the
    agent's question as an HTML preamble. Triggered when the agent
    stamps `outcome=needs_review` and provides a specific
    human-answerable question in `reason` / `classification_reason`.

    Why a forward (not a new email): forwards preserve the source
    `ConversationId`, so when AP replies to the inquiry forward, the
    reply lands in the SAME conversation as the source vendor email.
    Step 1e's existing detection (internal sender + Re: + sibling
    stamped `flagged_needs_review` + no tracked Bill) then fires on
    the reply without needing any new conversation-tracking token.
    The source attachment (PDF) auto-rides along on the forward, so
    AP sees the document alongside the question.

    Self-loop hygiene: the forward goes invoice@ -> invoice@. Both
    copies (Sent + Inbox) get polled, but:
      - Sent-folder copy is stamped `outbound` by the poll service
        (already handled, doesn't enter pending queue).
      - Inbox copy is `pending` and would normally be classified by
        the agent. To prevent the agent processing its own outbound
        as an "instruction reply", the email_specialist prompt's
        Step 0 self-loop guard detects emails matching
        (from_address == invoice_inbox_email AND subject starts with
        "Fw:") and short-circuits them as
        `internal_forward + marked_irrelevant`.

    Failure semantics: every step is wrapped in an outer try/except.
    An inquiry-forward failure NEVER propagates back to the caller -
    the EmailMessage outcome stamp + red flag stand on their own.
    """

    def send_inquiry(
        self,
        *,
        email_public_id: str,
        question: str,
        confidence: Optional[float] = None,
    ) -> None:
        """Public surface. Drafts + sends a self-forward of the source
        email with the agent's question as an HTML preamble."""
        try:
            self._do_send(
                email_public_id=email_public_id,
                question=question,
                confidence=confidence,
            )
        except Exception as error:
            logger.exception(
                "agent_inquiry.send_failed email_public_id=%s: %s",
                email_public_id,
                error,
            )

    def _do_send(
        self,
        *,
        email_public_id: str,
        question: str,
        confidence: Optional[float],
    ) -> None:
        # Lazy imports to avoid circular dependencies with the
        # EmailMessage service and the MS outbox.
        from config import Settings
        from entities.email_message.business.service import EmailMessageService
        from integrations.ms.outbox.business.service import MsOutboxService

        question = (question or "").strip()
        if not question:
            logger.info(
                "agent_inquiry.skipped email_public_id=%s reason=no_question_text",
                email_public_id,
            )
            return

        settings = Settings()
        invoice_inbox = settings.invoice_inbox_email
        if not invoice_inbox:
            logger.warning(
                "agent_inquiry.skipped email_public_id=%s reason=invoice_inbox_email_unset",
                email_public_id,
            )
            return

        email = EmailMessageService().read_by_public_id(public_id=email_public_id)
        if email is None:
            logger.warning(
                "agent_inquiry.skipped email_public_id=%s reason=email_not_found",
                email_public_id,
            )
            return
        if not email.graph_message_id or not email.mailbox_address:
            logger.warning(
                "agent_inquiry.skipped email_public_id=%s "
                "reason=source_email_missing_graph_message_id_or_mailbox",
                email_public_id,
            )
            return

        html_preamble = self._build_html_preamble(
            email=email,
            question=question,
            confidence=confidence,
        )

        # Enqueue forward via the outbox. mode=send so the inquiry
        # arrives in invoice@ inbox immediately (drafts would require
        # AP to dig in their Drafts folder to send). forward_message_id
        # set so the worker dispatches to forward_message instead of
        # send_message. The forward inherits the source's body +
        # attachments + ConversationId.
        result = MsOutboxService().enqueue_send_mail(
            entity_type="EmailMessage",
            entity_public_id=str(email.public_id),
            to_addresses=[{"email": invoice_inbox, "name": "Invoice Inbox"}],
            cc_addresses=[],
            bcc_addresses=[],
            # Subject / body / attachment are ignored on the forward
            # path - Graph inherits them from the source. We pass
            # placeholders so the outbox payload schema stays
            # consistent with the non-forward send_mail callers.
            subject="",
            body="",
            body_type="HTML",
            attachment=None,
            mode="send",
            forward_message_id=email.graph_message_id,
            html_preamble=html_preamble,
        )

        if result is None:
            logger.info(
                "agent_inquiry.enqueue_refused email_public_id=%s "
                "reason=ms_writes_gate",
                email_public_id,
            )
            return

        logger.info(
            "agent_inquiry.enqueued email_public_id=%s outbox_public_id=%s "
            "invoice_inbox=%s",
            email_public_id,
            result.public_id,
            invoice_inbox,
        )

    @staticmethod
    def _build_html_preamble(*, email, question: str, confidence: Optional[float]) -> str:
        """HTML block prepended to the forwarded body. Format:

            **AGENT QUESTION** (confidence X%)

            {question}

            How to reply: just hit Reply and answer in plain English.
            Your reply lands back in invoice@ and the agent picks it
            up automatically. Examples: ...

            ---

        Question / source values are HTML-escaped to avoid markup
        injection from agent-generated text. Hidden HTML comment
        marker (`<!-- agent-inquiry -->`) included as a redundant
        signal in case future flows need it, but the primary
        self-loop guard is the from-address + Fw: subject check in
        the email_specialist prompt.
        """
        escaped_question = html.escape(question)
        vendor_display = html.escape(email.from_name or email.from_address or "(unknown sender)")
        subject_display = html.escape(email.subject or "(no subject)")
        confidence_pct = (
            f" (agent confidence {int(round(confidence * 100))}%)"
            if confidence is not None
            else ""
        )

        return (
            "<!-- agent-inquiry -->"
            "<div style='border-left:4px solid #d97706; padding:8px 12px; "
            "background:#fef3c7; font-family:Arial,Helvetica,sans-serif; "
            "font-size:14px; color:#92400e; margin-bottom:12px;'>"
            f"<p style='margin:0 0 8px 0;'><strong>AGENT QUESTION</strong>"
            f"{html.escape(confidence_pct)}</p>"
            f"<p style='margin:0 0 8px 0; white-space:pre-wrap;'>{escaped_question}</p>"
            "<p style='margin:0 0 0 0; font-size:12px; color:#78350f;'>"
            "<em>How to reply:</em> Hit Reply and answer in plain English. "
            "Your reply lands back in invoice@ and the agent picks it up "
            "automatically. Examples: <em>'project is MR2-CABIN'</em>, "
            "<em>'skip'</em>, <em>'this is a credit memo'</em>, "
            "<em>'route to bill_specialist anyway'</em>."
            "</p>"
            "</div>"
            f"<p style='font-size:12px; color:#6b7280;'>"
            f"Source: <strong>{subject_display}</strong> from {vendor_display}"
            "</p>"
            "<hr style='border:none; border-top:1px solid #e5e7eb; margin:8px 0;'/>"
        )
