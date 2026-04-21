# Python Standard Library Imports
import logging
import time
from typing import List, Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit, QboVendorCreditLine
from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
from integrations.intuit.qbo.vendorcredit.external.schemas import (
    QboVendorCredit as QboVendorCreditSchema,
    QboVendorCreditLine as QboVendorCreditLineSchema,
)
logger = logging.getLogger(__name__)


class QboVendorCreditService:
    """Service for QBO VendorCredit sync operations."""

    def __init__(self, repo: Optional[QboVendorCreditRepository] = None):
        self.repo = repo or QboVendorCreditRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sync_to_modules: bool = True,
    ) -> List[QboVendorCredit]:
        """
        Sync VendorCredits from QBO to local cache.
        
        Args:
            realm_id: QBO realm/company ID
            last_updated_time: Fetch only records updated after this time
            start_date: Filter by transaction date start
            end_date: Filter by transaction date end
            sync_to_modules: Whether to also sync to BillCredit module
            
        Returns:
            List of synced VendorCredits
        """
        synced = []

        # QboHttpClient (via QboVendorCreditClient) resolves and refreshes the
        # access token lazily, so no upfront auth call is needed.
        with QboVendorCreditClient(realm_id=realm_id) as client:
            # Fetch all VendorCredits
            vendor_credits = client.query_all_vendor_credits(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
            )
            
            logger.info(f"Fetched {len(vendor_credits)} VendorCredits from QBO")
            
            # Process each VendorCredit
            for vc in vendor_credits:
                try:
                    local_vc = self._upsert_vendor_credit(vc, realm_id)
                    if local_vc:
                        synced.append(local_vc)
                        
                        # Sync line items
                        if vc.line:
                            self._upsert_vendor_credit_lines(local_vc.id, vc.line)
                    
                    # Small delay to avoid overwhelming DB
                    time.sleep(0.05)
                except Exception as e:
                    logger.error(f"Error syncing VendorCredit {vc.id}: {e}")
                    continue
        
        # Sync to BillCredit module if requested
        if sync_to_modules and synced:
            self._sync_to_bill_credits(synced)
        
        return synced

    def _upsert_vendor_credit(self, qbo_vc: QboVendorCreditSchema, realm_id: str) -> Optional[QboVendorCredit]:
        """Upsert a VendorCredit to local cache."""
        # Check if exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_vc.id, realm_id)
        
        # Build local model
        local_vc = QboVendorCredit(
            id=existing.id if existing else None,
            public_id=existing.public_id if existing else None,
            row_version=existing.row_version if existing else None,
            created_datetime=existing.created_datetime if existing else None,
            modified_datetime=existing.modified_datetime if existing else None,
            realm_id=realm_id,
            qbo_id=qbo_vc.id,
            sync_token=qbo_vc.sync_token,
            vendor_ref_value=qbo_vc.vendor_ref.value if qbo_vc.vendor_ref else None,
            vendor_ref_name=qbo_vc.vendor_ref.name if qbo_vc.vendor_ref else None,
            txn_date=qbo_vc.txn_date,
            doc_number=qbo_vc.doc_number,
            total_amt=qbo_vc.total_amt,
            private_note=qbo_vc.private_note,
            ap_account_ref_value=qbo_vc.ap_account_ref.value if qbo_vc.ap_account_ref else None,
            ap_account_ref_name=qbo_vc.ap_account_ref.name if qbo_vc.ap_account_ref else None,
            currency_ref_value=qbo_vc.currency_ref.value if qbo_vc.currency_ref else None,
            currency_ref_name=qbo_vc.currency_ref.name if qbo_vc.currency_ref else None,
        )
        
        if existing:
            return self.repo.update_by_qbo_id(local_vc)
        else:
            return self.repo.create(local_vc)

    def _upsert_vendor_credit_lines(self, qbo_vendor_credit_id: int, lines: List[QboVendorCreditLineSchema]) -> None:
        """Upsert VendorCredit line items."""
        # Delete existing lines and recreate (simpler than individual updates)
        self.repo.delete_lines_by_vendor_credit_id(qbo_vendor_credit_id)
        
        for line in lines:
            # Extract detail fields based on detail type
            item_ref_value = None
            item_ref_name = None
            class_ref_value = None
            class_ref_name = None
            unit_price = None
            qty = None
            billable_status = None
            customer_ref_value = None
            customer_ref_name = None
            account_ref_value = None
            account_ref_name = None
            
            if line.detail_type == "ItemBasedExpenseLineDetail" and line.item_based_expense_line_detail:
                detail = line.item_based_expense_line_detail
                if detail.item_ref:
                    item_ref_value = detail.item_ref.value
                    item_ref_name = detail.item_ref.name
                if detail.class_ref:
                    class_ref_value = detail.class_ref.value
                    class_ref_name = detail.class_ref.name
                unit_price = detail.unit_price
                qty = detail.qty
                billable_status = detail.billable_status
                if detail.customer_ref:
                    customer_ref_value = detail.customer_ref.value
                    customer_ref_name = detail.customer_ref.name
            
            elif line.detail_type == "AccountBasedExpenseLineDetail" and line.account_based_expense_line_detail:
                detail = line.account_based_expense_line_detail
                if detail.account_ref:
                    account_ref_value = detail.account_ref.value
                    account_ref_name = detail.account_ref.name
                if detail.class_ref:
                    class_ref_value = detail.class_ref.value
                    class_ref_name = detail.class_ref.name
                billable_status = detail.billable_status
                if detail.customer_ref:
                    customer_ref_value = detail.customer_ref.value
                    customer_ref_name = detail.customer_ref.name
            
            local_line = QboVendorCreditLine(
                id=None,
                public_id=None,
                row_version=None,
                created_datetime=None,
                modified_datetime=None,
                qbo_vendor_credit_id=qbo_vendor_credit_id,
                qbo_line_id=line.id,
                line_num=line.line_num,
                description=line.description,
                amount=line.amount,
                detail_type=line.detail_type,
                item_ref_value=item_ref_value,
                item_ref_name=item_ref_name,
                class_ref_value=class_ref_value,
                class_ref_name=class_ref_name,
                unit_price=unit_price,
                qty=qty,
                billable_status=billable_status,
                customer_ref_value=customer_ref_value,
                customer_ref_name=customer_ref_name,
                account_ref_value=account_ref_value,
                account_ref_name=account_ref_name,
            )
            
            self.repo.create_line(local_line)

    def _sync_to_bill_credits(self, vendor_credits: List[QboVendorCredit]) -> None:
        """Sync VendorCredits to BillCredit module via connector."""
        from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import VendorCreditBillCreditConnector
        
        connector = VendorCreditBillCreditConnector()
        
        for vc in vendor_credits:
            try:
                lines = self.repo.read_lines_by_vendor_credit_id(vc.id)
                connector.sync_from_qbo_vendor_credit(vc, lines)
            except Exception as e:
                logger.error(f"Error syncing VendorCredit {vc.id} to BillCredit: {e}")

    # Read methods
    def read_all(self) -> List[QboVendorCredit]:
        return self.repo.read_by_realm_id("all")  # Would need to implement read_all

    def read_by_realm_id(self, realm_id: str) -> List[QboVendorCredit]:
        return self.repo.read_by_realm_id(realm_id)

    def read_by_id(self, id: int) -> Optional[QboVendorCredit]:
        return self.repo.read_by_id(id)

    def read_by_qbo_id(self, qbo_id: str, realm_id: str) -> Optional[QboVendorCredit]:
        return self.repo.read_by_qbo_id_and_realm_id(qbo_id, realm_id)

    def read_lines_by_vendor_credit_id(self, qbo_vendor_credit_id: int) -> List[QboVendorCreditLine]:
        return self.repo.read_lines_by_vendor_credit_id(qbo_vendor_credit_id)
