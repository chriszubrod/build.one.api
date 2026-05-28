# Python Standard Library Imports
import logging
from decimal import Decimal
from typing import Optional

# Local Imports
from entities.review.persistence.inbox_repo import ReviewInboxRepository
from entities.task.business.feed import TaskCount, TaskRow

logger = logging.getLogger(__name__)


_TASK_TYPE = "Review"


class ReviewTaskFeed:
    """
    Feed that surfaces pending Review rows as TaskRows for the Task inbox.

    All four parent types (Bill, Expense, BillCredit, Invoice) are unioned
    inside `dbo.ReadInboxTasks` — one DB call per inbox request.
    """

    task_type = _TASK_TYPE

    def __init__(self, repo: Optional[ReviewInboxRepository] = None):
        self.repo = repo or ReviewInboxRepository()

    def read_inbox(
        self,
        *,
        current_user_id: int,
        is_system_admin: bool,
        scope: str,
        entity_type: Optional[str],
        status_public_id: Optional[str],
        page: int,
        page_size: int,
    ) -> list[TaskRow]:
        rows = self.repo.read_inbox(
            current_user_id=current_user_id,
            is_system_admin=is_system_admin,
            scope=scope,
            entity_type=entity_type,
            status_public_id=status_public_id,
            page=page,
            page_size=page_size,
        )
        return [self._row_to_task(row) for row in rows]

    def read_counts(
        self,
        *,
        current_user_id: int,
        is_system_admin: bool,
    ) -> list[TaskCount]:
        rows = self.repo.read_inbox_counts(
            current_user_id=current_user_id,
            is_system_admin=is_system_admin,
        )
        return [
            TaskCount(
                task_type=_TASK_TYPE,
                entity_type=row.EntityType,
                is_credit=bool(row.IsCredit),
                mine=int(row.Mine or 0),
                total=int(row.Total or 0),
                mine_submitted=int(row.MineSubmitted or 0),
            )
            for row in rows
        ]

    @staticmethod
    def _row_to_task(row) -> TaskRow:
        amount = row.Amount
        if amount is not None and not isinstance(amount, Decimal):
            amount = Decimal(str(amount))

        return TaskRow(
            task_type=_TASK_TYPE,
            entity_type=row.EntityType,
            is_credit=bool(row.IsCredit),
            parent_public_id=str(row.ParentPublicId),
            parent_id=int(row.ParentId),
            parent_number=row.ParentNumber,
            counterparty_name=row.CounterpartyName,
            amount=amount,
            review_public_id=str(row.ReviewPublicId) if row.ReviewPublicId else None,
            status_name=row.StatusName,
            status_color=row.StatusColor,
            status_sort_order=int(row.StatusSortOrder),
            status_is_final=bool(row.StatusIsFinal),
            status_is_declined=bool(row.StatusIsDeclined),
            submitter_firstname=row.SubmitterFirstname,
            submitter_lastname=row.SubmitterLastname,
            last_activity_at=str(row.LastActivityAt) if row.LastActivityAt else "",
            assigned_to_me=bool(row.AssignedToMe),
        )
