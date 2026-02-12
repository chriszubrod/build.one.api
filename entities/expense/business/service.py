# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.expense.business.model import Expense
from entities.expense.persistence.repo import ExpenseRepository
from entities.vendor.business.service import VendorService

logger = logging.getLogger(__name__)


class ExpenseService:
    """
    Service for Expense entity business operations.
    """

    def __init__(self, repo: Optional[ExpenseRepository] = None):
        """Initialize the ExpenseService."""
        self.repo = repo or ExpenseRepository()

    def create(self, *, tenant_id: int = 1, vendor_public_id: str, expense_date: str, reference_number: str, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> Expense:
        """
        Create a new expense.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            vendor_public_id: Vendor public ID (required)
            expense_date: Expense date
            reference_number: Reference number
            total_amount: Total amount (optional)
            memo: Memo (optional)
            is_draft: Whether expense is in draft state
        """
        if not vendor_public_id:
            raise ValueError("Vendor is required.")
        if not expense_date:
            raise ValueError("Expense date is required.")
        if not reference_number:
            raise ValueError("Reference number is required.")
        
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
        vendor_id = vendor.id
        
        # Check if an expense with the same ReferenceNumber and VendorId already exists
        existing = self.repo.read_by_reference_number_and_vendor_id(reference_number=reference_number, vendor_id=vendor_id)
        if existing:
            raise ValueError(f"An expense with ReferenceNumber '{reference_number}' already exists for this vendor. Please update the existing expense instead of creating a new one.")
        
        return self.repo.create(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            expense_date=expense_date,
            reference_number=reference_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=is_draft,
        )

    def read_all(self) -> list[Expense]:
        """
        Read all expenses.
        """
        return self.repo.read_all()

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        sort_by: str = "ExpenseDate",
        sort_direction: str = "DESC",
    ) -> list[Expense]:
        """
        Read expenses with pagination and filtering.
        """
        return self.repo.read_paginated(
            page_number=page_number,
            page_size=page_size,
            search_term=search_term,
            vendor_id=vendor_id,
            start_date=start_date,
            end_date=end_date,
            is_draft=is_draft,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
    ) -> int:
        """
        Count expenses matching the filter criteria.
        """
        return self.repo.count(
            search_term=search_term,
            vendor_id=vendor_id,
            start_date=start_date,
            end_date=end_date,
            is_draft=is_draft,
        )

    def read_by_id(self, id: int) -> Optional[Expense]:
        """
        Read an expense by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Expense]:
        """
        Read an expense by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_reference_number_and_vendor_public_id(self, reference_number: str, vendor_public_id: str) -> Optional[Expense]:
        """
        Read an expense by reference number and vendor public ID.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            return None
        return self.repo.read_by_reference_number_and_vendor_id(reference_number=reference_number, vendor_id=vendor.id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        vendor_public_id: str = None,
        expense_date: str = None,
        reference_number: str = None,
        total_amount: float = None,
        memo: str = None,
        is_draft: bool = None,
    ) -> Optional[Expense]:
        """
        Update an expense by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        
        existing.row_version = row_version
        
        # Convert vendor_public_id to vendor_id if provided
        if vendor_public_id is not None:
            vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
            if not vendor:
                raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
            existing.vendor_id = vendor.id
        
        if expense_date is not None:
            existing.expense_date = expense_date
        if reference_number is not None:
            existing.reference_number = reference_number
        if total_amount is not None:
            existing.total_amount = Decimal(str(total_amount))
        if memo is not None:
            existing.memo = memo
        if is_draft is not None:
            existing.is_draft = is_draft
        
        updated_expense = self.repo.update_by_id(existing)
        
        return updated_expense

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Expense]:
        """
        Delete an expense by public ID with cascading deletes.
        
        TODO: In Phase 10, validate tenant_id matches record's tenant
        
        Process:
        1. Get the expense by public_id
        2. Get all ExpenseLineItems for this expense
        3. For each ExpenseLineItem:
           a. Get its ExpenseLineItemAttachment (1-1 relationship)
           b. If attachment exists:
              - Get the Attachment record
              - Delete the file from Azure Blob Storage (if blob_url exists)
              - Delete the Attachment record from database
              - Delete the ExpenseLineItemAttachment record
           c. Delete the ExpenseLineItem record
        4. Delete the Expense record
        """
        # Import here to avoid circular import
        from entities.expense_line_item.business.service import ExpenseLineItemService
        from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
        from entities.expense_line_item_attachment.persistence.repo import ExpenseLineItemAttachmentRepository
        from entities.attachment.business.service import AttachmentService
        from shared.storage import AzureBlobStorage, AzureBlobStorageError
        
        # Step 1: Get the expense
        existing = self.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None
        
        expense_id = existing.id
        
        # Step 2: Get all ExpenseLineItems for this expense
        expense_line_item_service = ExpenseLineItemService()
        expense_line_items = expense_line_item_service.read_by_expense_id(expense_id=expense_id)
        
        # Step 3: Delete each ExpenseLineItem and its associated attachments
        expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
        expense_line_item_attachment_repo = ExpenseLineItemAttachmentRepository()
        attachment_service = AttachmentService()
        
        # Initialize storage once (may fail if config is missing, handle gracefully)
        storage = None
        try:
            storage = AzureBlobStorage()
        except Exception as e:
            logger.warning(f"Could not initialize Azure Blob Storage for file deletion: {e}")
        
        for line_item in expense_line_items:
            try:
                # Step 3a: Get the ExpenseLineItemAttachment for this line item (1-1 relationship)
                if line_item.public_id:
                    attachment_link = expense_line_item_attachment_service.read_by_expense_line_item_id(
                        expense_line_item_public_id=line_item.public_id
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
                            
                            # Delete the ExpenseLineItemAttachment record
                            if attachment_link.id:
                                try:
                                    expense_line_item_attachment_repo.delete_by_id(id=attachment_link.id)
                                    logger.info(f"Deleted expense line item attachment {attachment_link.id}")
                                except Exception as e:
                                    logger.warning(f"Error deleting expense line item attachment {attachment_link.id}: {e}")
                        except Exception as e:
                            logger.warning(f"Error processing attachment for line item {line_item.id}: {e}")
                
                # Step 3c: Delete the ExpenseLineItem record
                if line_item.id and line_item.public_id:
                    try:
                        expense_line_item_service.delete_by_public_id(public_id=line_item.public_id)
                        logger.info(f"Deleted expense line item {line_item.id}")
                    except Exception as e:
                        logger.warning(f"Error deleting expense line item {line_item.id}: {e}")
                elif line_item.id:
                    # Fallback: delete directly by ID if public_id is missing
                    try:
                        from entities.expense_line_item.persistence.repo import ExpenseLineItemRepository
                        expense_line_item_repo = ExpenseLineItemRepository()
                        expense_line_item_repo.delete_by_id(id=line_item.id)
                        logger.info(f"Deleted expense line item {line_item.id} (by ID, no public_id)")
                    except Exception as e:
                        logger.warning(f"Error deleting expense line item {line_item.id} by ID: {e}")
            except Exception as e:
                logger.warning(f"Error processing expense line item {line_item.id if line_item.id else 'unknown'}: {e}")
        
        # Step 4: Delete the Expense record
        return self.repo.delete_by_id(existing.id)

    def complete_expense(self, public_id: str) -> dict:
        """
        Complete an expense: finalize and upload attachments to module folders.
        Delegates to ExpenseCompleteService.
        """
        from entities.expense.business.complete_service import ExpenseCompleteService
        return ExpenseCompleteService().complete_expense(public_id=public_id)
