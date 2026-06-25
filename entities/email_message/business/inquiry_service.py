# Python Standard Library Imports
import html
import logging
from typing import Literal, Optional

# Local Imports

logger = logging.getLogger(__name__)

# Mode controls which HTML preamble (color + header label) renders on the
# self-forward. Two modes today:
#   "inquiry"      — fired on needs_review; "AGENT REVIEW" yellow callout;
#                    body is findings + ask; AP replies with the answer
#                    and Step 1e applies it.
#   "confirmation" — fired on awaiting_approval / processed (when an
#                    action was taken); "AGENT ACTION" green callout;
#                    body is findings + what-I-did + next-step.
#                    Redirect replies are not yet automated in v1 — AP
#                    sees the action, replies if a change is needed, and
#                    the reply gets flagged for manual handling.
CorrespondenceMode = Literal["inquiry", "confirmation"]


class AgentInquiryService:
    """Sends a self-forward of a polled email to invoice@ with the
    agent's findings (+ ask OR + what-was-done) as an HTML preamble.

    Triggered on every `outcome=needs_review` stamp where `reason` is
    non-empty (mode=inquiry), plus every `outcome=awaiting_approval` or
    `outcome=processed` stamp where `reason` is non-empty AND the
    decided_action represents real work (mode=confirmation). NEVER
    triggered on `outcome=irrelevant` — newsletter/spam outcomes don't
    warrant a self-forward.

    Why a forward (not a new email): forwards preserve the source
    `ConversationId`, so when AP replies, the reply lands in the SAME
    conversation as the source vendor email. Step 1e's detection
    (internal sender + Re: + sibling stamped flagged_needs_review +
    no tracked Bill) then fires on the reply without needing any new
    conversation-tracking token. The source attachment (PDF) auto-rides
    along on the forward, so AP sees the document alongside the message.

    Self-loop hygiene: the forward goes invoice@ -> invoice@. Both
    copies (Sent + Inbox) get polled, but:
      - Sent-folder copy is stamped `outbound` by the poll service
        (doesn't enter pending queue).
      - Inbox copy of an invoice@-from-invoice@ self-send is filtered
        out at the poll layer (entities/email_message/business/service.py
        `_ingest_messages` defensive filter), so it never reaches the
        agent. The email_specialist's Step 0 self-loop guard is
        defense-in-depth.

    Failure semantics: every step is wrapped in an outer try/except. A
    correspondence-forward failure NEVER propagates back to the caller —
    the EmailMessage outcome stamp + red flag stand on their own.
    """

    def send_inquiry(
        self,
        *,
        email_public_id: str,
        question: str,
        confidence: Optional[float] = None,
        mode: CorrespondenceMode = "inquiry",
        bill_public_id: Optional[str] = None,
    ) -> None:
        """Public surface. Drafts + sends a self-forward of the source
        email with the agent's findings rendered as an HTML preamble.

        `mode` controls the preamble style:
          - "inquiry"      — findings + ask (needs_review path).
          - "confirmation" — findings + what-I-did (awaiting_approval /
                             processed path).

        `bill_public_id` — when set AND `Settings.web_base_url` is
        configured, the preamble renders a clickable "View Bill" button
        linking to `{web_base_url}/bill/{public_id}`. Lets AP jump
        straight from the email to the Bill detail page (to run
        complete-bill, adjust coding, etc.).
        """
        try:
            self._do_send(
                email_public_id=email_public_id,
                question=question,
                confidence=confidence,
                mode=mode,
                bill_public_id=bill_public_id,
            )
        except Exception as error:
            logger.exception(
                "agent_inquiry.send_failed email_public_id=%s mode=%s: %s",
                email_public_id,
                mode,
                error,
            )

    def _do_send(
        self,
        *,
        email_public_id: str,
        question: str,
        confidence: Optional[float],
        mode: CorrespondenceMode = "inquiry",
        bill_public_id: Optional[str] = None,
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

        bill_view_url: Optional[str] = None
        if bill_public_id and settings.web_base_url:
            base = settings.web_base_url.rstrip("/")
            bill_view_url = f"{base}/bill/{bill_public_id}"

        html_preamble = self._build_html_preamble(
            email=email,
            question=question,
            confidence=confidence,
            mode=mode,
            bill_view_url=bill_view_url,
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
            "agent_inquiry.enqueued email_public_id=%s mode=%s "
            "outbox_public_id=%s invoice_inbox=%s",
            email_public_id,
            mode,
            result.public_id,
            invoice_inbox,
        )

    @staticmethod
    def _build_html_preamble(
        *,
        email,
        question: str,
        confidence: Optional[float],
        mode: CorrespondenceMode = "inquiry",
        bill_view_url: Optional[str] = None,
    ) -> str:
        """HTML block prepended to the forwarded body.

        Two visual modes:
          - inquiry: yellow "AGENT REVIEW" callout — agent has a
            question; AP reply lands back via Step 1e and applies.
          - confirmation: green "AGENT ACTION" callout — agent took
            an action; AP reply lands flagged for manual handling
            (redirect-via-reply automation is v2 work).

        Question / source values are HTML-escaped to avoid markup
        injection from agent-generated text. Hidden HTML comment
        marker (`<!-- agent-inquiry -->`) included for downstream
        detection if a future flow needs it; primary self-loop guard
        is the poll-layer from-address filter, not the marker.
        """
        escaped_question = html.escape(question)
        vendor_display = html.escape(email.from_name or email.from_address or "(unknown sender)")
        subject_display = html.escape(email.subject or "(no subject)")
        confidence_pct = (
            f" (agent confidence {int(round(confidence * 100))}%)"
            if confidence is not None
            else ""
        )

        if mode == "confirmation":
            # Green callout — "AGENT ACTION" — confirms what the agent
            # did and tells AP what to expect next. v1 reply path is
            # informational only (no autonomous redirect/undo yet).
            header_label = "AGENT ACTION"
            border_color = "#059669"       # emerald-600
            bg_color = "#d1fae5"            # emerald-100
            text_color = "#065f46"          # emerald-800
            hint_color = "#064e3b"          # emerald-900
            reply_hint = (
                "<em>FYI / Reply to redirect:</em> Hit Reply and prefix your "
                "instruction with <strong>@Build.One</strong> so the agent "
                "knows which part is for it. Examples: "
                "<em>'@Build.One change project to MR2-CABIN'</em>, "
                "<em>'@Build.One delete that draft'</em>, "
                "<em>'@Build.One reclassify as a credit memo'</em>. "
                "Replies are flagged for manual follow-up in v1 — automated "
                "redirect/undo is not wired yet."
            )
        else:
            # Yellow callout — "AGENT REVIEW" — agent needs help to
            # resolve. AP reply is parsed by Step 1e and applied.
            header_label = "AGENT REVIEW"
            border_color = "#d97706"        # amber-600
            bg_color = "#fef3c7"             # amber-100
            text_color = "#92400e"           # amber-800
            hint_color = "#78350f"           # amber-900
            reply_hint = (
                "<em>How to reply:</em> Hit Reply and prefix your instruction "
                "with <strong>@Build.One</strong> so the agent knows which "
                "part is for it (rest of the email can be free-form chatter "
                "for your team). Examples: "
                "<em>'@Build.One project is MR2-CABIN'</em>, "
                "<em>'@Build.One skip'</em>, "
                "<em>'@Build.One this is a credit memo'</em>, "
                "<em>'@Build.One create the bill manually'</em>, "
                "<em>'@Build.One route to bill_specialist anyway'</em>."
            )

        # Optional "View Bill" button — renders only when the caller
        # supplied a bill_public_id AND web_base_url was configured (so
        # we have a real URL to link). Outlook treats the <a> tag as a
        # clickable button via the inline styles. Same-origin-only by
        # construction (URL is composed from `settings.web_base_url` —
        # not from user input).
        bill_button_html = ""
        if bill_view_url:
            bill_button_html = (
                f"<p style='margin:0 0 12px 0;'>"
                f"<a href='{html.escape(bill_view_url, quote=True)}' "
                f"style='display:inline-block; padding:8px 16px; "
                f"background:#1f3864; color:#ffffff; text-decoration:none; "
                f"font-family:Arial,Helvetica,sans-serif; font-size:13px; "
                f"font-weight:600; border-radius:4px;'>View Bill in build.one →</a>"
                f"</p>"
            )

        # Anti-phishing footer — surfaces on every correspondence email
        # regardless of mode, so AP doesn't reflex-click on a spoofed
        # message. Cheap defence-in-depth; the real protection is
        # SPF/DKIM/DMARC on the rogersbuild.com domain.
        antiphish_footer = (
            "<p style='font-size:11px; color:#9ca3af; "
            "font-family:Arial,Helvetica,sans-serif; margin:4px 0 0 0;'>"
            "<em>Authenticity check: this email is generated by the build.one "
            "agent and sent from <strong>invoice@rogersbuild.com</strong> to "
            "itself. Verify the sender before clicking links. Never enter "
            "credentials in response to an email — the agent never asks for "
            "them.</em>"
            "</p>"
        )

        return (
            "<!-- agent-inquiry -->"
            f"<div style='border-left:4px solid {border_color}; padding:8px 12px; "
            f"background:{bg_color}; font-family:Arial,Helvetica,sans-serif; "
            f"font-size:14px; color:{text_color}; margin-bottom:12px;'>"
            f"<p style='margin:0 0 8px 0;'><strong>{header_label}</strong>"
            f"{html.escape(confidence_pct)}</p>"
            f"<p style='margin:0 0 8px 0; white-space:pre-wrap;'>{escaped_question}</p>"
            f"{bill_button_html}"
            f"<p style='margin:0 0 0 0; font-size:12px; color:{hint_color};'>"
            f"{reply_hint}"
            "</p>"
            "</div>"
            f"<p style='font-size:12px; color:#6b7280;'>"
            f"Source: <strong>{subject_display}</strong> from {vendor_display}"
            "</p>"
            f"{antiphish_footer}"
            "<hr style='border:none; border-top:1px solid #e5e7eb; margin:8px 0;'/>"
        )

