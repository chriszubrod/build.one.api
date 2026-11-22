"""
Shared pieces of the review-submit email (U-797c2a).

Bill, Expense and Contract Labor each send one review email per record or
(project, date). The pieces that were hand-copied in each service live here:
the `[Review] ` subject prefix, the recipient mapping, the archive BCC, the
slash-joined first-name greeting, the reviewer ask paragraph, and the
record-then-send sequence.

What stays in each service: the subject *body* (vendor / number / project /
amount for a bill; project / date for a crew), the line-item and attachment
resolution, and the recipient resolution. Those differ by type and are pinned
byte-for-byte by tests/test_u797c2a_goldens.py and the U-754 characterization.

Ordering (U-797c2a): the ReviewNotification record is written BEFORE the
enqueue, and its id travels in the payload as `review_notification_id`. The
record is a sidecar. If it cannot be written, the email enqueues exactly as it
did before, without the key. A record failure never blocks or un-sends the email.
"""

import html
import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# The one subject prefix every review email carries.
SUBJECT_PREFIX = "[Review] "

class ReviewEmailComposer:
    """Stateless helpers. Every review email goes through here."""

    # ── subject / recipients / greeting ─────────────────────────────────

    @staticmethod
    def subject(text: str) -> str:
        """`[Review] ` + the type's own subject text."""
        return f"{SUBJECT_PREFIX}{text}"

    @staticmethod
    def address(email: str, name: Optional[str]) -> dict:
        """One outbox recipient, in the `{email, name}` shape the mail client reads."""
        return {"email": email, "name": name}

    @staticmethod
    def archive_bcc(inbox_email: str, name: Optional[str]) -> list:
        """The office archive as a BCC list. Empty is handled by the caller."""
        return [ReviewEmailComposer.address(inbox_email, name)]

    @staticmethod
    def greeting(first_names: Iterable[str], *, dedupe: bool) -> str:
        """`<p>A/B,</p>` from already-stripped, non-empty first names, or "" when none.

        `dedupe` is per type: Contract Labor drops repeated first names, Bill and
        Expense never have."""
        names: list = []
        for name in first_names:
            if dedupe and name in names:
                continue
            names.append(name)
        joined = "/".join(names)
        return f"<p>{html.escape(joined)},</p>" if joined else ""

    # ── record + send ────────────────────────────────────────────────────

    @staticmethod
    def records_or_none(records_factory, context: str = ""):
        """Build the record service lazily. A failure is logged by class name and returns
        None, so the caller sends the email exactly as it did before the record existed."""
        try:
            return records_factory()
        except Exception as error:
            logger.error("review_notification.service_unavailable context=%s error=%s",
                         context, type(error).__name__)
            return None

    @staticmethod
    def send_with_record(*, outbox, records_factory, record, mail: dict, context: str = ""):
        """Enqueue the draft. `record` is an already-written ReviewNotification row, or None.

        The record id goes in the payload only when there is a record; otherwise the call
        is exactly the pre-U-797c2a call. The outbox id is attached to the record after the
        enqueue (audit only). Returns the outbox row, or None when the enqueue was refused.
        """
        kwargs = dict(mail)
        if record is not None:
            kwargs["review_notification_id"] = record.Id
        row = outbox.enqueue_send_mail(**kwargs)
        if row is not None and record is not None:
            records = ReviewEmailComposer.records_or_none(records_factory, context)
            if records is not None:
                records.try_attach_outbox(record.Id, row.id, context=context)
        return row

    @classmethod
    def record_then_send(cls, *, outbox, records_factory, record_kwargs: dict, mail: dict,
                         context: str = ""):
        """Bill / Expense: write the record first, then enqueue.

        With MS writes off the record is not created at all, and the email goes through the
        same enqueue path (which refuses it), exactly as before. A record service that cannot
        be built is treated as no record. A refused or raised enqueue releases the record, so
        no orphan row is left behind. A raised enqueue is re-raised to the caller."""
        from integrations.ms.outbox.business import service as outbox_service

        if not outbox_service.writes_allowed():
            return outbox.enqueue_send_mail(**mail)
        records = cls.records_or_none(records_factory, context)
        if records is None:
            return outbox.enqueue_send_mail(**mail)
        record = records.try_record_for_review(context=context, **record_kwargs)
        try:
            row = cls.send_with_record(outbox=outbox, records_factory=lambda: records,
                                       record=record, mail=mail, context=context)
        except Exception:
            if record is not None:
                records.try_release(record.Id, context=context)
            raise
        if row is None and record is not None:
            records.try_release(record.Id, context=context)
        return row
