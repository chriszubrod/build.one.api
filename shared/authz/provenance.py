"""U-563 — bind emailed reviewer decisions to a real inbound reply.

Per entity:
- **Bill / Expense** — inbound reply only: required EmailMessage public id,
  row resolves, folder ``inbox``, and ``From`` matches ``reviewer_email``
  (normalised). Thread/conversation is **not** bound (v2 review notification
  starts a new Graph conversation; thread anchor deferred to U-563b).
- **Contract Labor** — same inbound checks plus the reply must sit on the
  outbound consolidated notification thread (case-sensitive ``ConversationId``
  match against the stored outbound ``EmailMessage``).

The sender check trusts the ``From`` address as Microsoft Graph reports it.
Authentication-Results / SPF / DKIM / DMARC evidence is not recorded (U-563b).

Replies whose ``ConversationId`` did not survive the round trip (fuzzy-only
matches) cannot be bound automatically — fail closed and route to a human.
"""

from __future__ import annotations

from typing import Optional

from shared.api.errors import ApiError, ErrorCode

class ReviewerProvenanceRefused(ApiError):
    """The supplied reply EmailMessage failed provenance binding."""

    def __init__(self, message: str):
        super().__init__(
            status_code=403,
            detail=message,
            error_code=ErrorCode.REVIEWER_PROVENANCE_REFUSED,
        )


def bind_reviewer_reply(
    *,
    reviewer_email: str,
    reviewer_email_message_public_id: Optional[str],
    what: str,
):
    """Bind a decision to a real inbound reply FROM the asserted reviewer.

    Required id, resolves, folder 'inbox', From == reviewer_email (normalised).
    Returns the EmailMessage. Raises ReviewerProvenanceRefused naming which check
    failed (non-leaky — no entity ids in the refusal text). Does NOT bind the
    thread: Bill/Expense use this alone until U-563b anchors their review thread.
    """
    from entities.email_message.business.service import EmailMessageService

    pub = (reviewer_email_message_public_id or "").strip()
    if not pub:
        raise ReviewerProvenanceRefused(
            f"Cannot apply {what}: the inbound reply EmailMessage public id is required."
        )

    em = EmailMessageService().read_by_public_id(public_id=pub)
    if em is None:
        raise ReviewerProvenanceRefused(
            f"Cannot apply {what}: the inbound reply EmailMessage does not resolve."
        )

    folder = (em.folder or "").strip().lower()
    if folder != "inbox":
        raise ReviewerProvenanceRefused(
            f"Cannot apply {what}: the reply must be an inbound inbox message, "
            f"not an outbound or sent item."
        )

    asserted = (reviewer_email or "").strip().lower()
    sender = (em.from_address or "").strip().lower()
    if not asserted or sender != asserted:
        raise ReviewerProvenanceRefused(
            f"Cannot apply {what}: the reply sender does not match the asserted reviewer."
        )

    return em


def bind_reviewer_reply_to_thread(
    *,
    reviewer_email: str,
    reviewer_email_message_public_id: Optional[str],
    expected_conversation_id: Optional[str],
    what: str,
):
    """`bind_reviewer_reply`, then require the reply to sit on the document's
    review thread: `expected_conversation_id` must be non-blank and equal the
    reply's ConversationId EXACTLY (Graph ids are case-sensitive). Callers own
    how the expected thread is derived — for ContractLabor that derivation is
    where the case-sensitive anchor check lives (see
    `ContractLaborService._expected_cl_reviewer_reply_conversation_id`)."""
    em = bind_reviewer_reply(
        reviewer_email=reviewer_email,
        reviewer_email_message_public_id=reviewer_email_message_public_id,
        what=what,
    )
    expected = (expected_conversation_id or "").strip()
    if not expected:
        raise ReviewerProvenanceRefused(
            f"Cannot apply {what}: the document has no linked email thread for this reply."
        )
    if em.conversation_id != expected:
        raise ReviewerProvenanceRefused(
            f"Cannot apply {what}: the reply is not on this document's email thread."
        )
    return em
