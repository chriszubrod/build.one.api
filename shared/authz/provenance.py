"""U-563 — bind emailed reviewer decisions to a real inbound reply.

Per entity:
- **Bill / Expense** — inbound reply: required EmailMessage public id, row
  resolves, folder ``inbox``, ``From`` matches ``reviewer_email`` (normalised),
  and the reply must sit on this document's review-notification thread. The
  primary anchor is the draft ``conversation_id`` recorded on completed
  ``send_mail`` MS outbox payloads when the notification draft was created
  (U-579; mailbox- and subject-independent). **Legacy fallback** (notifications
  enqueued before that stamp): an outbound ``sentitems`` message on the reply
  conversation whose subject exactly matches a completed ``send_mail`` payload
  subject — weaker (shared subjects, sent-items poll, edited draft subjects).
- **Contract Labor** — same inbound checks plus the reply must sit on the
  outbound consolidated notification thread (case-sensitive ``ConversationId``
  match against the stored outbound ``EmailMessage``).

The sender check trusts the ``From`` address as Microsoft Graph reports it.
Authentication-Results / SPF / DKIM / DMARC evidence is not recorded (U-563b
part 2).

Replies whose ``ConversationId`` did not survive the round trip (fuzzy-only
matches) cannot be bound automatically — fail closed and route to a human.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional, Set

from shared.api.errors import ApiError, ErrorCode

logger = logging.getLogger(__name__)

# U-579 (conversation-id stamp) deployed 2026-09-29; only notifications created before this may use the subject fallback.
# The cutoff is a UTC day boundary (MsOutbox.CreatedDatetime is SYSUTCDATETIME()).
LEGACY_THREAD_FALLBACK_CUTOFF = "2026-09-30 00:00:00"

_CREATED_DT_FORMAT = "%Y-%m-%d %H:%M:%S"
_LEGACY_CUTOFF_DT = datetime.strptime(LEGACY_THREAD_FALLBACK_CUTOFF, _CREATED_DT_FORMAT)


def _is_legacy_notification_row(created_datetime: object) -> bool:
    if not isinstance(created_datetime, str) or not created_datetime.strip():
        return False
    try:
        parsed = datetime.strptime(created_datetime.strip(), _CREATED_DT_FORMAT)
    except ValueError:
        return False
    return parsed < _LEGACY_CUTOFF_DT


def _row_matches_entity(row, entity_public_id: str) -> bool:
    row_pid = getattr(row, "entity_public_id", None)
    if isinstance(row_pid, str) and row_pid.strip():
        return row_pid.strip().lower() == (entity_public_id or "").strip().lower()
    return True

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
    failed (non-leaky — no entity ids in the refusal text).
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


def _recorded_and_legacy_subjects_from_send_mail_rows(
    rows,
    entity_public_id: str,
) -> tuple[set[str], set[str]]:
    """Parse completed send_mail outbox rows — recorded conversation ids plus
    legacy notification subjects (pre-cutoff rows only)."""
    recorded: set[str] = set()
    notification_subjects: set[str] = set()
    for row in rows:
        row_kind = getattr(row, "kind", None)
        if isinstance(row_kind, str) and row_kind.strip() and row_kind != "send_mail":
            continue
        if not _row_matches_entity(row, entity_public_id):
            continue
        raw = getattr(row, "payload", None)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        conv_id = parsed.get("conversation_id")
        if isinstance(conv_id, str) and conv_id.strip():
            recorded.add(conv_id)
        if _is_legacy_notification_row(getattr(row, "created_datetime", None)):
            subject = parsed.get("subject")
            if isinstance(subject, str) and subject.strip():
                notification_subjects.add(subject)
    return recorded, notification_subjects


def recorded_review_conversation_ids(
    entity_type: str,
    entity_public_id: str,
) -> set[str]:
    """Non-empty ``conversation_id`` values from completed ``send_mail`` outbox
    payloads for this document (exact, case-sensitive). Same row filters as
    ``expected_review_thread_conversation_ids`` uses for its recorded path."""
    from integrations.ms.outbox.persistence.repo import MsOutboxRepository

    rows = MsOutboxRepository().read_completed_by_entity(
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        kind="send_mail",
    )
    recorded, _ = _recorded_and_legacy_subjects_from_send_mail_rows(
        rows, entity_public_id,
    )
    return recorded


def expected_review_thread_conversation_ids(
    entity_type: str,
    entity_public_id: str,
    reply_conversation_id: str,
) -> Set[str]:
    """Conversation ids where this document's review notification was sent.

    Reads completed ``send_mail`` MS outbox rows for ``(entity_type,
    entity_public_id)``. When any row carries a non-empty string
    ``conversation_id`` in its payload (U-579 draft stamp), returns
    ``{reply_conversation_id}`` only if that id is in the recorded set (exact,
    case-sensitive); otherwise returns empty without consulting subjects or
    EmailMessages. When no row carries a recorded ``conversation_id`` (legacy
    notifications), falls back to matching an outbound ``sentitems`` message on
    ``reply_conversation_id`` whose subject exactly equals a payload ``subject``.
    """
    from entities.email_message.persistence.repo import EmailMessageRepository
    from integrations.ms.outbox.persistence.repo import MsOutboxRepository

    reply_conv = (reply_conversation_id or "").strip()
    if not reply_conv:
        return set()

    rows = MsOutboxRepository().read_completed_by_entity(
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        kind="send_mail",
    )
    recorded, notification_subjects = _recorded_and_legacy_subjects_from_send_mail_rows(
        rows, entity_public_id,
    )

    if recorded:
        return {reply_conv} if reply_conv in recorded else set()

    if not notification_subjects:
        return set()

    logger.warning(
        "review_thread.legacy_subject_fallback",
        extra={
            "event_name": "review_thread.legacy_subject_fallback",
            "entity_type": entity_type,
            "entity_public_id": entity_public_id,
        },
    )

    confirmed: set[str] = set()
    for msg in EmailMessageRepository().read_by_conversation_id(
        conversation_id=reply_conv,
    ):
        folder = (getattr(msg, "folder", None) or "").strip().lower()
        if folder != "sentitems":
            continue
        if (getattr(msg, "processing_status", None) or "") != "outbound":
            continue
        if (getattr(msg, "subject", None) or "") not in notification_subjects:
            continue
        msg_conv = getattr(msg, "conversation_id", None)
        if msg_conv != reply_conv:
            continue
        if msg_conv:
            confirmed.add(msg_conv)
    return confirmed


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
    how the expected thread is derived — Bill/Expense go through
    `bind_reviewer_reply_to_review_thread` instead; ContractLabor uses
    `ContractLaborService._expected_cl_reviewer_reply_conversation_id`."""
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


def bind_reviewer_reply_to_review_thread(
    *,
    entity_type: str,
    entity_public_id: str,
    reviewer_email: str,
    reviewer_email_message_public_id: Optional[str],
    what: str,
):
    """Bill/Expense entry point (U-563b): bind the reply (required id, resolves,
    inbound inbox, From == reviewer) ONCE, then require its conversation to be
    one where this document's review notification was sent
    (`expected_review_thread_conversation_ids`). The cheap sender/folder checks
    run before any outbox read; refusal order matches `bind_reviewer_reply_to_thread`."""
    em = bind_reviewer_reply(
        reviewer_email=reviewer_email,
        reviewer_email_message_public_id=reviewer_email_message_public_id,
        what=what,
    )
    reply_conv = em.conversation_id or ""
    thread_ids = expected_review_thread_conversation_ids(
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        reply_conversation_id=reply_conv,
    )
    if not reply_conv or reply_conv not in thread_ids:
        raise ReviewerProvenanceRefused(
            f"Cannot apply {what}: the reply is not on this document's review email thread."
        )
    return em
