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
    ) -> Optional[ExpenseCodingItem]:
        return self.repo.record_flag(
            public_id=public_id,
            reason=reason,
            modified_by_user_id=modified_by_user_id,
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
