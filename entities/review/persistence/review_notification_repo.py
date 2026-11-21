# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ReviewNotificationRepository:
    """
    Calls the dbo.ReviewNotification sprocs (U-797c1). Thin: one method per sproc,
    params bound by name. Errors are mapped and re-raised; callers decide whether
    a failure blocks anything (the outbound email never does).
    """

    def create(
        self,
        *,
        kind: str,
        review_id: Optional[int],
        subject: Optional[str],
        project_id: Optional[int],
        work_date,
    ) -> Optional[pyodbc.Row]:
        """Insert a notification. For kind='contract_labor' this is the
        (ProjectId, WorkDate) claim: returns None when it already exists."""
        params = {
            "Kind": kind,
            "ReviewId": review_id,
            "Subject": subject,
            "ProjectId": project_id,
            "WorkDate": work_date,
        }
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="CreateReviewNotification", params=params)
                row = cursor.fetchone()
                conn.commit()
                return row
        except Exception as error:
            logger.error("Error during CreateReviewNotification: %s", type(error).__name__)
            raise map_database_error(error)

    def add_target(
        self,
        *,
        review_notification_id: int,
        bill_id: Optional[int],
        expense_id: Optional[int],
        bill_credit_id: Optional[int],
        contract_labor_id: Optional[int],
    ):
        params = {
            "ReviewNotificationId": review_notification_id,
            "BillId": bill_id,
            "ExpenseId": expense_id,
            "BillCreditId": bill_credit_id,
            "ContractLaborId": contract_labor_id,
        }
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="AddReviewNotificationTarget", params=params)
                row = cursor.fetchone()
                conn.commit()
                return row
        except Exception as error:
            logger.error("Error during AddReviewNotificationTarget: %s", type(error).__name__)
            raise map_database_error(error)

    def set_outbox(self, *, id: int, outbox_id: int) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SetReviewNotificationOutbox",
                    params={"Id": id, "OutboxId": outbox_id},
                )
                conn.commit()
        except Exception as error:
            logger.error("Error during SetReviewNotificationOutbox: %s", type(error).__name__)
            raise map_database_error(error)

    def stamp_from_outbox(
        self,
        *,
        outbox_id: int,
        conversation_id: Optional[str],
        internet_message_id: Optional[str],
        draft_graph_message_id: Optional[str],
    ) -> None:
        params = {
            "OutboxId": outbox_id,
            "ConversationId": conversation_id,
            "InternetMessageId": internet_message_id,
            "DraftGraphMessageId": draft_graph_message_id,
        }
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="StampReviewNotificationFromOutbox", params=params)
                conn.commit()
        except Exception as error:
            logger.error("Error during StampReviewNotificationFromOutbox: %s", type(error).__name__)
            raise map_database_error(error)

    def delete(self, *, id: int) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="DeleteReviewNotification", params={"Id": id})
                conn.commit()
        except Exception as error:
            logger.error("Error during DeleteReviewNotification: %s", type(error).__name__)
            raise map_database_error(error)

    def find_for_reply(self, *, conversation_id: str):
        """Return (notifications, targets) for the reply's ConversationId.

        Two result sets: the matching ReviewNotification rows, then their
        ReviewNotificationTarget rows. Exact, case-sensitive match (BIN)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="FindReviewNotificationForReply",
                    params={"ConversationId": conversation_id},
                )
                notifications = cursor.fetchall()
                cursor.nextset()
                targets = cursor.fetchall()
                return notifications, targets
        except Exception as error:
            logger.error("Error during FindReviewNotificationForReply: %s", type(error).__name__)
            raise map_database_error(error)

    def read_open_reply_conversations(self, *, since_days: int = 45) -> list[str]:
        """U-797c3: Graph ConversationIds whose review thread still has an open
        document (see dbo.ReadOpenReviewReplyConversations for the rules)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOpenReviewReplyConversations",
                    params={"SinceDays": since_days},
                )
                return [row[0] for row in cursor.fetchall() if row[0]]
        except Exception as error:
            logger.error("Error during ReadOpenReviewReplyConversations: %s", type(error).__name__)
            raise map_database_error(error)
