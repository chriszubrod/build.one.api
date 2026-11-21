"""U-797c3: copy reviewer replies on open review threads from the invoice inbox.

Insert path is ``MailboxPollService._ingest_messages`` (idempotent on
InternetMessageId + Folder). Copied rows get ``reviewer_reply`` status, so no agent
claims them. Nothing is applied to a bill, expense or contract labor row here.

Gates, checked in this order before any DB or Graph call:
  * ``review_reply_ingest_enabled`` off -> ``{"skipped": "disabled"}``
  * ``PAUSE_EMAIL_POLL`` set           -> ``{"skipped": "paused"}``
  * ``invoice_inbox_email`` unset      -> ``{"skipped": "no_mailbox"}``
Then ``ms_app_lock(LOCK_NAME)``: not acquired -> ``{"skipped": "locked"}``.
Both entry points run under that lock, so the 10-minute ingest and the daily
catch-up never overlap.

Operational failures (Graph non-200, DB or Graph exceptions) are counted in
``errors`` and logged by exception class only; programming errors still raise.

Watermark hazard: copied replies advance MAX(ReceivedDatetime), the watermark of the
dormant forward poll. Reviving that poll requires excluding ``reviewer_reply`` rows
first (see ``ReadMaxReceivedDatetimeByMailbox``), or it skips earlier inbox mail.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import config
from entities.email_message.business.service import (
    REVIEWER_REPLY_STATUS,
    MailboxPollService,
    _email_poll_paused,
)
from entities.review.persistence.review_notification_repo import (
    ReviewNotificationRepository,
)
from integrations.ms.base.locking import ms_app_lock
from integrations.ms.mail.external import client as mail_client

logger = logging.getLogger(__name__)

FOLDER = "inbox"
LOCK_NAME = "review_reply_ingest"
SINCE_DAYS = 45
# Graph requires the $orderby property (receivedDateTime) to appear in $filter, so a
# per-conversation listing carries this no-op floor (same as copy_review_thread_replies).
ORDERBY_FLOOR = "2000-01-01T00:00:00Z"
PAGE_SIZE = 50
MAX_PAGES = 20
_PROGRAMMING_ERRORS = (AttributeError, KeyError, NameError, TypeError)


def _count_error(counts: dict, where: str, error: BaseException) -> None:
    if isinstance(error, _PROGRAMMING_ERRORS):
        raise error
    counts["errors"] += 1
    logger.error("review_reply_ingest %s failed: %s", where, type(error).__name__)


class ReviewReplyIngestService:
    def __init__(
        self,
        *,
        repo=None,
        poll_service=None,
        mail=None,
        settings_factory: Optional[Callable] = None,
        lock=None,
        clock: Optional[Callable[[], float]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ):
        self._repo = repo
        self._poll = poll_service
        self._mail = mail
        self._settings_factory = settings_factory or config.Settings
        self._lock = lock or ms_app_lock
        self._clock = clock or time.monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))

    @property
    def repo(self):
        if self._repo is None:
            self._repo = ReviewNotificationRepository()
        return self._repo

    @property
    def poll(self):
        if self._poll is None:
            self._poll = MailboxPollService()
        return self._poll

    @property
    def mail(self):
        return self._mail if self._mail is not None else mail_client

    def _gate(self) -> tuple[Optional[dict], Optional[str]]:
        settings = self._settings_factory()
        if not getattr(settings, "review_reply_ingest_enabled", False):
            return {"skipped": "disabled"}, None
        if _email_poll_paused():
            return {"skipped": "paused"}, None
        mailbox = getattr(settings, "invoice_inbox_email", None)
        if not mailbox:
            return {"skipped": "no_mailbox"}, None
        return None, mailbox

    def _run(self, body: Callable[[str], dict]) -> dict:
        skipped, mailbox = self._gate()
        if skipped:
            return skipped
        with self._lock(LOCK_NAME) as acquired:
            if not acquired:
                return {"skipped": "locked"}
            return body(mailbox)

    def _list_matching(self, *, mailbox, filter_query, keep, label, counts,
                       page_size, max_pages) -> list[dict]:
        matched: list[dict] = []
        for page_index in range(max_pages):
            result = self.mail.list_messages(
                folder=FOLDER,
                top=page_size,
                skip=page_index * page_size,
                filter_query=filter_query,
                order_by="receivedDateTime desc",
                mailbox=mailbox,
            )
            if result.get("status_code") != 200:
                counts["errors"] += 1
                logger.error(
                    "review_reply_ingest list_messages %s status=%s",
                    label, result.get("status_code"),
                )
                break
            page = result.get("messages") or []
            counts["listed"] += len(page)
            matched.extend(m for m in page if keep(m))
            if len(page) < page_size:
                break
        return matched

    def _insert(self, *, matched, mailbox, counts) -> None:
        if not matched:
            return
        new_count, _attach, ingest_errors = self.poll._ingest_messages(
            messages=matched,
            mailbox=mailbox,
            folder=FOLDER,
            default_status=REVIEWER_REPLY_STATUS,
            download_attachments=False,
        )
        counts["inserted"] += new_count
        counts["errors"] += len(ingest_errors)

    def ingest_recent(self, *, lookback_hours: int = 3, page_size: int = PAGE_SIZE,
                      max_pages: int = MAX_PAGES) -> dict:
        """List recent inbox mail and copy only messages on open review threads."""

        def body(mailbox: str) -> dict:
            counts = {"open": 0, "listed": 0, "matched": 0, "inserted": 0, "errors": 0}
            try:
                open_set = set(self.repo.read_open_reply_conversations(since_days=SINCE_DAYS))
            except Exception as error:
                _count_error(counts, "read_open", error)
                return counts
            counts["open"] = len(open_set)
            if not open_set:
                return counts

            since = (self._now() - timedelta(hours=lookback_hours)).astimezone(timezone.utc)
            filter_query = f"receivedDateTime ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            matched: list[dict] = []
            try:
                matched = self._list_matching(
                    mailbox=mailbox,
                    filter_query=filter_query,
                    keep=lambda m: m.get("conversation_id") in open_set,
                    label="inbox",
                    counts=counts,
                    page_size=page_size,
                    max_pages=max_pages,
                )
            except Exception as error:
                _count_error(counts, "list_messages", error)
            counts["matched"] = len(matched)
            try:
                self._insert(matched=matched, mailbox=mailbox, counts=counts)
            except Exception as error:
                _count_error(counts, "ingest", error)
            return counts

        return self._run(body)

    def catch_up(self, *, max_seconds: float = 45) -> dict:
        """Per open conversation: list that thread's inbox mail and insert it.

        Stops starting new conversations once ``max_seconds`` is spent. The order is
        a shuffle seeded by the UTC date ordinal: stable within a day, different each
        day. Coverage is probabilistic, not guaranteed: a thread unchecked for d days
        has probability (1 - checked/n)^d of going unchecked, so with a 40% budget a
        thread is missed for 14 days with probability 0.6^14 (about 0.08%). The
        10-minute ingest is the primary path; this walk is the backstop.
        A failing conversation is counted and skipped."""

        def body(mailbox: str) -> dict:
            counts = {"open": 0, "checked": 0, "remaining": 0, "listed": 0,
                      "matched": 0, "inserted": 0, "errors": 0}
            try:
                ids = sorted(set(self.repo.read_open_reply_conversations(since_days=SINCE_DAYS)))
            except Exception as error:
                _count_error(counts, "read_open", error)
                return counts
            total = len(ids)
            counts["open"] = total
            if not total:
                return counts

            order = list(ids)
            random.Random(self._now().date().toordinal()).shuffle(order)
            started = self._clock()
            for conversation_id in order:
                if self._clock() - started >= max_seconds:
                    break
                counts["checked"] += 1
                escaped = conversation_id.replace("'", "''")
                filter_query = (
                    f"receivedDateTime ge {ORDERBY_FLOOR} and "
                    f"conversationId eq '{escaped}'"
                )
                try:
                    matched = self._list_matching(
                        mailbox=mailbox,
                        filter_query=filter_query,
                        keep=lambda m, cid=conversation_id: m.get("conversation_id") == cid,
                        label=f"conversation={conversation_id[-8:]}",
                        counts=counts,
                        page_size=PAGE_SIZE,
                        max_pages=MAX_PAGES,
                    )
                    counts["matched"] += len(matched)
                    self._insert(matched=matched, mailbox=mailbox, counts=counts)
                except Exception as error:
                    _count_error(counts, f"conversation={conversation_id[-8:]}", error)
            counts["remaining"] = total - counts["checked"]
            return counts

        return self._run(body)
