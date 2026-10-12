# Python Standard Library Imports
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports

# Local Imports
from entities.coding_suggestion.business.model import CodingSuggestion
from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

logger = logging.getLogger(__name__)


class CodingSuggestionService:
    """Business layer for expense coding queue instrumentation rows."""

    def __init__(self):
        self.repo = CodingSuggestionRepository()

    def upsert_from_queue(
        self,
        *,
        qbo_purchase_id: int,
        qbo_purchase_qbo_id: str,
        realm_id: str,
        vendor_qbo_id: Optional[str] = None,
        expense_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Optional[CodingSuggestion]:
        vendor_id = self._resolve_vendor_id(vendor_qbo_id, realm_id=realm_id)
        if expense_id is None:
            expense_id = self._resolve_expense_id_for_purchase(
                qbo_purchase_qbo_id=qbo_purchase_qbo_id,
                realm_id=realm_id,
            )
        return self.repo.upsert_from_queue(
            qbo_purchase_id=qbo_purchase_id,
            qbo_purchase_qbo_id=qbo_purchase_qbo_id,
            realm_id=realm_id,
            vendor_id=vendor_id,
            expense_id=expense_id,
            created_by_user_id=created_by_user_id,
        )

    @staticmethod
    def _resolve_expense_id_for_purchase(
        *, qbo_purchase_qbo_id: str, realm_id: str
    ) -> Optional[int]:
        try:
            from entities.expense.business.service import ExpenseService

            expense = ExpenseService().read_by_qbo_identity(qbo_purchase_qbo_id, realm_id)
            return int(expense.id) if expense and expense.id is not None else None
        except Exception as error:
            logger.warning(
                "Could not resolve expense for purchase %s: %s",
                qbo_purchase_qbo_id,
                error,
            )
            return None

    def read_by_public_id(self, public_id: str) -> Optional[CodingSuggestion]:
        return self.repo.read_by_public_id(public_id)

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
        return self.repo.record_suggestion(
            public_id=public_id,
            suggested_project_id=suggested_project_id,
            suggested_sub_cost_code_id=suggested_sub_cost_code_id,
            suggested_description=suggested_description,
            suggestion_source=suggestion_source,
            suggestion_reason=suggestion_reason,
            suggestion_confidence=suggestion_confidence,
            sync_token_at_suggest=sync_token_at_suggest,
            status=status,
        )

    def record_flag(
        self,
        public_id: str,
        reason: str,
        modified_by_user_id: Optional[int] = None,
        only_from_pending_like: bool = False,
    ) -> Optional[CodingSuggestion]:
        return self.repo.record_flag(
            public_id=public_id,
            reason=reason,
            modified_by_user_id=modified_by_user_id,
            only_from_pending_like=only_from_pending_like,
        )

    def claim(
        self,
        public_id: str,
        user_id: int,
        reclaim_after_seconds: int = 900,
    ) -> Optional[CodingSuggestion]:
        return self.repo.claim(
            public_id=public_id,
            user_id=user_id,
            reclaim_after_seconds=reclaim_after_seconds,
        )

    def release(self, public_id: str, user_id: int) -> Optional[CodingSuggestion]:
        return self.repo.release(public_id=public_id, user_id=user_id)

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
        return self.repo.record_confirmation(
            public_id=public_id,
            confirmed_project_id=confirmed_project_id,
            confirmed_sub_cost_code_id=confirmed_sub_cost_code_id,
            confirmed_description=confirmed_description,
            was_overridden=was_overridden,
            confirmed_by_user_id=confirmed_by_user_id,
            expected_sync_token=expected_sync_token,
            status=status,
            confirmed_mixed_codes=confirmed_mixed_codes,
            prefill_state=prefill_state,
            confirmation_source=confirmation_source,
        )

    def mark_enqueued(self, public_id: str) -> Optional[CodingSuggestion]:
        return self.repo.mark_enqueued(public_id)

    def mark_written(
        self,
        public_id: str,
        sync_token: Optional[str] = None,
    ) -> Optional[CodingSuggestion]:
        return self.repo.mark_written(public_id, sync_token=sync_token)

    def mark_changed_in_qbo(
        self, public_id: str, expected_row_version: Optional[bytes] = None
    ) -> Optional[CodingSuggestion]:
        return self.repo.mark_changed_in_qbo(public_id, expected_row_version=expected_row_version)

    _DEFAULT_EXTERNALLY_RESOLVED_REASON = (
        "recoded outside the cockpit; staging line replaced"
    )

    def reconcile_externally_resolved(self, dry_run: bool = True) -> dict:
        candidates = self.repo.read_externally_resolved_candidates()
        if dry_run:
            return {"candidates": len(candidates), "items": candidates}

        marked = 0
        skipped = 0
        for item in candidates:
            result = self.repo.mark_resolved_externally(
                item["public_id"],
                self._DEFAULT_EXTERNALLY_RESOLVED_REASON,
            )
            if result is not None:
                marked += 1
            else:
                skipped += 1
        return {"marked": marked, "skipped": skipped}

    def mark_error(
        self,
        public_id: str,
        write_error: Optional[str] = None,
    ) -> Optional[CodingSuggestion]:
        return self.repo.mark_error(public_id, write_error=write_error)

    def confirm(
        self,
        *,
        public_id: str,
        project_id: int,
        sub_cost_code_id: int,
        description: Optional[str],
        was_overridden: bool,
        user_id: int,
        confirmed_mixed_codes: Optional[bool] = None,
        prefill_state: Optional[str] = None,
        allow_multi_line: bool = False,
    ) -> dict:
        """Confirm a suggestion and enqueue its QBO recode.

        ``allow_multi_line`` is set ONLY by the review-submit path, where every
        uncategorized line carries its own ExpenseLineItem coding and the worker writes
        each line with it. The confirm endpoint leaves it False: one document-level code
        cannot express per-line coding, so a multi-line purchase is refused (U-652).
        """
        from integrations.intuit.qbo.base.client import recode_write_gate_reason

        # U-058: gate-off must reject with zero DB writes — a silently recorded
        # 'confirmed' looked successful to the operator but never reached QBO.
        gate_reason = recode_write_gate_reason()
        if gate_reason is not None:
            return {"status": "writes_disabled", "reason": gate_reason}

        item = self.read_by_public_id(public_id)
        if item is None:
            return {"status": "not_found"}

        if not project_id or not sub_cost_code_id:
            return {
                "status": "invalid",
                "reason": "project and sub_cost_code required",
            }

        from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref

        qbo_item_ref = resolve_qbo_item_ref(sub_cost_code_id, realm_id=item.realm_id)
        if qbo_item_ref is None:
            return {
                "status": "mapping_missing",
                "reason": "SubCostCode has no QBO Item mapping",
            }

        uncategorized_count = self._count_uncategorized_purchase_lines(
            realm_id=item.realm_id or "",
            qbo_purchase_qbo_id=item.qbo_purchase_qbo_id or "",
        )
        if uncategorized_count > 1 and not allow_multi_line:
            return {
                "status": "multi_line_unsupported",
                "reason": "multi_line_purchase_confirm_unsupported",
            }

        expected_sync_token = None
        try:
            from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

            qbo_purchase = QboPurchaseRepository().read_by_id(item.qbo_purchase_id)
            if qbo_purchase and qbo_purchase.sync_token:
                expected_sync_token = qbo_purchase.sync_token
        except Exception as error:
            logger.warning(
                "Could not snapshot sync token for coding suggestion %s: %s",
                public_id,
                error,
            )

        self.record_confirmation(
            public_id=public_id,
            confirmed_project_id=project_id,
            confirmed_sub_cost_code_id=sub_cost_code_id,
            confirmed_description=description,
            was_overridden=was_overridden,
            confirmed_by_user_id=user_id,
            expected_sync_token=expected_sync_token,
            status="confirmed",
            confirmed_mixed_codes=confirmed_mixed_codes,
            prefill_state=prefill_state,
            # The worker writes per-line coding only for a review-submit confirmation.
            confirmation_source="review_submit" if allow_multi_line else "confirm_endpoint",
        )

        from integrations.intuit.qbo.outbox.business.service import QboOutboxService

        QboOutboxService().enqueue(
            kind="recode_purchase_line",
            entity_type="CodingSuggestion",
            entity_public_id=public_id,
            realm_id=item.realm_id,
        )
        self.mark_enqueued(public_id)
        return {"status": "enqueued", "enqueued": True}

    def is_multi_line_purchase(self, item: CodingSuggestion) -> bool:
        """True when the suggestion's QBO purchase has 2+ uncategorized lines (read-only)."""
        return self._count_uncategorized_purchase_lines(
            realm_id=item.realm_id or "",
            qbo_purchase_qbo_id=item.qbo_purchase_qbo_id or "",
        ) > 1

    @staticmethod
    def _count_uncategorized_purchase_lines(
        *, realm_id: str, qbo_purchase_qbo_id: str
    ) -> int:
        if not realm_id or not qbo_purchase_qbo_id:
            return 0
        from shared.database import get_connection

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM [qbo].[PurchaseLine] pl
                INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
                WHERE p.[QboId] = ?
                  AND p.[RealmId] = ?
                  AND pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
                """,
                qbo_purchase_qbo_id,
                realm_id,
            )
            row = cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0

    def legacy_single_line_ids(
        self, item: CodingSuggestion
    ) -> tuple[Optional[int], Optional[str]]:
        """Legacy response keys when exactly one uncategorized staging line exists."""
        if not item.realm_id or not item.qbo_purchase_qbo_id:
            return None, None
        from shared.database import get_connection

        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT pl.[Id], pl.[QboLineId]
                FROM [qbo].[PurchaseLine] pl
                INNER JOIN [qbo].[Purchase] p ON pl.[QboPurchaseId] = p.[Id]
                WHERE p.[QboId] = ?
                  AND p.[RealmId] = ?
                  AND pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
                ORDER BY pl.[QboLineId]
                """,
                item.qbo_purchase_qbo_id,
                item.realm_id,
            )
            rows = cursor.fetchall()
        if len(rows) != 1:
            return None, None
        line_pk = getattr(rows[0], "Id", rows[0][0])
        line_qbo_id = getattr(rows[0], "QboLineId", rows[0][1])
        return (
            int(line_pk) if line_pk is not None else None,
            str(line_qbo_id) if line_qbo_id is not None else None,
        )

    def to_api_dict(self, item: CodingSuggestion) -> dict:
        payload = item.to_dict()
        line_pk, line_qbo_id = self.legacy_single_line_ids(item)
        payload["qbo_purchase_line_id"] = line_pk
        payload["qbo_line_id"] = line_qbo_id
        return payload

    # One of FIVE near-identical dbo-first/legacy-fallback vendor-ref resolvers
    # (U-284v): this one, BillBillConnector._get_vendor_public_id (pull) +
    # _get_qbo_vendor_ref (push), PurchaseExpenseConnector._get_vendor_public_id,
    # VendorCreditBillCreditConnector._get_vendor_public_id. Hand-copied
    # deliberately, mirroring _get_project_public_id's own precedent — see
    # TODO.md's U-005[reuse] entry before adding a 6th copy or consolidating.
    def _resolve_vendor_id(
        self,
        vendor_qbo_id: Optional[str],
        *,
        realm_id: Optional[str] = None,
    ) -> Optional[int]:
        if not vendor_qbo_id:
            return None
        try:
            from entities.vendor.business.service import VendorService
            from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only

            vendor_service = VendorService()

            # U-313: dbo.Vendor's native QboId/RealmId is the SOLE identity
            # store for Vendor (Wave 5 "trust dbo alone" — qbo.VendorVendor no
            # longer has a writer, see docs/design/wave5.md). No legacy hop
            # left to fall back to on a miss (removed; it had no data source
            # left either).
            direct_vendor = vendor_service.read_by_qbo_identity(vendor_qbo_id, realm_id)
            if direct_vendor:
                verified_qbo_id = verify_identity_dbo_only(
                    direct_vendor, read_direct_by_qbo_identity=vendor_service.read_by_qbo_identity,
                )
                if verified_qbo_id:
                    return direct_vendor.id

            return None
        except Exception as error:
            logger.warning(f"Could not resolve vendor for QBO id {vendor_qbo_id}: {error}")
            return None
