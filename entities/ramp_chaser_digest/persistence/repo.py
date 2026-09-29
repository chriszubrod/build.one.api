# Python Standard Library Imports
import base64
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.ramp_chaser_digest.business.model import RampChaserDigest
from shared.database import call_procedure, conn_ctx, get_connection, map_database_error


logger = logging.getLogger(__name__)


class RampChaserDigestRepository:
    """Persistence for per-cardholder weekly Ramp chaser digest state (U-549 §5.2)."""

    def _from_db(self, row: Optional[pyodbc.Row]) -> Optional[RampChaserDigest]:
        if not row:
            return None
        row_version_bytes = getattr(row, "RowVersion", None)
        week_of_raw = getattr(row, "WeekOf", None)
        return RampChaserDigest(
            id=getattr(row, "Id", None),
            public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
            row_version=(
                base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None
            ),
            card_holder_ramp_user_id=getattr(row, "CardHolderRampUserId", None),
            week_of=str(week_of_raw) if week_of_raw is not None else None,
            draft_message_id=getattr(row, "DraftMessageId", None),
            conversation_id=getattr(row, "ConversationId", None),
            internet_message_id=getattr(row, "InternetMessageId", None),
            last_drafted_at=getattr(row, "LastDraftedAt", None),
            last_notified_at=getattr(row, "LastNotifiedAt", None),
            notify_count=getattr(row, "NotifyCount", None),
            outcome=getattr(row, "Outcome", None),
            recipient_hash=getattr(row, "RecipientHash", None),
            created_at=getattr(row, "CreatedAt", None),
            updated_at=getattr(row, "UpdatedAt", None),
        )

    def upsert(
        self,
        *,
        card_holder_ramp_user_id: str,
        week_of: str,
        recipient_hash: Optional[str] = None,
        conn: Optional[pyodbc.Connection] = None,
    ) -> Optional[RampChaserDigest]:
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                params: dict = {
                    "CardHolderRampUserId": card_holder_ramp_user_id,
                    "WeekOf": week_of,
                    "RecipientHash": recipient_hash,
                }
                call_procedure(
                    cursor=cursor,
                    name="UpsertRampChaserDigest",
                    params=params,
                )
                row = self._from_db(cursor.fetchone())
                if conn is not None:
                    c.commit()
                return row
        except Exception as error:
            logger.error(
                "Error upserting ramp chaser digest %s %s: %s",
                card_holder_ramp_user_id,
                week_of,
                error,
            )
            raise map_database_error(error)

    def read_latest_recipient_hash(
        self,
        *,
        card_holder_ramp_user_id: str,
        week_of: str,
    ) -> Optional[str]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadLatestRampChaserDigestRecipientHash",
                    params={
                        "CardHolderRampUserId": card_holder_ramp_user_id,
                        "WeekOf": week_of,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return getattr(row, "RecipientHash", None)
        except Exception as error:
            logger.error(
                "Error reading latest ramp chaser digest recipient hash %s %s: %s",
                card_holder_ramp_user_id,
                week_of,
                error,
            )
            raise map_database_error(error)

    def read_by_card_holder_and_week(
        self,
        *,
        card_holder_ramp_user_id: str,
        week_of: str,
    ) -> Optional[RampChaserDigest]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRampChaserDigestByCardHolderAndWeek",
                    params={
                        "CardHolderRampUserId": card_holder_ramp_user_id,
                        "WeekOf": week_of,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(
                "Error reading ramp chaser digest %s %s: %s",
                card_holder_ramp_user_id,
                week_of,
                error,
            )
            raise map_database_error(error)

    def read_uncaptured(
        self, *, conn: Optional[pyodbc.Connection] = None
    ) -> List[RampChaserDigest]:
        """Digest rows with no Graph draft id yet (enqueue may have completed)."""
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUncapturedRampChaserDigests",
                    params={},
                )
                rows = [self._from_db(row) for row in cursor.fetchall()]
                if conn is not None:
                    c.commit()
                return [r for r in rows if r is not None]
        except Exception as error:
            logger.error("Error reading uncaptured ramp chaser digests: %s", error)
            raise map_database_error(error)

    def read_outstanding(
        self, *, conn: Optional[pyodbc.Connection] = None
    ) -> List[RampChaserDigest]:
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOutstandingRampChaserDigests",
                    params={},
                )
                rows = [self._from_db(row) for row in cursor.fetchall()]
                if conn is not None:
                    c.commit()
                return [r for r in rows if r is not None]
        except Exception as error:
            logger.error("Error reading outstanding ramp chaser digests: %s", error)
            raise map_database_error(error)

    def stamp_drafted(
        self,
        *,
        card_holder_ramp_user_id: str,
        week_of: str,
        draft_message_id: str,
        conversation_id: Optional[str] = None,
        internet_message_id: Optional[str] = None,
        conn: Optional[pyodbc.Connection] = None,
    ) -> Optional[RampChaserDigest]:
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="StampRampChaserDigestDrafted",
                    params={
                        "CardHolderRampUserId": card_holder_ramp_user_id,
                        "WeekOf": week_of,
                        "DraftMessageId": draft_message_id,
                        "ConversationId": conversation_id,
                        "InternetMessageId": internet_message_id,
                    },
                )
                row = self._from_db(cursor.fetchone())
                if conn is not None:
                    c.commit()
                return row
        except Exception as error:
            logger.error(
                "Error stamping ramp chaser digest drafted %s %s: %s",
                card_holder_ramp_user_id,
                week_of,
                error,
            )
            raise map_database_error(error)

    def stamp_outcome(
        self,
        *,
        card_holder_ramp_user_id: str,
        week_of: str,
        outcome: str,
        conn: Optional[pyodbc.Connection] = None,
    ) -> Optional[RampChaserDigest]:
        """Set Outcome without touching LastNotifiedAt / NotifyCount (§6.1)."""
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="StampRampChaserDigestOutcome",
                    params={
                        "CardHolderRampUserId": card_holder_ramp_user_id,
                        "WeekOf": week_of,
                        "Outcome": outcome,
                    },
                )
                row = self._from_db(cursor.fetchone())
                if conn is not None:
                    c.commit()
                return row
        except Exception as error:
            logger.error(
                "Error stamping ramp chaser digest outcome %s %s: %s",
                card_holder_ramp_user_id,
                week_of,
                error,
            )
            raise map_database_error(error)

    def stamp_notified(
        self,
        *,
        card_holder_ramp_user_id: str,
        week_of: str,
        outcome: str,
        conn: Optional[pyodbc.Connection] = None,
    ) -> Optional[RampChaserDigest]:
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="StampRampChaserDigestNotified",
                    params={
                        "CardHolderRampUserId": card_holder_ramp_user_id,
                        "WeekOf": week_of,
                        "Outcome": outcome,
                    },
                )
                row = self._from_db(cursor.fetchone())
                if conn is not None:
                    c.commit()
                return row
        except Exception as error:
            logger.error(
                "Error stamping ramp chaser digest notified %s %s: %s",
                card_holder_ramp_user_id,
                week_of,
                error,
            )
            raise map_database_error(error)
