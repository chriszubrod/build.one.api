# Python Standard Library Imports
import logging
from typing import List, Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit, QboVendorCreditLine
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo import VendorCreditBillCreditMappingRepository
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.vendor.business.service import VendorService

logger = logging.getLogger(__name__)


class VendorCreditBillCreditConnector:
    """Connector service for syncing QBO VendorCredits to BillCredits."""

    def __init__(self):
        self.mapping_repo = VendorCreditBillCreditMappingRepository()
        self.bill_credit_service = BillCreditService()
        self.bill_credit_line_item_service = BillCreditLineItemService()
        self.vendor_service = VendorService()

    def sync_from_qbo_vendor_credit(
        self,
        qbo_vc: QboVendorCredit,
        qbo_lines: List[QboVendorCreditLine],
    ) -> Optional[BillCredit]:
        """
        Sync a QBO VendorCredit to BillCredit module.
        
        Process:
        1. Resolve vendor via VendorRefValue -> QboVendor -> VendorVendor -> Vendor
        2. Check if BillCredit already exists via mapping table
        3. Create or update BillCredit
        4. Create mapping record
        5. Sync line items
        """
        try:
            # Step 1: Resolve vendor
            vendor_public_id = self._get_vendor_public_id(qbo_vc.vendor_ref_value)
            if not vendor_public_id:
                logger.warning(f"Cannot sync VendorCredit {qbo_vc.qbo_id}: vendor not found for ref {qbo_vc.vendor_ref_value}")
                return None
            
            # Step 2: Check for existing mapping
            existing_mapping = self.mapping_repo.read_by_qbo_vendor_credit_id(qbo_vc.id)
            
            if existing_mapping:
                # Update existing BillCredit
                bill_credit = self.bill_credit_service.read_by_id(existing_mapping.bill_credit_id)
                if bill_credit:
                    updated = self.bill_credit_service.update_by_public_id(
                        public_id=bill_credit.public_id,
                        row_version=bill_credit.row_version,
                        vendor_public_id=vendor_public_id,
                        credit_date=qbo_vc.txn_date,
                        credit_number=qbo_vc.doc_number or f"QBO-{qbo_vc.qbo_id}",
                        total_amount=Decimal(str(qbo_vc.total_amt)) if qbo_vc.total_amt else None,
                        memo=qbo_vc.private_note,
                    )
                    if updated:
                        # Sync line items
                        self._sync_line_items(updated.id, updated.public_id, qbo_lines)
                    return updated
            
            # Step 3: Create new BillCredit
            bill_credit = self.bill_credit_service.create(
                vendor_public_id=vendor_public_id,
                credit_date=qbo_vc.txn_date,
                credit_number=qbo_vc.doc_number or f"QBO-{qbo_vc.qbo_id}",
                total_amount=Decimal(str(qbo_vc.total_amt)) if qbo_vc.total_amt else None,
                memo=qbo_vc.private_note,
                is_draft=False,
            )
            
            if bill_credit:
                # Step 4: Create mapping
                self.mapping_repo.create(
                    qbo_vendor_credit_id=qbo_vc.id,
                    bill_credit_id=bill_credit.id,
                )
                
                # Step 5: Sync line items
                self._sync_line_items(bill_credit.id, bill_credit.public_id, qbo_lines)
                
                logger.info(f"Created BillCredit {bill_credit.public_id} from VendorCredit {qbo_vc.qbo_id}")
            
            return bill_credit
            
        except Exception as e:
            logger.error(f"Error syncing VendorCredit {qbo_vc.qbo_id} to BillCredit: {e}")
            return None

    def _get_vendor_public_id(self, qbo_vendor_ref_value: Optional[str]) -> Optional[str]:
        """Resolve QBO vendor ref (QBO API string ID) to local Vendor public_id.
        Same two-step lookup as PurchaseExpenseConnector: QboVendor by qbo_id, then VendorVendor by QboVendor.Id.
        """
        if not qbo_vendor_ref_value:
            return None
        
        try:
            from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
            from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
            
            qbo_vendor_repo = QboVendorRepository()
            vendor_vendor_repo = VendorVendorRepository()
            
            # Step 1: Find local QboVendor by QBO API vendor ID (string)
            qbo_vendor = qbo_vendor_repo.read_by_qbo_id(qbo_vendor_ref_value)
            if not qbo_vendor or not qbo_vendor.id:
                logger.warning(f"QboVendor not found for qbo_id: {qbo_vendor_ref_value}")
                return None
            
            # Step 2: Find VendorVendor mapping by local QboVendor.Id (integer)
            mapping = vendor_vendor_repo.read_by_qbo_vendor_id(qbo_vendor.id)
            if not mapping or not mapping.vendor_id:
                logger.warning(f"VendorVendor mapping not found for QboVendor ID: {qbo_vendor.id}")
                return None
            
            vendor = self.vendor_service.read_by_id(id=mapping.vendor_id)
            if not vendor:
                logger.warning(f"Vendor not found for ID: {mapping.vendor_id}")
                return None
            
            return vendor.public_id
        except Exception as e:
            logger.warning(f"Error resolving vendor ref {qbo_vendor_ref_value}: {e}")
            return None

    def _sync_line_items(
        self,
        bill_credit_id: int,
        bill_credit_public_id: str,
        qbo_lines: List[QboVendorCreditLine],
    ) -> None:
        """
        Sync line items from QBO VendorCredit to BillCreditLineItems.

        NOTE: Current implementation is delete-then-recreate, which loses any
        local-only state on the line items (attachments, etc.) on every sync.
        Task #17 (Shape B line matching) could not be applied here because
        fingerprint matching requires upsert-in-place semantics.

        TODO: refactor to upsert-in-place like Bill's _sync_line_items in
        `bill/connector/bill/business/service.py`. Steps:
          1. For each qbo_line, look up existing BillCreditLineItem via the
             VendorCreditLineItemBillCreditLineItem mapping table.
          2. Update in place if found; create if not.
          3. Delete only lines whose mapping is no longer referenced by any
             current qbo_line (true stale-line cleanup).
        Blocks full line-matching parity with Bill (task #17).
        """
        from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import VendorCreditLineItemConnector

        connector = VendorCreditLineItemConnector()

        # Delete existing line items and recreate (see TODO above).
        existing_items = self.bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id)
        for item in existing_items:
            try:
                self.bill_credit_line_item_service.delete_by_public_id(item.public_id)
            except Exception as e:
                logger.warning(f"Error deleting line item {item.id}: {e}")

        # Create new line items from QBO lines
        for line in qbo_lines:
            try:
                connector.sync_from_qbo_line(bill_credit_public_id, line)
            except Exception as e:
                logger.warning(f"Error syncing line item {line.qbo_line_id}: {e}")
