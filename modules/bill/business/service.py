# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from modules.bill.business.model import Bill
from modules.bill.persistence.repo import BillRepository
from modules.vendor.business.service import VendorService

logger = logging.getLogger(__name__)


class BillService:
    """
    Service for Bill entity business operations.
    """

    def __init__(self, repo: Optional[BillRepository] = None):
        """Initialize the BillService."""
        self.repo = repo or BillRepository()

    def create(self, *, vendor_public_id: str, terms_id: Optional[int] = None, bill_date: str, due_date: str, bill_number: str, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> Bill:
        """
        Create a new bill.
        """
        if not vendor_public_id:
            raise ValueError("Vendor is required.")
        if not bill_date:
            raise ValueError("Bill date is required.")
        if not due_date:
            raise ValueError("Due date is required.")
        if not bill_number:
            raise ValueError("Bill number is required.")
        
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
        vendor_id = vendor.id
        
        # Check if a bill with the same BillNumber and VendorId already exists
        existing = self.repo.read_by_bill_number_and_vendor_id(bill_number=bill_number, vendor_id=vendor_id)
        if existing:
            raise ValueError(f"A bill with BillNumber '{bill_number}' already exists for this vendor. Please update the existing bill instead of creating a new one.")
        
        return self.repo.create(
            vendor_id=vendor_id,
            terms_id=terms_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=is_draft,
        )

    def read_all(self) -> list[Bill]:
        """
        Read all bills.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Bill]:
        """
        Read a bill by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Bill]:
        """
        Read a bill by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_bill_number(self, bill_number: str) -> Optional[Bill]:
        """
        Read a bill by bill number.
        """
        return self.repo.read_by_bill_number(bill_number)

    def read_by_bill_number_and_vendor_public_id(self, bill_number: str, vendor_public_id: str) -> Optional[Bill]:
        """
        Read a bill by bill number and vendor public ID.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            return None
        return self.repo.read_by_bill_number_and_vendor_id(bill_number=bill_number, vendor_id=vendor.id)

    def update_by_public_id(self, public_id: str, bill) -> Optional[Bill]:
        """
        Update a bill by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        
        if not hasattr(bill, 'vendor_public_id') or not bill.vendor_public_id:
            raise ValueError("Vendor is required.")
        if not hasattr(bill, 'bill_date') or not bill.bill_date:
            raise ValueError("Bill date is required.")
        if not hasattr(bill, 'due_date') or not bill.due_date:
            raise ValueError("Due date is required.")
        if not hasattr(bill, 'bill_number') or not bill.bill_number:
            raise ValueError("Bill number is required.")
        
        existing.row_version = bill.row_version
        
        # Convert vendor_public_id to vendor_id
        vendor = VendorService().read_by_public_id(public_id=bill.vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{bill.vendor_public_id}' not found.")
        existing.vendor_id = vendor.id
        
        existing.terms_id = bill.terms_id
        existing.bill_date = bill.bill_date
        existing.due_date = bill.due_date
        existing.bill_number = bill.bill_number
        existing.total_amount = bill.total_amount
        existing.memo = bill.memo
        # Only update is_draft if explicitly provided
        if hasattr(bill, 'is_draft') and bill.is_draft is not None:
            existing.is_draft = bill.is_draft
        
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Bill]:
        """
        Delete a bill by public ID with cascading deletes.
        
        Process:
        1. Get the bill by public_id
        2. Get all BillLineItems for this bill
        3. For each BillLineItem:
           a. Get its BillLineItemAttachment (1-1 relationship)
           b. If attachment exists:
              - Get the Attachment record
              - Delete the file from Azure Blob Storage (if blob_url exists)
              - Delete the Attachment record from database
              - Delete the BillLineItemAttachment record
           c. Delete the BillLineItem record
        4. Delete the Bill record
        
        This will cascade delete:
        - BillLineItemAttachment records
        - Attachment records (and files from Azure Blob Storage)
        - BillLineItem records
        - Bill record
        """
        # Import here to avoid circular import
        from modules.bill_line_item.business.service import BillLineItemService
        from modules.bill_line_item_attachment.business.service import BillLineItemAttachmentService
        from modules.bill_line_item_attachment.persistence.repo import BillLineItemAttachmentRepository
        from modules.attachment.business.service import AttachmentService
        from shared.storage import AzureBlobStorage, AzureBlobStorageError
        
        # Step 1: Get the bill
        existing = self.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None
        
        bill_id = existing.id
        
        # Step 2: Get all BillLineItems for this bill
        bill_line_item_service = BillLineItemService()
        bill_line_items = bill_line_item_service.read_by_bill_id(bill_id=bill_id)
        
        # Step 3: Delete each BillLineItem and its associated attachments
        bill_line_item_attachment_service = BillLineItemAttachmentService()
        bill_line_item_attachment_repo = BillLineItemAttachmentRepository()
        attachment_service = AttachmentService()
        
        # Initialize storage once (may fail if config is missing, handle gracefully)
        storage = None
        try:
            storage = AzureBlobStorage()
        except Exception as e:
            logger.warning(f"Could not initialize Azure Blob Storage for file deletion: {e}")
        
        for line_item in bill_line_items:
            try:
                # Step 3a: Get the BillLineItemAttachment for this line item (1-1 relationship)
                if line_item.public_id:
                    attachment_link = bill_line_item_attachment_service.read_by_bill_line_item_id(
                        bill_line_item_public_id=line_item.public_id
                    )
                    
                    # Step 3b: Delete attachment and its file if it exists
                    if attachment_link and attachment_link.attachment_id:
                        try:
                            # Get the attachment record
                            attachment = attachment_service.read_by_id(id=attachment_link.attachment_id)
                            if attachment:
                                # Delete from Azure Blob Storage if blob_url exists
                                if attachment.blob_url and storage:
                                    try:
                                        storage.delete_file(attachment.blob_url)
                                        logger.info(f"Deleted blob {attachment.blob_url} for attachment {attachment.id}")
                                    except AzureBlobStorageError as e:
                                        logger.warning(f"Error deleting blob {attachment.blob_url} for attachment {attachment.id}: {e}")
                                    except Exception as e:
                                        logger.warning(f"Error deleting blob {attachment.blob_url} for attachment {attachment.id}: {e}")
                                
                                # Delete the Attachment record
                                try:
                                    attachment_service.delete_by_public_id(public_id=attachment.public_id)
                                    logger.info(f"Deleted attachment {attachment.id}")
                                except Exception as e:
                                    logger.warning(f"Error deleting attachment {attachment.id}: {e}")
                            
                            # Delete the BillLineItemAttachment record
                            if attachment_link.id:
                                try:
                                    bill_line_item_attachment_repo.delete_by_id(id=attachment_link.id)
                                    logger.info(f"Deleted bill line item attachment {attachment_link.id}")
                                except Exception as e:
                                    logger.warning(f"Error deleting bill line item attachment {attachment_link.id}: {e}")
                        except Exception as e:
                            logger.warning(f"Error processing attachment for line item {line_item.id}: {e}")
                
                # Step 3c: Delete the BillLineItem record
                if line_item.id and line_item.public_id:
                    try:
                        bill_line_item_service.delete_by_public_id(public_id=line_item.public_id)
                        logger.info(f"Deleted bill line item {line_item.id}")
                    except Exception as e:
                        logger.warning(f"Error deleting bill line item {line_item.id}: {e}")
                elif line_item.id:
                    # Fallback: delete directly by ID if public_id is missing
                    try:
                        from modules.bill_line_item.persistence.repo import BillLineItemRepository
                        bill_line_item_repo = BillLineItemRepository()
                        bill_line_item_repo.delete_by_id(id=line_item.id)
                        logger.info(f"Deleted bill line item {line_item.id} (by ID, no public_id)")
                    except Exception as e:
                        logger.warning(f"Error deleting bill line item {line_item.id} by ID: {e}")
            except Exception as e:
                logger.warning(f"Error processing bill line item {line_item.id if line_item.id else 'unknown'}: {e}")
        
        # Step 4: Delete the Bill record
        return self.repo.delete_by_id(existing.id)
