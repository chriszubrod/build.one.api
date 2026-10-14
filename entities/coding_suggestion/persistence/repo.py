# Python Standard Library Imports
import base64
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.coding_suggestion.business.model import (
    CodingSuggestion,
    VendorDominantProjectForBills,
    VendorDominantSubCostCodeForBills,
)
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)

def _normalize_as_of_datetime(as_of):
    """History cutoffs: aware datetimes → UTC naive; naive datetimes → unchanged (UTC)."""
    if as_of is None:
        return None
    if isinstance(as_of, datetime) and as_of.tzinfo is not None:
        return as_of.astimezone(timezone.utc).replace(tzinfo=None)
    return as_of


class CodingSuggestionRepository:
    """Repository for CodingSuggestion persistence."""

    def _from_db(self, row: Optional[pyodbc.Row]) -> Optional[CodingSuggestion]:
        if not row:
            return None
        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            suggestion_confidence = getattr(row, "SuggestionConfidence", None)
            budget_remaining = getattr(row, "BudgetRemaining", None)
            return CodingSuggestion(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                target_kind=getattr(row, "TargetKind", None),
                bill_id=getattr(row, "BillId", None),
                bill_credit_id=getattr(row, "BillCreditId", None),
                expense_id=getattr(row, "ExpenseId", None),
                attachment_id=getattr(row, "AttachmentId", None),
                qbo_purchase_id=getattr(row, "QboPurchaseId", None),
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
                features_json=getattr(row, "FeaturesJson", None),
                budget_check=getattr(row, "BudgetCheck", None),
                budget_remaining=Decimal(str(budget_remaining)) if budget_remaining is not None else None,
                route=getattr(row, "Route", None),
                model_version=getattr(row, "ModelVersion", None),
                confirmed_project_id=getattr(row, "ConfirmedProjectId", None),
                confirmed_sub_cost_code_id=getattr(row, "ConfirmedSubCostCodeId", None),
                confirmed_description=getattr(row, "ConfirmedDescription", None),
                was_overridden=getattr(row, "WasOverridden", None),
                confirmed_by_user_id=getattr(row, "ConfirmedByUserId", None),
                confirmed_at=getattr(row, "ConfirmedAt", None),
                confirmed_mixed_codes=getattr(row, "ConfirmedMixedCodes", None),
                prefill_state=getattr(row, "PrefillState", None),
                confirmation_source=getattr(row, "ConfirmationSource", None),
                confirmation_seq=getattr(row, "ConfirmationSeq", None),
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
            logger.error("Attribute error during coding suggestion mapping: %s", type(error).__name__)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during coding suggestion mapping: %s", type(error).__name__)
            raise map_database_error(error)

    def upsert_from_queue(
        self,
        *,
        qbo_purchase_id: int,
        qbo_purchase_qbo_id: str,
        realm_id: str,
        vendor_id: Optional[int] = None,
        expense_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
        qbo_purchase_line_id: Optional[int] = None,
        qbo_line_id: Optional[str] = None,
    ) -> Optional[CodingSuggestion]:
        del qbo_purchase_line_id, qbo_line_id
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertCodingSuggestionForPurchase",
                    params={
                        "QboPurchaseId": qbo_purchase_id,
                        "QboPurchaseQboId": qbo_purchase_qbo_id,
                        "RealmId": realm_id,
                        "VendorId": vendor_id,
                        "ExpenseId": expense_id,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(
                "Error upserting coding suggestion for purchase %s: %s",
                qbo_purchase_qbo_id,
                error,
            )
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCodingSuggestionByPublicId",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading coding suggestion {public_id}: {error}")
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
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RecordCodingSuggestion",
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
            logger.error(f"Error recording suggestion for coding suggestion {public_id}: {error}")
            raise map_database_error(error)

    def record_flag(
        self,
        public_id: str,
        reason: str,
        modified_by_user_id: Optional[int] = None,
        only_from_pending_like: bool = False,
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RecordCodingSuggestionFlag",
                    params={
                        "PublicId": public_id,
                        "FlagReason": reason,
                        "ModifiedByUserId": modified_by_user_id,
                        "OnlyFromPendingLike": 1 if only_from_pending_like else 0,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error flagging coding suggestion {public_id}: {error}")
            raise map_database_error(error)

    def claim(
        self,
        public_id: str,
        user_id: int,
        reclaim_after_seconds: int = 900,
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ClaimCodingSuggestion",
                    params={
                        "PublicId": public_id,
                        "UserId": user_id,
                        "ReclaimAfterSeconds": reclaim_after_seconds,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error claiming coding suggestion {public_id}: {error}")
            raise map_database_error(error)

    def release(self, public_id: str, user_id: int) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReleaseCodingSuggestion",
                    params={
                        "PublicId": public_id,
                        "UserId": user_id,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error releasing coding suggestion {public_id}: {error}")
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
        confirmed_mixed_codes: Optional[bool] = None,
        prefill_state: Optional[str] = None,
        confirmation_source: Optional[str] = None,
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RecordCodingSuggestionConfirmation",
                    params={
                        "PublicId": public_id,
                        "ConfirmedProjectId": confirmed_project_id,
                        "ConfirmedSubCostCodeId": confirmed_sub_cost_code_id,
                        "ConfirmedDescription": confirmed_description,
                        "WasOverridden": was_overridden,
                        "ConfirmedByUserId": confirmed_by_user_id,
                        "ExpectedSyncToken": expected_sync_token,
                        "Status": status,
                        "ConfirmedMixedCodes": confirmed_mixed_codes,
                        "PrefillState": prefill_state,
                        "ConfirmationSource": confirmation_source,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error recording confirmation for coding suggestion {public_id}: {error}")
            raise map_database_error(error)

    def mark_enqueued(self, public_id: str) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkCodingSuggestionEnqueued",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error marking coding suggestion {public_id} enqueued: {error}")
            raise map_database_error(error)

    def mark_written(
        self,
        public_id: str,
        sync_token: Optional[str] = None,
        expected_confirmation_seq: Optional[int] = None,
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkCodingSuggestionWritten",
                    params={
                        "PublicId": public_id,
                        "SyncToken": sync_token,
                        "ExpectedConfirmationSeq": expected_confirmation_seq,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error marking coding suggestion {public_id} written: {error}")
            raise map_database_error(error)

    def read_externally_resolved_candidates(self) -> list[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExternallyResolvedCodingSuggestionCandidates",
                    params={},
                )
                return [
                    {
                        "public_id": str(row.PublicId) if getattr(row, "PublicId", None) else None,
                        "id": getattr(row, "Id", None),
                        "status": getattr(row, "Status", None),
                        "qbo_purchase_qbo_id": getattr(row, "QboPurchaseQboId", None),
                        "qbo_line_id": getattr(row, "QboLineId", None),
                    }
                    for row in cursor.fetchall()
                ]
        except Exception as error:
            logger.error("Error reading externally resolved coding item candidates: %s", error)
            raise map_database_error(error)

    def mark_resolved_externally(
        self,
        public_id: str,
        reason: Optional[str] = None,
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkCodingSuggestionResolvedExternally",
                    params={
                        "PublicId": public_id,
                        "WriteError": reason,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(
                "Error marking coding suggestion %s resolved_externally: %s",
                public_id,
                error,
            )
            raise map_database_error(error)

    def mark_changed_in_qbo(
        self,
        public_id: str,
        expected_confirmation_seq: Optional[int] = None,
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkCodingSuggestionChangedInQbo",
                    params={
                        "PublicId": public_id,
                        "ExpectedConfirmationSeq": expected_confirmation_seq,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error marking coding suggestion {public_id} changed_in_qbo: {error}")
            raise map_database_error(error)

    def upsert_for_bill(
        self,
        *,
        bill_id: int,
        vendor_id: Optional[int] = None,
        suggested_project_id: Optional[int] = None,
        suggested_sub_cost_code_id: Optional[int] = None,
        suggestion_source: Optional[str] = None,
        suggestion_reason: Optional[str] = None,
        suggestion_confidence: Optional[Decimal] = None,
        features_json: Optional[str] = None,
        model_version: Optional[str] = None,
        prefill_state: Optional[str] = None,
        budget_check: Optional[str] = None,
        budget_remaining: Optional[Decimal] = None,
        created_by_user_id: Optional[int] = None,
        replace: bool = False,
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params: dict = {
                    "BillId": bill_id,
                    "VendorId": vendor_id,
                    "SuggestedProjectId": suggested_project_id,
                    "SuggestedSubCostCodeId": suggested_sub_cost_code_id,
                    "SuggestionSource": suggestion_source,
                    "SuggestionReason": suggestion_reason,
                    "SuggestionConfidence": (
                        Decimal(str(suggestion_confidence))
                        if suggestion_confidence is not None
                        else None
                    ),
                    "FeaturesJson": features_json,
                    "ModelVersion": model_version,
                    "PrefillState": prefill_state,
                    "CreatedByUserId": created_by_user_id,
                    "ReplaceSuggestion": 1 if replace else 0,
                }
                if budget_check is not None:
                    params["BudgetCheck"] = budget_check
                if budget_remaining is not None:
                    params["BudgetRemaining"] = Decimal(str(budget_remaining))
                # SQL first: the proc must carry @BudgetCheck/@BudgetRemaining before
                # this code deploys (a proc that lacks them fails loudly here).
                call_procedure(
                    cursor=cursor,
                    name="UpsertCodingSuggestionForBill",
                    params=params,
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(
                "Error upserting coding suggestion for bill %s: %s",
                bill_id,
                type(error).__name__,
            )
            raise map_database_error(error)

    def read_by_bill_id(self, bill_id: int) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCodingSuggestionByBillId",
                    params={"BillId": bill_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(
                "Error reading coding suggestion for bill %s: %s",
                bill_id,
                type(error).__name__,
            )
            raise map_database_error(error)

    def read_vendor_dominant_sub_cost_code_for_bills(
        self,
        vendor_id: int,
        *,
        project_id: Optional[int] = None,
        as_of=None,
        exclude_bill_id: Optional[int] = None,
    ) -> Optional[VendorDominantSubCostCodeForBills]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorDominantSubCostCodeForBills",
                    params={
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "AsOfDatetime": _normalize_as_of_datetime(as_of),
                        "ExcludeBillId": exclude_bill_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    return None
                scc_public = getattr(row, "SubCostCodePublicId", None)
                return VendorDominantSubCostCodeForBills(
                    top_sub_cost_code_id=int(row.TopSubCostCodeId),
                    top_count=int(row.TopCount),
                    second_count=int(row.SecondCount),
                    total_count=int(row.TotalCount),
                    public_id=str(scc_public) if scc_public is not None else None,
                    number=getattr(row, "Number", None),
                    name=getattr(row, "Name", None),
                )
        except Exception as error:
            logger.error(
                "Error reading bill dominant sub cost code for vendor %s: %s",
                vendor_id,
                type(error).__name__,
            )
            raise map_database_error(error)

    def read_vendor_dominant_project_for_bills(
        self,
        vendor_id: int,
        *,
        as_of=None,
        exclude_bill_id: Optional[int] = None,
    ) -> Optional[VendorDominantProjectForBills]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorDominantProjectForBills",
                    params={
                        "VendorId": vendor_id,
                        "AsOfDatetime": _normalize_as_of_datetime(as_of),
                        "ExcludeBillId": exclude_bill_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return VendorDominantProjectForBills(
                    top_project_id=int(row.TopProjectId),
                    top_count=int(row.TopCount),
                    second_count=int(row.SecondCount),
                    total_count=int(row.TotalCount),
                )
        except Exception as error:
            logger.error(
                "Error reading bill dominant project for vendor %s: %s",
                vendor_id,
                type(error).__name__,
            )
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
        expected_confirmation_seq: Optional[int] = None,
    ) -> Optional[CodingSuggestion]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="MarkCodingSuggestionError",
                    params={
                        "PublicId": public_id,
                        "WriteError": write_error,
                        "ExpectedConfirmationSeq": expected_confirmation_seq,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error marking coding suggestion {public_id} error: {error}")
            raise map_database_error(error)

    def read_state_by_expense_ids(self, expense_ids: list[int]) -> dict[int, list[dict]]:
        """Batch lookup of coding items keyed by ExpenseId (U-480).

        One round trip for a whole page. Without it the Expense list would
        resolve coding state per row — the N+1 Bill's review stitch avoided.
        """
        if not expense_ids:
            return {}
        from shared.authz import current_is_system_admin, current_user_id

        csv = ",".join(str(int(x)) for x in expense_ids if x is not None)
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCodingSuggestionStateByExpenseIds",
                    params={
                        "ExpenseIds": csv,
                        "ActorUserId": current_user_id.get(),
                        "ActorIsSystemAdmin": 1 if current_is_system_admin.get() else 0,
                    },
                )
                out: dict[int, list[dict]] = {}
                for row in cursor.fetchall():
                    expense_id = getattr(row, "ExpenseId", None)
                    if expense_id is None:
                        continue
                    suggestion_confidence = getattr(row, "SuggestionConfidence", None)
                    out.setdefault(int(expense_id), []).append(
                        {
                            "public_id": (
                                str(row.CodingItemPublicId)
                                if getattr(row, "CodingItemPublicId", None)
                                else None
                            ),
                            "status": getattr(row, "Status", None),
                            "confidence": (
                                Decimal(str(suggestion_confidence))
                                if suggestion_confidence is not None
                                else None
                            ),
                            "suggested_project_id": getattr(row, "SuggestedProjectId", None),
                            "suggested_sub_cost_code_id": getattr(
                                row, "SuggestedSubCostCodeId", None
                            ),
                            "flag_reason": getattr(row, "FlagReason", None),
                        }
                    )
                return out
        except Exception as error:
            logger.error("Error during read expense coding state by expense ids: %s", error)
            raise map_database_error(error)
