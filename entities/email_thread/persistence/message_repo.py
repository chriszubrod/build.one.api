from __future__ import annotations

import base64
import logging
from decimal import Decimal
from typing import Optional

from entities.email_thread.business.message_model import EmailThreadMessage
from shared.database import get_connection, call_procedure, map_database_error

logger = logging.getLogger(__name__)


class EmailThreadMessageRepository:

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _from_db(self, row) -> EmailThreadMessage:
        raw_confidence = getattr(row, "ClassificationConfidence", None)
        return EmailThreadMessage(
            id=                         getattr(row, "Id",                      None),
            public_id=                  getattr(row, "PublicId",                None),
            row_version=                getattr(row, "RowVersion",              None),
            created_datetime=           getattr(row, "CreatedDatetime",         None),
            updated_datetime=           getattr(row, "UpdatedDatetime",         None),
            email_thread_id=            getattr(row, "EmailThreadId",           None),
            inbox_record_id=            getattr(row, "InboxRecordId",           None),
            sender_role=                getattr(row, "SenderRole",              None),
            message_position=           getattr(row, "MessagePosition",         None),
            is_reply=                   bool(row.IsReply)   if getattr(row, "IsReply",   None) is not None else None,
            is_forward=                 bool(row.IsForward) if getattr(row, "IsForward", None) is not None else None,
            classification=             getattr(row, "Classification",          None),
            classification_confidence=  Decimal(str(raw_confidence)) if raw_confidence is not None else None,
            received_datetime=          getattr(row, "ReceivedDatetime",        None),
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create(
        self,
        public_id:                  str,
        email_thread_id:            int,
        inbox_record_id:            int,
        sender_role:                str,
        message_position:           int,
        is_reply:                   bool                = False,
        is_forward:                 bool                = False,
        classification:             Optional[str]       = None,
        classification_confidence:  Optional[Decimal]   = None,
        received_datetime:          Optional[str]       = None,
    ) -> EmailThreadMessage:
        """Create a new message record within an existing thread."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateEmailThreadMessage",
                    params={
                        "PublicId":                 public_id,
                        "EmailThreadId":            email_thread_id,
                        "InboxRecordId":            inbox_record_id,
                        "SenderRole":               sender_role,
                        "MessagePosition":          message_position,
                        "IsReply":                  1 if is_reply else 0,
                        "IsForward":                1 if is_forward else 0,
                        "Classification":           classification,
                        "ClassificationConfidence": Decimal(str(classification_confidence)) if classification_confidence is not None else None,
                        "ReceivedDatetime":         received_datetime,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create email thread message: {error}")
            raise map_database_error(error)

    def update_classification(
        self,
        public_id:                  str,
        classification:             str,
        classification_confidence:  Decimal,
    ) -> EmailThreadMessage:
        """Update classification result after the agent processes the message."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateEmailThreadMessageClassification",
                    params={
                        "PublicId":                 public_id,
                        "Classification":           classification,
                        "ClassificationConfidence": Decimal(str(classification_confidence)),
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update email thread message classification: {error}")
            raise map_database_error(error)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_by_public_id(self, public_id: str) -> Optional[EmailThreadMessage]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadMessageByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read email thread message by public id: {error}")
            raise map_database_error(error)

    def read_by_thread_id(self, email_thread_id: int) -> list[EmailThreadMessage]:
        """Return all messages in a thread ordered by position ascending."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadMessagesByThreadId",
                    params={"EmailThreadId": email_thread_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows]
        except Exception as error:
            logger.error(f"Error during read email thread messages by thread id: {error}")
            raise map_database_error(error)

    def read_latest(self, email_thread_id: int) -> Optional[EmailThreadMessage]:
        """
        Return the most recent message in a thread.
        Used by the process engine to determine current thread position.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadLatestEmailThreadMessage",
                    params={"EmailThreadId": email_thread_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read latest email thread message: {error}")
            raise map_database_error(error)
