# Python Standard Library Imports
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports

# Local Imports
from entities.expense_coding_item.business.model import ExpenseCodingItem
from entities.expense_coding_item.persistence.repo import ExpenseCodingItemRepository

logger = logging.getLogger(__name__)


class ExpenseCodingItemService:
    """Business layer for expense coding queue instrumentation rows."""

    def __init__(self):
        self.repo = ExpenseCodingItemRepository()

    def upsert_from_queue(
        self,
        *,
        qbo_purchase_id: int,
        qbo_purchase_line_id: int,
        qbo_line_id: Optional[str] = None,
        qbo_purchase_qbo_id: Optional[str] = None,
        realm_id: Optional[str] = None,
        vendor_qbo_id: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Optional[ExpenseCodingItem]:
        vendor_id = self._resolve_vendor_id(vendor_qbo_id, realm_id=realm_id)
        return self.repo.upsert_from_queue(
            qbo_purchase_id=qbo_purchase_id,
            qbo_purchase_line_id=qbo_purchase_line_id,
            qbo_line_id=qbo_line_id,
            qbo_purchase_qbo_id=qbo_purchase_qbo_id,
            realm_id=realm_id,
            vendor_id=vendor_id,
            created_by_user_id=created_by_user_id,
        )

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseCodingItem]:
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
    ) -> Optional[ExpenseCodingItem]:
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
    ) -> Optional[ExpenseCodingItem]:
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
    ) -> Optional[ExpenseCodingItem]:
        return self.repo.claim(
            public_id=public_id,
            user_id=user_id,
            reclaim_after_seconds=reclaim_after_seconds,
        )

    def release(self, public_id: str, user_id: int) -> Optional[ExpenseCodingItem]:
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
    ) -> Optional[ExpenseCodingItem]:
        return self.repo.record_confirmation(
            public_id=public_id,
            confirmed_project_id=confirmed_project_id,
            confirmed_sub_cost_code_id=confirmed_sub_cost_code_id,
            confirmed_description=confirmed_description,
            was_overridden=was_overridden,
            confirmed_by_user_id=confirmed_by_user_id,
            expected_sync_token=expected_sync_token,
            status=status,
        )

    def mark_enqueued(self, public_id: str) -> Optional[ExpenseCodingItem]:
        return self.repo.mark_enqueued(public_id)

    def mark_written(
        self,
        public_id: str,
        sync_token: Optional[str] = None,
    ) -> Optional[ExpenseCodingItem]:
        return self.repo.mark_written(public_id, sync_token=sync_token)

    def mark_changed_in_qbo(self, public_id: str) -> Optional[ExpenseCodingItem]:
        return self.repo.mark_changed_in_qbo(public_id)

    def mark_error(
        self,
        public_id: str,
        write_error: Optional[str] = None,
    ) -> Optional[ExpenseCodingItem]:
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
    ) -> dict:
        item = self.read_by_public_id(public_id)
        if item is None:
            return {"status": "not_found"}

        if not project_id or not sub_cost_code_id:
            return {
                "status": "invalid",
                "reason": "project and sub_cost_code required",
            }

        from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository

        item_mapping = ItemSubCostCodeRepository().read_by_sub_cost_code_id(sub_cost_code_id)
        if item_mapping is None:
            return {
                "status": "mapping_missing",
                "reason": "SubCostCode has no QBO Item mapping",
            }

        expected_sync_token = None
        try:
            from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

            qbo_purchase = QboPurchaseRepository().read_by_id(item.qbo_purchase_id)
            if qbo_purchase and qbo_purchase.sync_token:
                expected_sync_token = qbo_purchase.sync_token
        except Exception as error:
            logger.warning(
                "Could not snapshot sync token for expense coding item %s: %s",
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
        )

        from integrations.intuit.qbo.base.client import _writes_allowed

        if _writes_allowed():
            from integrations.intuit.qbo.outbox.business.service import QboOutboxService

            QboOutboxService().enqueue(
                kind="recode_purchase_line",
                entity_type="ExpenseCodingItem",
                entity_public_id=public_id,
                realm_id=item.realm_id,
            )
            self.mark_enqueued(public_id)
            return {"status": "enqueued", "enqueued": True}

        return {
            "status": "confirmed",
            "enqueued": False,
            "reason": "qbo_writes_disabled",
        }

    def _resolve_vendor_id(
        self,
        vendor_qbo_id: Optional[str],
        *,
        realm_id: Optional[str] = None,
    ) -> Optional[int]:
        if not vendor_qbo_id:
            return None
        try:
            from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
            from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository

            qbo_vendor_repo = QboVendorRepository()
            if realm_id is not None:
                qbo_vendor = qbo_vendor_repo.read_by_qbo_id_and_realm_id(vendor_qbo_id, realm_id)
            else:
                qbo_vendor = qbo_vendor_repo.read_by_qbo_id(vendor_qbo_id)
            if not qbo_vendor:
                return None
            mapping = VendorVendorRepository().read_by_qbo_vendor_id(qbo_vendor.id)
            if not mapping:
                return None
            return mapping.vendor_id
        except Exception as error:
            logger.warning(f"Could not resolve vendor for QBO id {vendor_qbo_id}: {error}")
            return None
