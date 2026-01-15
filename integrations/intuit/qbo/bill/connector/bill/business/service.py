# Python Standard Library Imports
import logging
from typing import List, Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.bill.connector.bill.business.model import BillBill
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.business.model import QboBill, QboBillLine
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from modules.bill.business.service import BillService
from modules.bill.business.model import Bill
from modules.vendor.business.service import VendorService

logger = logging.getLogger(__name__)


class BillBillConnector:
    """
    Connector service for synchronization between QboBill and Bill modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[BillBillRepository] = None,
        bill_service: Optional[BillService] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_vendor_repo: Optional[VendorVendorRepository] = None,
        qbo_vendor_repo: Optional[QboVendorRepository] = None,
    ):
        """Initialize the BillBillConnector."""
        self.mapping_repo = mapping_repo or BillBillRepository()
        self.bill_service = bill_service or BillService()
        self.vendor_service = vendor_service or VendorService()
        self.vendor_vendor_repo = vendor_vendor_repo or VendorVendorRepository()
        self.qbo_vendor_repo = qbo_vendor_repo or QboVendorRepository()

    def sync_from_qbo_bill(self, qbo_bill: QboBill, qbo_bill_lines: List[QboBillLine]) -> Bill:
        """
        Sync data from QboBill to Bill module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Bill accordingly
        3. Syncs line items to BillLineItem module
        
        Args:
            qbo_bill: QboBill record
            qbo_bill_lines: List of QboBillLine records for this bill
        
        Returns:
            Bill: The synced Bill record
        """
        # Find vendor mapping to get Vendor public_id
        vendor_public_id = self._get_vendor_public_id(qbo_bill.vendor_ref_value)
        if not vendor_public_id:
            raise ValueError(f"No vendor mapping found for QBO vendor ref: {qbo_bill.vendor_ref_value}")
        
        # Map QBO Bill fields to Bill module fields
        bill_number = qbo_bill.doc_number or f"QBO-{qbo_bill.qbo_id}"
        bill_date = qbo_bill.txn_date or ""
        due_date = qbo_bill.due_date or ""
        memo = qbo_bill.private_note
        total_amount = qbo_bill.total_amt
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_bill_id(qbo_bill.id)
        
        if mapping:
            # Found existing mapping - update the Bill
            bill = self.bill_service.read_by_id(mapping.bill_id)
            if bill:
                logger.info(f"Updating existing Bill {bill.id} from QboBill {qbo_bill.id}")
                
                # Create an update object
                class BillUpdate:
                    pass
                
                update = BillUpdate()
                update.vendor_public_id = vendor_public_id
                update.bill_date = bill_date
                update.due_date = due_date
                update.bill_number = bill_number
                update.total_amount = total_amount
                update.memo = memo
                update.terms_id = None
                update.is_draft = False
                update.row_version = bill.row_version
                
                bill = self.bill_service.update_by_public_id(bill.public_id, update)
                
                # Sync line items for existing bill
                self._sync_line_items(bill.id, qbo_bill_lines)
                
                return bill
            else:
                # Mapping exists but Bill not found - recreate Bill
                logger.warning(f"Mapping exists but Bill {mapping.bill_id} not found. Creating new Bill.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new Bill
        logger.info(f"Creating new Bill from QboBill {qbo_bill.id}: bill_number={bill_number}")
        bill = self.bill_service.create(
            vendor_public_id=vendor_public_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=False
        )
        
        # Create mapping
        bill_id = int(bill.id) if isinstance(bill.id, str) else bill.id
        try:
            mapping = self.create_mapping(bill_id=bill_id, qbo_bill_id=qbo_bill.id)
            logger.info(f"Created mapping: Bill {bill_id} <-> QboBill {qbo_bill.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        # Sync line items for new bill
        self._sync_line_items(bill_id, qbo_bill_lines)
        
        return bill

    def _get_vendor_public_id(self, qbo_vendor_ref_value: str) -> Optional[str]:
        """
        Get the Vendor public_id from QBO vendor reference value.
        
        Args:
            qbo_vendor_ref_value: QBO vendor reference value (QBO Vendor ID)
        
        Returns:
            str: Vendor public_id or None
        """
        if not qbo_vendor_ref_value:
            return None
        
        # First find the QboVendor by qbo_id
        qbo_vendor = self.qbo_vendor_repo.read_by_qbo_id(qbo_vendor_ref_value)
        if not qbo_vendor:
            logger.warning(f"QboVendor not found for qbo_id: {qbo_vendor_ref_value}")
            return None
        
        # Then find the VendorVendor mapping
        vendor_mapping = self.vendor_vendor_repo.read_by_qbo_vendor_id(qbo_vendor.id)
        if not vendor_mapping:
            logger.warning(f"VendorVendor mapping not found for QboVendor ID: {qbo_vendor.id}")
            return None
        
        # Get the Vendor
        vendor = self.vendor_service.read_by_id(vendor_mapping.vendor_id)
        if not vendor:
            logger.warning(f"Vendor not found for ID: {vendor_mapping.vendor_id}")
            return None
        
        return vendor.public_id

    def _sync_line_items(self, bill_id: int, qbo_bill_lines: List[QboBillLine]) -> None:
        """
        Sync bill line items to BillLineItem module.
        
        Args:
            bill_id: Database ID of the Bill
            qbo_bill_lines: List of QboBillLine records
        """
        if not qbo_bill_lines:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
        
        line_connector = BillLineItemConnector()
        
        for qbo_line in qbo_bill_lines:
            try:
                line_connector.sync_from_qbo_bill_line(bill_id, qbo_line)
            except Exception as e:
                logger.error(f"Failed to sync QboBillLine {qbo_line.id} to BillLineItem: {e}")

    def create_mapping(self, bill_id: int, qbo_bill_id: int) -> BillBill:
        """
        Create a mapping between Bill and QboBill.
        
        Args:
            bill_id: Database ID of Bill record
            qbo_bill_id: Database ID of QboBill record
        
        Returns:
            BillBill: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_bill = self.mapping_repo.read_by_bill_id(bill_id)
        if existing_by_bill:
            raise ValueError(
                f"Bill {bill_id} is already mapped to QboBill {existing_by_bill.qbo_bill_id}"
            )
        
        existing_by_qbo_bill = self.mapping_repo.read_by_qbo_bill_id(qbo_bill_id)
        if existing_by_qbo_bill:
            raise ValueError(
                f"QboBill {qbo_bill_id} is already mapped to Bill {existing_by_qbo_bill.bill_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(bill_id=bill_id, qbo_bill_id=qbo_bill_id)

    def get_mapping_by_bill_id(self, bill_id: int) -> Optional[BillBill]:
        """
        Get mapping by Bill ID.
        """
        return self.mapping_repo.read_by_bill_id(bill_id)

    def get_mapping_by_qbo_bill_id(self, qbo_bill_id: int) -> Optional[BillBill]:
        """
        Get mapping by QboBill ID.
        """
        return self.mapping_repo.read_by_qbo_bill_id(qbo_bill_id)
