"""
ReviewNotification record service (U-797c1).

Records one row per outbound review email (dbo.ReviewNotification) and the
documents that email covers (dbo.ReviewNotificationTarget). Writers are the
Bill / Expense / Contract Labor notification services; the MS worker stamps the
Graph ids on draft create; the reply binder (shared/authz/provenance.py) reads
it back.

Failure policy: the record is a sidecar. Writers wrap every call so a failure
here never blocks or rolls back the email. `stamp_from_outbox` never raises.
"""

import logging
from typing import Iterable, Optional

from entities.review.persistence.review_notification_repo import ReviewNotificationRepository

logger = logging.getLogger(__name__)

# Target kind -> the dbo.ReviewNotificationTarget column it fills.
_TARGET_FIELD = {
    "bill": "BillId",
    "expense": "ExpenseId",
    "bill_credit": "BillCreditId",
    "contract_labor": "ContractLaborId",
}


def target_pairs(target_rows: Iterable, notification_id) -> set:
    """(kind, id) pairs for the targets of one notification."""
    pairs: set = set()
    for t in target_rows:
        if getattr(t, "ReviewNotificationId", None) != notification_id:
            continue
        for kind, field in _TARGET_FIELD.items():
            value = getattr(t, field, None)
            if value is not None:
                pairs.add((kind, int(value)))
    return pairs


class ReviewNotificationRecordService:
    """Writes and reads the ReviewNotification record."""

    def __init__(self, repo: Optional[ReviewNotificationRepository] = None):
        self.repo = repo or ReviewNotificationRepository()

    # ------------------------------------------------------------------
    # Writers
    # ------------------------------------------------------------------
    def record_for_review(self, *, kind: str, review_id: int, targets, subject: Optional[str]):
        """One notification for a per-record email (bill / expense / bill_credit).

        `targets` is a list of (kind, id). Returns the notification row."""
        row = self.repo.create(
            kind=kind,
            review_id=review_id,
            subject=subject,
            project_id=None,
            work_date=None,
        )
        if row is None:
            return None
        for target_kind, target_id in targets:
            self._add_target(row.Id, target_kind, target_id)
        return row

    def claim_for_crew(self, *, project_id: int, work_date, subject: Optional[str],
                       contract_labor_ids: Iterable[int]):
        """The Contract Labor (project, date) claim, with one target per crew member.

        Returns None when the claim already exists (no targets are written).
        If a target write fails, the claim is released so a retry can run."""
        row = self.repo.create(
            kind="contract_labor",
            review_id=None,
            subject=subject,
            project_id=project_id,
            work_date=work_date,
        )
        if row is None:
            return None
        try:
            for cl_id in contract_labor_ids:
                self._add_target(row.Id, "contract_labor", cl_id)
        except Exception:
            self.release(row.Id)
            raise
        return row

    def attach_outbox(self, id: int, outbox_id: int) -> None:
        self.repo.set_outbox(id=id, outbox_id=outbox_id)

    def release(self, id: int) -> None:
        self.repo.delete(id=id)

    def stamp_from_outbox(self, *, outbox_id: int, conversation_id: Optional[str],
                          internet_message_id: Optional[str],
                          draft_graph_message_id: Optional[str]) -> None:
        """Called by the MS worker on draft create. Never raises."""
        try:
            self.repo.stamp_from_outbox(
                outbox_id=outbox_id,
                conversation_id=conversation_id,
                internet_message_id=internet_message_id,
                draft_graph_message_id=draft_graph_message_id,
            )
        except Exception as error:
            logger.warning(
                "review_notification.stamp_failed outbox_id=%s error=%s",
                outbox_id, type(error).__name__,
            )

    # ------------------------------------------------------------------
    # Readers
    # ------------------------------------------------------------------
    def lookup_for_reply(self, conversation_id: Optional[str]) -> list:
        """Every notification on this exact ConversationId, with its target pairs.

        Returns [(notification_row, {(kind, id), ...}), ...]. Blank input is []
        with no database call. The caller decides what 0, 1 or 2+ mean."""
        conv = conversation_id if isinstance(conversation_id, str) else ""
        if not conv.strip():
            return []
        notifications, targets = self.repo.find_for_reply(conversation_id=conv)
        return [(n, target_pairs(targets, n.Id)) for n in notifications]

    def _add_target(self, notification_id: int, kind: str, target_id: int):
        field = _TARGET_FIELD[kind]
        return self.repo.add_target(
            review_notification_id=notification_id,
            bill_id=target_id if field == "BillId" else None,
            expense_id=target_id if field == "ExpenseId" else None,
            bill_credit_id=target_id if field == "BillCreditId" else None,
            contract_labor_id=target_id if field == "ContractLaborId" else None,
        )
