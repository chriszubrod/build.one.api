# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.expense_coding_item.business.model import ExpenseCodingItem
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class ExpenseCodingItemRepository:
    """Repository for ExpenseCodingItem persistence."""

    def _from_db(self, row: Optional[pyodbc.Row]) -> Optional[ExpenseCodingItem]:
        if not row:
            return None
        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            suggestion_confidence = getattr(row, "SuggestionConfidence", None)
            return ExpenseCodingItem(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                qbo_purchase_id=getattr(row, "QboPurchaseId", None),
                qbo_purchase_line_id=getattr(row, "QboPurchaseLineId", None),
                qbo_line_id=getattr(row, "QboLineId", None),
                qbo_purchase_qbo_id=getattr(row, "QboPurchaseQboId", None),
                realm_id=getattr(row, "RealmId", None),
                vendor_id=getattr(row, "VendorId", None),
                sync_token_at_suggest=getattr(row, "SyncTokenAtSuggest", None),
                status=getattr(row, "Status", None),
                suggested_project_id=getattr(row, "SuggestedProjectId", None),
                suggested_sub_cost_code_id=getattr(row, "SuggestedSubCostCodeId", None),
                suggested_description=getattr(row, "SuggestedDescription", None),
                suggestion_source=getattr(row, "SuggestionSource", None),
                suggestion_reason=getattr(row, "SuggestionReason", None),
                suggestion_confidence=Decimal(str(suggestion_confidence)) if suggestion_confidence is not None else None,
                suggested_at=getattr(row, "SuggestedAt", None),
                confirmed_project_id=getattr(row, "ConfirmedProjectId", None),
                confirmed_sub_cost_code_id=getattr(row, "ConfirmedSubCostCodeId", None),
                confirmed_description=getattr(row, "ConfirmedDescription", None),
                was_overridden=getattr(row, "WasOverridden", None),
                confirmed_by_user_id=getattr(row, "ConfirmedByUserId", None),
                confirmed_at=getattr(row, "ConfirmedAt", None),
                flag_reason=getattr(row, "FlagReason", None),
                flagged_at=getattr(row, "FlaggedAt", None),
                written_at=getattr(row, "WrittenAt", None),
                write_error=getattr(row, "WriteError", None),
                claimed_by_user_id=getattr(row, "ClaimedByUserId", None),
                claimed_at=getattr(row, "ClaimedAt", None),
                company_id=getattr(row, "CompanyId", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during expense coding item mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during expense coding item mapping: {error}")
            raise map_database_error(error)

    def upsert_from_queue(
        self,
        *,
        qbo_purchase_id: int,
        qbo_purchase_line_id: int,
        qbo_line_id: Optional[str] = None,
        qbo_purchase_qbo_id: Optional[str] = None,
        realm_id: Optional[str] = None,
        vendor_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertExpenseCodingItem",
                    params={
                        "QboPurchaseId": qbo_purchase_id,
                        "QboPurchaseLineId": qbo_purchase_line_id,
                        "QboLineId": qbo_line_id,
                        "QboPurchaseQboId": qbo_purchase_qbo_id,
                        "RealmId": realm_id,
                        "VendorId": vendor_id,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error upserting expense coding item for line {qbo_purchase_line_id}: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseCodingItemByPublicId",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading expense coding item {public_id}: {error}")
            raise map_database_error(error)

    def record_suggestion(
        self,
        *,
        public_id: str,
        suggested_project_id: Optional[int] = None,
        suggested_sub_cost_code_id: Optional[int] = None,
        suggested_description: Optional[str] = None,
        suggestion_source: Optional[str] = None,
        suggestion_reason: Optional[str] = None,
        suggestion_confidence: Optional[Decimal] = None,
        sync_token_at_suggest: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RecordExpenseCodingSuggestion",
                    params={
                        "PublicId": public_id,
                        "SuggestedProjectId": suggested_project_id,
                        "SuggestedSubCostCodeId": suggested_sub_cost_code_id,
                        "SuggestedDescription": suggested_description,
                        "SuggestionSource": suggestion_source,
                        "SuggestionReason": suggestion_reason,
                        "SuggestionConfidence": (
                            Decimal(str(suggestion_confidence)) if suggestion_confidence is not None else None
                        ),
                        "SyncTokenAtSuggest": sync_token_at_suggest,
                        "Status": status,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error recording suggestion for expense coding item {public_id}: {error}")
            raise map_database_error(error)

    def record_flag(
        self,
        public_id: str,
        reason: str,
        modified_by_user_id: Optional[int] = None,
        only_from_pending_like: bool = False,
    ) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RecordExpenseCodingFlag",
                    params={
                        "PublicId": public_id,
                        "FlagReason": reason,
                        "ModifiedByUserId": modified_by_user_id,
                        "OnlyFromPendingLike": 1 if only_from_pending_like else 0,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error flagging expense coding item {public_id}: {error}")
            raise map_database_error(error)

    def claim(
        self,
        public_id: str,
        user_id: int,
        reclaim_after_seconds: int = 900,
    ) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ClaimExpenseCodingItem",
                    params={
                        "PublicId": public_id,
                        "UserId": user_id,
                        "ReclaimAfterSeconds": reclaim_after_seconds,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error claiming expense coding item {public_id}: {error}")
            raise map_database_error(error)

    def release(self, public_id: str, user_id: int) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReleaseExpenseCodingItem",
                    params={
                        "PublicId": public_id,
                        "UserId": user_id,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error releasing expense coding item {public_id}: {error}")
            raise map_database_error(error)

    def record_confirmation(
        self,
        *,
        public_id: str,
        confirmed_project_id: Optional[int] = None,
        confirmed_sub_cost_code_id: Optional[int] = None,
        confirmed_description: Optional[str] = None,
        was_overridden: Optional[bool] = None,
        confirmed_by_user_id: Optional[int] = None,
        expected_sync_token: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RecordExpenseCodingConfirmation",
                    params={
                        "PublicId": public_id,
                        "ConfirmedProjectId": confirmed_project_id,
                        "ConfirmedSubCostCodeId": confirmed_sub_cost_code_id,
                        "ConfirmedDescription": confirmed_description,
                        "WasOverridden": was_overridden,
                        "ConfirmedByUserId": confirmed_by_user_id,
                        "ExpectedSyncToken": expected_sync_token,
                        "Status": status,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error recording confirmation for expense coding item {public_id}: {error}")
            raise map_database_error(error)

    def mark_enqueued(self, public_id: str) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkExpenseCodingEnqueued",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error marking expense coding item {public_id} enqueued: {error}")
            raise map_database_error(error)

    def mark_written(
        self,
        public_id: str,
        sync_token: Optional[str] = None,
    ) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkExpenseCodingWritten",
                    params={
                        "PublicId": public_id,
                        "SyncToken": sync_token,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error marking expense coding item {public_id} written: {error}")
            raise map_database_error(error)

    def mark_changed_in_qbo(self, public_id: str) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkExpenseCodingChangedInQbo",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error marking expense coding item {public_id} changed_in_qbo: {error}")
            raise map_database_error(error)

    def read_vendor_dominant_sub_cost_code(self, vendor_id: int) -> Optional[dict]:
        """Return the modal SubCostCode from committed vendor expense history."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorDominantSubCostCode",
                    params={"VendorId": vendor_id},
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {
                    "sub_cost_code_id": row.SubCostCodeId,
                    "number": row.Number,
                    "name": row.Name,
                    "top_count": row.TopCount,
                    "total_count": row.TotalCount,
                }
        except Exception as error:
            logger.error(
                f"Error reading dominant sub cost code for vendor {vendor_id}: {error}"
            )
            raise map_database_error(error)

    def mark_error(
        self,
        public_id: str,
        write_error: Optional[str] = None,
    ) -> Optional[ExpenseCodingItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkExpenseCodingError",
                    params={
                        "PublicId": public_id,
                        "WriteError": write_error,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error marking expense coding item {public_id} error: {error}")
            raise map_database_error(error)
