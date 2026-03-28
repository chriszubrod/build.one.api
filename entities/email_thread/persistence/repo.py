from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

from entities.email_thread.business.model import EmailThread
from shared.database import get_connection, call_procedure, map_database_error

logger = logging.getLogger(__name__)


class EmailThreadRepository:

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _from_db(self, row) -> EmailThread:
        raw_confidence = getattr(row, "ClassificationConfidence", None)
        return EmailThread(
            id=                         getattr(row, "Id",                      None),
            public_id=                  getattr(row, "PublicId",                None),
            row_version=                getattr(row, "RowVersion",              None),
            created_datetime=           getattr(row, "CreatedDatetime",         None),
            updated_datetime=           getattr(row, "UpdatedDatetime",         None),
            inbox_record_id=            getattr(row, "InboxRecordId",           None),
            category=                   getattr(row, "Category",                None),
            process_type=               getattr(row, "ProcessType",             None),
            current_stage=              getattr(row, "CurrentStage",            None),
            is_reply=                   bool(row.IsReply)    if getattr(row, "IsReply",    None) is not None else None,
            is_forward=                 bool(row.IsForward)  if getattr(row, "IsForward",  None) is not None else None,
            internet_message_id=        getattr(row, "InternetMessageId",       None),
            subject=                    getattr(row, "Subject",                 None),
            owner_user_id=              getattr(row, "OwnerUserId",             None),
            classification_confidence=  Decimal(str(raw_confidence)) if raw_confidence is not None else None,
            is_resolved=                bool(row.IsResolved)      if getattr(row, "IsResolved",      None) is not None else None,
            requires_action=            bool(row.RequiresAction)  if getattr(row, "RequiresAction",  None) is not None else None,
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(
        self,
        public_id:                  str,
        inbox_record_id:            int,
        category:                   str,
        process_type:               str,
        current_stage:              str,
        is_reply:                   bool,
        is_forward:                 bool,
        internet_message_id:        Optional[str]   = None,
        subject:                    Optional[str]   = None,
        owner_user_id:              Optional[int]   = None,
        classification_confidence:  Optional[Decimal] = None,
        is_resolved:                Optional[bool]  = None,
        requires_action:            Optional[bool]  = None,
    ) -> EmailThread:
        """Create or update an EmailThread. Matches on PublicId."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertEmailThread",
                    params={
                        "PublicId":                 public_id,
                        "InboxRecordId":            inbox_record_id,
                        "Category":                 category,
                        "ProcessType":              process_type,
                        "CurrentStage":             current_stage,
                        "IsReply":                  1 if is_reply else 0,
                        "IsForward":                1 if is_forward else 0,
                        "InternetMessageId":        internet_message_id,
                        "Subject":                  subject,
                        "OwnerUserId":              owner_user_id,
                        "ClassificationConfidence": float(classification_confidence) if classification_confidence is not None else None,
                        "IsResolved":               (1 if is_resolved else 0) if is_resolved is not None else None,
                        "RequiresAction":           (1 if requires_action else 0) if requires_action is not None else None,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during upsert email thread: {error}")
            raise map_database_error(error)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_by_public_id(self, public_id: str) -> Optional[EmailThread]:
        """Primary public-facing lookup."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read email thread by public id: {error}")
            raise map_database_error(error)

    def read_by_inbox_record_id(self, inbox_record_id: int) -> Optional[EmailThread]:
        """Find the thread started by a specific InboxRecord."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadByInboxRecordId",
                    params={"InboxRecordId": inbox_record_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read email thread by inbox record id: {error}")
            raise map_database_error(error)

    def read_by_internet_message_id(
        self, internet_message_id: str
    ) -> Optional[EmailThread]:
        """
        Thread dedup lookup — find an existing thread by RFC 2822 Message-ID.
        Called during email ingest before creating a new thread.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadByInternetMessageId",
                    params={"InternetMessageId": internet_message_id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read email thread by internet message id: {error}")
            raise map_database_error(error)

    def read_requiring_action(
        self, owner_user_id: Optional[int] = None
    ) -> list[EmailThread]:
        """
        Return all open threads requiring action.
        Optionally filtered by owner. Used to populate the process inbox.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmailThreadsRequiringAction",
                    params={"OwnerUserId": owner_user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows]
        except Exception as error:
            logger.error(f"Error during read email threads requiring action: {error}")
            raise map_database_error(error)
