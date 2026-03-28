from __future__ import annotations

import logging
from typing import Optional

from entities.email_thread.business.stage_history_model import EmailThreadStageHistory
from shared.database import get_connection, call_procedure, map_database_error

logger = logging.getLogger(__name__)


class EmailThreadStageHistoryRepository:
    """
    Append-only repository for EmailThreadStageHistory.
    No update or delete methods exist by design — stage history
    is an immutable audit trail.
    """

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _from_db(self, row) -> EmailThreadStageHistory:
        return EmailThreadStageHistory(
            id=                     getattr(row, "Id",                      None),
            public_id=              getattr(row, "PublicId",                None),
            row_version=            getattr(row, "RowVersion",              None),
            created_datetime=       getattr(row, "CreatedDatetime",         None),
            email_thread_id=        getattr(row, "EmailThreadId",           None),
            from_stage=             getattr(row, "FromStage",               None),
            to_stage=               getattr(row, "ToStage",                 None),
            triggered_by=           getattr(row, "TriggeredBy",             None),
            user_id=                getattr(row, "UserId",                  None),
            email_thread_message_id=getattr(row, "EmailThreadMessageId",    None),
            notes=                  getattr(row, "Notes",                   None),
            transition_datetime=    getattr(row, "TransitionDatetime",      None),
        )

    # ------------------------------------------------------------------
    # Write (append only)
    # ------------------------------------------------------------------

    def create(
        self,
        public_id:              str,
        email_thread_id:        int,
        from_stage:             str,
        to_stage:               str,
        triggered_by:           str,
        user_id:                Optional[int]   = None,
        email_thread_message_id: Optional[int]  = None,
        notes:                  Optional[str]   = None,
        transition_datetime:    Optional[str]   = None,
    ) -> EmailThreadStageHistory:
        """
        Write a new stage transition record.
        triggered_by should be an EventType value from the process engine.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateEmailThreadStageHistory",
                    params={
                        "PublicId":                 public_id,
                        "EmailThreadId":            email_thread_id,
                        "FromStage":                from_stage,
                        "ToStage":                  to_stage,
                        "TriggeredBy":              triggered_by,
                        "UserId":                   user_id,
                        "EmailThreadMessageId":     email_thread_message_id,
                        "Notes":                    notes,
                        "TransitionDatetime":       transition_datetime,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create email thread stage history: {error}")
            raise map_database_error(error)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_by_public_id(self, public_id: str) -> Optional[EmailThreadStageHistory]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadStageHistoryByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read email thread stage history by public id: {error}")
            raise map_database_error(error)

    def read_by_thread_id(
        self, email_thread_id: int
    ) -> list[EmailThreadStageHistory]:
        """Full audit trail for a thread in chronological order."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadStageHistoryByThreadId",
                    params={"EmailThreadId": email_thread_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows]
        except Exception as error:
            logger.error(f"Error during read email thread stage history by thread id: {error}")
            raise map_database_error(error)

    def read_latest(
        self, email_thread_id: int
    ) -> Optional[EmailThreadStageHistory]:
        """
        Return the most recent stage transition for a thread.
        Used by the process engine to confirm current stage after an advance.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadLatestEmailThreadStageHistory",
                    params={"EmailThreadId": email_thread_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read latest email thread stage history: {error}")
            raise map_database_error(error)

    def read_threads_exceeding_sla(
        self, max_hours: int
    ) -> list[dict]:
        """
        Return threads where the current stage has been held longer than
        max_hours. Used by the APScheduler to fire SLA_BREACH events.
        Returns raw dicts rather than model instances — the result set
        includes computed columns (HoursInStage, StalledAtStage) that
        don't exist on the EmailThreadStageHistory model.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadsExceedingStageDuration",
                    params={"MaxHours": max_hours},
                )
                rows = cursor.fetchall()
                if not rows:
                    return []
                columns = [col[0] for col in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
        except Exception as error:
            logger.error(f"Error during read email threads exceeding sla: {error}")
            raise map_database_error(error)
