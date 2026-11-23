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
import re
from datetime import date, datetime
from typing import Callable, Optional, Set

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


def _send_mail_review_rows(
    entity_type: str,
    entity_public_id: str,
) -> tuple[set[str], set[str]]:
    from integrations.ms.outbox.persistence.repo import MsOutboxRepository

    rows = MsOutboxRepository().read_completed_by_entity(
        entity_type=entity_type,
        entity_public_id=entity_public_id,
        kind="send_mail",
    )
    return _recorded_and_legacy_subjects_from_send_mail_rows(
        rows, entity_public_id,
    )


def recorded_review_conversation_ids(
    entity_type: str,
    entity_public_id: str,
) -> set[str]:
    """Non-empty ``conversation_id`` values from completed ``send_mail`` outbox
    payloads for this document (exact, case-sensitive). Same row filters as
    ``expected_review_thread_conversation_ids`` uses for its recorded path."""
    recorded, _ = _send_mail_review_rows(entity_type, entity_public_id)
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

    reply_conv = (reply_conversation_id or "").strip()
    if not reply_conv:
        return set()

    recorded, notification_subjects = _send_mail_review_rows(
        entity_type, entity_public_id,
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


# ---------------------------------------------------------------------------
# U-797c1 stage 2: bind through the ReviewNotification record first.
# ---------------------------------------------------------------------------
# The record (dbo.ReviewNotification) is written from c1 on, so every notification
# created after c1 binds here. Notifications created before c1 have no row and
# keep using the legacy bindings above, for this window only. c1 records the
# date; it does not enforce it yet (D4: 60 days after 2026-11-20).
LEGACY_BINDING_RETIRE_AFTER = date(2027, 1, 19)


def _binder_v2_enabled() -> bool:
    from config import Settings

    return bool(Settings().review_reply_binder_v2_enabled)


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _norm_work_date(value) -> Optional[date]:
    """A work date as a `date`, or None when it cannot be parsed (the caller then refuses).

    Accepts date, datetime (its date part) and an ISO 'YYYY-MM-DD' string, optionally followed
    by a time part. Anything else, including '2026-11-5' and '11/20/2026', returns None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip().replace("T", " ").split(" ")[0]
        if not _ISO_DATE.match(text):
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    return None


# U-797c2b: per-kind coverage. "target" = the document must be among the notification's
# targets. "project_date" = Contract Labor: Kind, ProjectId and WorkDate must match the request
# (target membership is not used). A kind not listed here refuses (wrong_document).
COVERAGE_BY_KIND = {
    "bill": "target",
    "expense": "target",
    "bill_credit": "target",
    "contract_labor": "project_date",
}

_REPLY_PREFIX = re.compile(r"^\s*(?:re|fw|fwd)\s*:\s*", re.IGNORECASE)


def normalize_subject(subject) -> Optional[str]:
    """A subject for comparison: reply/forward prefixes removed (repeatedly), whitespace
    collapsed, casefolded. None, or a subject that is blank after that, gives None."""
    if not isinstance(subject, str):
        return None
    text = subject.strip()
    while True:
        stripped = _REPLY_PREFIX.sub("", text, count=1).strip()
        if stripped == text:
            break
        text = stripped
    text = " ".join(text.split()).casefold()
    return text or None


def subject_mismatch(matches, reply_subject) -> bool:
    """True only when the reply's subject is known, EVERY match has a known subject, and none
    of them equals it. Anything less certain returns False, so the decision is today's."""
    reply = normalize_subject(reply_subject)
    if reply is None or not matches:
        return False
    for notification, _targets in matches:
        match_subject = normalize_subject(getattr(notification, "Subject", None))
        if match_subject is None or match_subject == reply:
            return False
    return True


# U-797c2b fix round 1 (F1): the subject guard rescues ONLY these decisions, and only to legacy.
# It never overrides ambiguous or wrong_kind.
_SUBJECT_RESCUABLE_REFUSALS = frozenset({"wrong_project_or_date", "wrong_document"})


def select_notification(matches, expected_target, *, project_id=None, work_date=None, reply_subject=None):
    """Pure decision shared by the live binder and the re-bind proof.

    reply_subject (U-797c2b, the retitled-draft guard): today's decision is computed first, with
    the subject ignored. Only when subject_mismatch(matches, reply_subject) is True AND that
    decision is BOUND, or REFUSED with wrong_project_or_date or wrong_document, the answer is
    ("NO_ROW", "subject_mismatch", None), so the caller falls back to the legacy binding. ambiguous
    and wrong_kind stay refused whatever the subject. No matches stays no_row. The guard never
    binds on a subject and never filters the matches."""
    decision = _decide_ignoring_subject(
        matches, expected_target, project_id=project_id, work_date=work_date)
    status, reason, _notification = decision
    if (status == "BOUND" or (status == "REFUSED" and reason in _SUBJECT_RESCUABLE_REFUSALS)) \
            and subject_mismatch(matches, reply_subject):
        return ("NO_ROW", "subject_mismatch", None)
    return decision


def _decide_ignoring_subject(matches, expected_target, *, project_id=None, work_date=None):
    """The decision with no subject involved (today's select_notification, unchanged in shape).

    matches: [(notification, {(kind, id), ...}), ...] for the reply's ConversationId.
    Returns (status, reason, notification):
      ("NO_ROW", "no_row", None)            no notification on this conversation
      ("REFUSED", "ambiguous", None)        two or more notifications
      ("REFUSED", "wrong_kind", None)       a Contract Labor target on a non-CL notification
      ("REFUSED", "wrong_project_or_date", None)  CL: project or work date does not match
      ("REFUSED", "wrong_document", None)   Bill / Expense: the document is not a target
      ("BOUND", "bound", notification)

    Contract Labor is NOT decided by target membership: a late-added crew member is
    not a target, but the notification for the same project and date still covers
    them. Crew membership is checked live by the caller.
    """
    if not matches:
        return ("NO_ROW", "no_row", None)
    if len(matches) > 1:
        return ("REFUSED", "ambiguous", None)
    notification, targets = matches[0]
    kind = expected_target[0]
    rule = COVERAGE_BY_KIND.get(kind)
    if rule is None:
        # An unknown kind is refused as a wrong document (trunk's reason), not wrong_kind.
        return ("REFUSED", "wrong_document", None)
    if rule == "project_date":
        if getattr(notification, "Kind", None) != kind:
            return ("REFUSED", "wrong_kind", None)
        notification_date = _norm_work_date(getattr(notification, "WorkDate", None))
        request_date = _norm_work_date(work_date)
        same_scope = (
            project_id is not None
            and notification_date is not None
            and request_date is not None
            and getattr(notification, "ProjectId", None) == project_id
            and notification_date == request_date
        )
        if not same_scope:
            return ("REFUSED", "wrong_project_or_date", None)
        return ("BOUND", "bound", notification)
    if expected_target not in targets:
        return ("REFUSED", "wrong_document", None)
    return ("BOUND", "bound", notification)


_REFUSAL_TEXT = {
    "ambiguous": "the reply's email thread matches more than one review notification.",
    "wrong_kind": "the reply's review notification is not a contract labor notification.",
    "wrong_project_or_date": "the reply's review notification is for a different project or work date.",
    "wrong_document": "the reply is not on this document's review email thread.",
}


def bind_reply_via_notification(reply_conversation_id: Optional[str], expected_target,
                                *, project_id=None, work_date=None, reply_subject=None):
    """Look the reply up in the ReviewNotification record and decide with select_notification.

    Returns the notification when bound. Returns None when no notification carries this
    ConversationId (the caller then uses the legacy binding). Raises ReviewerProvenanceRefused
    on any refusal: ambiguous, a notification that does not cover this document, or a
    Contract Labor notification for another project or date. A refusal propagates, except the
    subject-mismatch rescue (wrong_project_or_date and wrong_document), which falls back to legacy.
    """
    from entities.review.business.review_notification_service import (
        ReviewNotificationRecordService,
    )

    if not isinstance(reply_conversation_id, str) or not reply_conversation_id.strip():
        return None
    matches = ReviewNotificationRecordService().lookup_for_reply(reply_conversation_id)
    status, reason, notification = select_notification(
        matches, expected_target, project_id=project_id, work_date=work_date,
        reply_subject=reply_subject)
    if status == "NO_ROW":
        if reason == "subject_mismatch":
            # Ids and the reason only. Never the subject text, on either side.
            logger.warning("review_reply_binder.subject_mismatch notification_ids=%s",
                           [getattr(n, "Id", None) for n, _targets in matches])
        return None
    if status == "REFUSED":
        raise ReviewerProvenanceRefused(
            "Cannot apply the reviewer decision: " + _REFUSAL_TEXT[reason])
    return notification


def bind_reviewer_reply_with_notification(
    *,
    reviewer_email: str,
    reviewer_email_message_public_id: Optional[str],
    target: tuple,
    what: str,
    legacy: Callable[[], object],
    project_id=None,
    work_date=None,
):
    """Flag-gated reply binding shared by Bill, Expense and Contract Labor.

    Flag OFF: returns `legacy()` unchanged (today's binding, byte-for-byte).
    Flag ON: the generic checks (required id, resolves, inbox, From == reviewer)
    run first, then the record binds. A bound record returns the reply. No row
    falls back to `legacy()`. A refusal (ambiguous, or the record excludes this
    document) propagates. The one exception is the subject-mismatch rescue: a mismatch turns a
    wrong_project_or_date or wrong_document refusal into a no-row, which falls back to `legacy()`.
    Ambiguous and wrong_kind always propagate with no fallback.
    """
    em = resolve_reply_once(
        reviewer_email=reviewer_email,
        reviewer_email_message_public_id=reviewer_email_message_public_id,
        target=target,
        what=what,
        project_id=project_id,
        work_date=work_date,
    )
    return em if em is not None else legacy()


def resolve_reply_notification_once(
    *,
    reviewer_email: str,
    reviewer_email_message_public_id: Optional[str],
    target: tuple,
    what: str,
    project_id=None,
    work_date=None,
):
    """U-797c2b / U-797b2: resolve the record decision ONCE for a whole decision (a Contract Labor crew).

    Returns (em, notification) when the reply binds to a ReviewNotification row, else None.
    Flag OFF: returns None with no check and no lookup. The caller then runs the per-document
    binding exactly as today. Flag ON: the generic checks run, then the record is looked up once.
    No row returns None, and the caller runs the per-document legacy binding. A refusal raises,
    as the per-document binder does, before any write. U-797b2 needs the notification itself:
    its snapshots are keyed by the notification id."""
    if not _binder_v2_enabled():
        return None
    em = bind_reviewer_reply(
        reviewer_email=reviewer_email,
        reviewer_email_message_public_id=reviewer_email_message_public_id,
        what=what,
    )
    notification = bind_reply_via_notification(
        em.conversation_id, target, project_id=project_id, work_date=work_date,
        reply_subject=getattr(em, "subject", None))
    return (em, notification) if notification is not None else None


def resolve_reply_once(
    *,
    reviewer_email: str,
    reviewer_email_message_public_id: Optional[str],
    target: tuple,
    what: str,
    project_id=None,
    work_date=None,
):
    """U-797c2b: resolve the record decision ONCE for a whole decision (a Contract Labor crew).

    Thin wrapper over resolve_reply_notification_once. Returns the EmailMessage when the reply
    binds to a record, else None. Flag OFF returns None with no lookup."""
    bound = resolve_reply_notification_once(
        reviewer_email=reviewer_email,
        reviewer_email_message_public_id=reviewer_email_message_public_id,
        target=target,
        what=what,
        project_id=project_id,
        work_date=work_date,
    )
    return bound[0] if bound is not None else None

