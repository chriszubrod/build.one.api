# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.ramp_transaction_follow_up.business.model import RampTransactionFollowUp
from shared.database import call_procedure, conn_ctx, get_connection, map_database_error


logger = logging.getLogger(__name__)


class RampTransactionFollowUpRepository:
    """Persistence for Ramp receipt/memo chaser state (U-549)."""

    def _from_db(self, row: Optional[pyodbc.Row]) -> Optional[RampTransactionFollowUp]:
        if not row:
            return None
        row_version_bytes = getattr(row, "RowVersion", None)
        amount_raw = getattr(row, "Amount", None)
        return RampTransactionFollowUp(
            id=getattr(row, "Id", None),
            public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
            row_version=(
                base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None
            ),
            ramp_transaction_id=getattr(row, "RampTransactionId", None),
            card_holder_ramp_user_id=getattr(row, "CardHolderRampUserId", None),
            card_holder_name=getattr(row, "CardHolderName", None),
            merchant_name=getattr(row, "MerchantName", None),
            amount=Decimal(str(amount_raw)) if amount_raw is not None else None,
            transaction_date=getattr(row, "TransactionDate", None),
            needs_memo=getattr(row, "NeedsMemo", None),
            needs_receipt=getattr(row, "NeedsReceipt", None),
            first_seen_at=getattr(row, "FirstSeenAt", None),
            last_drafted_at=getattr(row, "LastDraftedAt", None),
            draft_message_id=getattr(row, "DraftMessageId", None),
            last_notified_at=getattr(row, "LastNotifiedAt", None),
            notify_count=getattr(row, "NotifyCount", None),
            escalated_at=getattr(row, "EscalatedAt", None),
            resolved_at=getattr(row, "ResolvedAt", None),
            created_at=getattr(row, "CreatedAt", None),
            updated_at=getattr(row, "UpdatedAt", None),
        )

    def upsert_open_item(
        self,
        *,
        ramp_transaction_id: str,
        card_holder_ramp_user_id: Optional[str],
        card_holder_name: Optional[str],
        merchant_name: Optional[str],
        amount: Optional[Decimal],
        transaction_date: Optional[str],
        needs_memo: bool,
        needs_receipt: bool,
        conn: Optional[pyodbc.Connection] = None,
    ) -> Optional[RampTransactionFollowUp]:
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertRampTransactionFollowUp",
                    params={
                        "RampTransactionId": ramp_transaction_id,
                        "CardHolderRampUserId": card_holder_ramp_user_id,
                        "CardHolderName": card_holder_name,
                        "MerchantName": merchant_name,
                        "Amount": Decimal(str(amount)) if amount is not None else None,
                        "TransactionDate": transaction_date,
                        "NeedsMemo": 1 if needs_memo else 0,
                        "NeedsReceipt": 1 if needs_receipt else 0,
                    },
                )
                row = self._from_db(cursor.fetchone())
                if conn is not None:
                    c.commit()
                return row
        except Exception as error:
            logger.error(
                "Error upserting ramp transaction follow-up %s: %s",
                ramp_transaction_id,
                error,
            )
            raise map_database_error(error)

    def mark_resolved(
        self,
        *,
        ramp_transaction_id: str,
        conn: Optional[pyodbc.Connection] = None,
    ) -> Optional[RampTransactionFollowUp]:
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkRampTransactionFollowUpResolved",
                    params={"RampTransactionId": ramp_transaction_id},
                )
                row = self._from_db(cursor.fetchone())
                if conn is not None:
                    c.commit()
                return row
        except Exception as error:
            logger.error(
                "Error resolving ramp transaction follow-up %s: %s",
                ramp_transaction_id,
                error,
            )
            raise map_database_error(error)

    def read_unresolved(
        self, *, conn: Optional[pyodbc.Connection] = None
    ) -> List[RampTransactionFollowUp]:
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUnresolvedRampTransactionFollowUps",
                    params={},
                )
                rows = [self._from_db(row) for row in cursor.fetchall()]
                if conn is not None:
                    c.commit()
                return [r for r in rows if r is not None]
        except Exception as error:
            logger.error("Error reading unresolved ramp follow-ups: %s", error)
            raise map_database_error(error)

    def read_unresolved_ramp_transaction_ids(
        self, *, conn: Optional[pyodbc.Connection] = None
    ) -> List[str]:
        try:
            with conn_ctx(conn) as c:
                cursor = c.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUnresolvedRampTransactionFollowUpIds",
                    params={},
                )
                ids = [
                    str(row.RampTransactionId)
                    for row in cursor.fetchall()
                    if getattr(row, "RampTransactionId", None)
                ]
                if conn is not None:
                    c.commit()
                return ids
        except Exception as error:
            logger.error("Error reading unresolved ramp follow-up ids: %s", error)
            raise map_database_error(error)

    def read_by_ramp_transaction_id(
        self, ramp_transaction_id: str
    ) -> Optional[RampTransactionFollowUp]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRampTransactionFollowUpByRampTransactionId",
                    params={"RampTransactionId": ramp_transaction_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(
                "Error reading ramp follow-up by ramp id %s: %s",
                ramp_transaction_id,
                error,
            )
            raise map_database_error(error)
