# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from services.bill.business.model import Bill
from services.bill.persistence.repo import BillRepository
from services.vendor.business.service import VendorService

logger = logging.getLogger(__name__)


class BillService:
    """
    Service for Bill entity business operations.
    """

    def __init__(self, repo: Optional[BillRepository] = None):
        """Initialize the BillService."""
        self.repo = repo or BillRepository()

    def create(self, *, tenant_id: int = 1, vendor_public_id: str, payment_term_public_id: Optional[str] = None, bill_date: str, due_date: str, bill_number: str, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> Bill:
        """
        Create a new bill.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            vendor_public_id: Vendor public ID (required)
            payment_term_public_id: Payment term public ID (optional)
            bill_date: Bill date
            due_date: Due date
            bill_number: Bill number
            total_amount: Total amount (optional)
            memo: Memo (optional)
            is_draft: Whether bill is in draft state
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
        
        # Resolve payment_term_public_id to payment_term_id
        payment_term_id = None
        if payment_term_public_id:
            from services.payment_term.business.service import PaymentTermService
            payment_term = PaymentTermService().read_by_public_id(public_id=payment_term_public_id)
            if payment_term:
                payment_term_id = payment_term.id
        
        # Check if a bill with the same BillNumber and VendorId already exists
        existing = self.repo.read_by_bill_number_and_vendor_id(bill_number=bill_number, vendor_id=vendor_id)
        if existing:
            raise ValueError(f"A bill with BillNumber '{bill_number}' already exists for this vendor. Please update the existing bill instead of creating a new one.")
        
        return self.repo.create(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            payment_term_id=payment_term_id,
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
        sort_by: str = "BillDate",
        sort_direction: str = "DESC",
    ) -> list[Bill]:
        """
        Read bills with pagination and filtering.
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
        Count bills matching the filter criteria.
        """
        return self.repo.count(
            search_term=search_term,
            vendor_id=vendor_id,
            start_date=start_date,
            end_date=end_date,
            is_draft=is_draft,
        )

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

    def sync_attachments_to_sharepoint(self, bill_public_id: str) -> dict:
        """
        Sync BillLineItem attachments to ProjectModule SharePoint driveitems when a bill is completed.
        
        For each BillLineItem with an attachment:
        - Downloads the attachment from Azure Blob Storage
        - Gets the ProjectModule driveitem for the BillLineItem's Project
        - Generates filename: {Project.Abbreviation} - {Vendor.Name} - {Bill.Number} - {BillLineItem.Description} - {SubCostCode.Number} - {BillLineItem.Price} - {Bill.BillDate}
        - Uploads to SharePoint
        - Deduplicates: if the same attachment is used for multiple line items, only uploads once
        
        Args:
            bill_public_id: Public ID of the bill
            
        Returns:
            Dict with:
            - success: bool
            - message: str
            - synced_count: int (number of files successfully synced)
            - errors: list of dict with line_item info and error message
        """
        # Import here to avoid circular imports
        from services.bill_line_item.business.service import BillLineItemService
        from services.bill_line_item_attachment.business.service import BillLineItemAttachmentService
        from services.attachment.business.service import AttachmentService
        from services.project.business.service import ProjectService
        from services.sub_cost_code.business.service import SubCostCodeService
        from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
        from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        from services.module.business.service import ModuleService
        from shared.storage import AzureBlobStorage, AzureBlobStorageError
        
        bill = self.read_by_public_id(public_id=bill_public_id)
        if not bill or not bill.id:
            return {
                "success": False,
                "message": "Bill not found",
                "synced_count": 0,
                "errors": []
            }
        
        # Get vendor for filename
        vendor = None
        if bill.vendor_id:
            vendor = VendorService().read_by_id(id=bill.vendor_id)
        
        if not vendor:
            return {
                "success": False,
                "message": "Vendor not found for bill",
                "synced_count": 0,
                "errors": []
            }
        
        # Get all bill line items
        bill_line_item_service = BillLineItemService()
        bill_line_items = bill_line_item_service.read_by_bill_id(bill_id=bill.id)
        
        if not bill_line_items:
            return {
                "success": True,
                "message": "No line items to sync",
                "synced_count": 0,
                "errors": []
            }
        
        # Get attachments for each line item
        bill_line_item_attachment_service = BillLineItemAttachmentService()
        attachment_service = AttachmentService()
        project_service = ProjectService()
        sub_cost_code_service = SubCostCodeService()
        module_service = ModuleService()
        project_module_connector = DriveItemProjectModuleConnector()
        driveitem_service = MsDriveItemService()
        drive_repo = MsDriveRepository()
        
        # Initialize storage
        try:
            storage = AzureBlobStorage()
        except Exception as e:
            logger.error(f"Failed to initialize Azure Blob Storage: {e}")
            return {
                "success": False,
                "message": f"Failed to initialize storage: {str(e)}",
                "synced_count": 0,
                "errors": []
            }
        
        # Track which attachments we've already uploaded (to avoid duplicates)
        uploaded_attachments = {}  # key: attachment_id, value: filename used
        synced_count = 0
        errors = []
        
        # Try to get the "Bills" module, fallback to first module if not found
        module = module_service.read_by_name("Bills")
        if not module:
            # Try "Invoices" as alternative
            module = module_service.read_by_name("Invoices")
        if not module:
            # Get first available module as fallback
            all_modules = module_service.read_all()
            if all_modules:
                module = all_modules[0]
                logger.warning(f"Using module '{module.name}' (ID: {module.id}) as fallback for bill sync")
            else:
                return {
                    "success": False,
                    "message": "No modules found. Please create a module first.",
                    "synced_count": 0,
                    "errors": []
                }
        
        module_id = int(module.id) if module.id else None
        
        for line_item in bill_line_items:
            try:
                # Skip if no project
                if not line_item.project_id:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": "Line item has no project"
                    })
                    continue
                
                # Get attachment for this line item
                if not line_item.public_id:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": None,
                        "error": "Line item missing public_id"
                    })
                    continue
                
                attachment_link = bill_line_item_attachment_service.read_by_bill_line_item_id(
                    bill_line_item_public_id=line_item.public_id
                )
                
                if not attachment_link or not attachment_link.attachment_id:
                    # No attachment for this line item, skip
                    continue
                
                # Check if we've already uploaded this attachment
                if attachment_link.attachment_id in uploaded_attachments:
                    logger.info(f"Attachment {attachment_link.attachment_id} already uploaded, skipping duplicate")
                    synced_count += 1
                    continue
                
                # Get attachment record
                attachment = attachment_service.read_by_id(id=attachment_link.attachment_id)
                if not attachment or not attachment.blob_url:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": "Attachment not found or missing blob_url"
                    })
                    continue
                
                # Get project
                project = project_service.read_by_id(id=str(line_item.project_id))
                if not project:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Project {line_item.project_id} not found"
                    })
                    continue
                
                # Get ProjectModule driveitem
                module_folder = project_module_connector.get_folder_for_module(
                    project_id=line_item.project_id,
                    module_id=module_id
                )
                
                if not module_folder:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"ProjectModule folder not found for project {line_item.project_id}, module {module_id}"
                    })
                    continue
                
                # module_folder is a dict from driveitem.to_dict()
                # Extract needed values directly from the dict
                folder_item_id = module_folder.get("item_id")
                folder_ms_drive_id = module_folder.get("ms_drive_id")
                
                if not folder_item_id:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Module folder missing item_id"
                    })
                    continue
                
                if not folder_ms_drive_id:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Module folder missing ms_drive_id"
                    })
                    continue
                
                # Get drive for upload
                drive = drive_repo.read_by_id(folder_ms_drive_id)
                if not drive:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Drive not found for driveitem"
                    })
                    continue
                
                # Get SubCostCode if available
                sub_cost_code = None
                sub_cost_code_number = ""
                if line_item.sub_cost_code_id:
                    sub_cost_code = sub_cost_code_service.read_by_id(id=str(line_item.sub_cost_code_id))
                    if sub_cost_code:
                        sub_cost_code_number = sub_cost_code.number or ""
                
                # Generate filename
                # Format: {Project.Name} - {Vendor.Abbreviation} - {Bill.Number} - {BillLineItem.Description} - {SubCostCode.Number} - {BillLineItem.Price} - {Bill.BillDate}
                # Note: Project doesn't have abbreviation field, so using name
                # Vendor has abbreviation field, use it with fallback to name
                project_abbreviation = project.name or ""
                vendor_abbreviation = vendor.abbreviation or vendor.name or ""
                bill_number = bill.bill_number or ""
                description = line_item.description or ""
                price = str(line_item.price) if line_item.price is not None else ""
                bill_date = bill.bill_date[:10] if bill.bill_date else ""  # Get YYYY-MM-DD part
                
                # Build filename parts
                filename_parts = [
                    project_abbreviation,
                    vendor_abbreviation,
                    bill_number,
                    description,
                    sub_cost_code_number,
                    price,
                    bill_date
                ]
                
                # Filter out empty parts and join with " - "
                filename_parts = [part for part in filename_parts if part]
                base_filename = " - ".join(filename_parts)
                
                # Sanitize filename (remove invalid characters)
                import re
                base_filename = re.sub(r'[<>:"/\\|?*]', '_', base_filename)
                
                # Get original file extension
                file_extension = attachment.file_extension or ""
                if file_extension and not file_extension.startswith("."):
                    file_extension = "." + file_extension
                
                filename = base_filename + file_extension
                
                # Download attachment from Azure Blob Storage
                try:
                    logger.info(f"Downloading attachment from blob: {attachment.blob_url}")
                    file_content, metadata = storage.download_file(attachment.blob_url)
                    logger.info(f"Successfully downloaded attachment (size: {len(file_content)} bytes)")
                except AzureBlobStorageError as e:
                    error_msg = str(e)
                    logger.error(f"Failed to download attachment from blob storage: {error_msg}. Blob URL: {attachment.blob_url}")
                    # Check if it's a 404 (blob not found) - might have been deleted or never uploaded correctly
                    if "404" in error_msg or "BlobNotFound" in error_msg:
                        errors.append({
                            "line_item_id": line_item.id,
                            "line_item_public_id": line_item.public_id,
                            "error": f"Attachment file not found in blob storage (may have been deleted). Blob URL: {attachment.blob_url}"
                        })
                    else:
                        errors.append({
                            "line_item_id": line_item.id,
                            "line_item_public_id": line_item.public_id,
                            "error": f"Failed to download attachment from storage: {error_msg}"
                        })
                    continue
                except Exception as e:
                    logger.exception(f"Unexpected error downloading attachment from blob: {attachment.blob_url}")
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Error downloading attachment: {str(e)}"
                    })
                    continue
                
                # Upload to SharePoint
                content_type = attachment.content_type or metadata.get("content_type", "application/octet-stream")
                logger.info(f"Uploading file '{filename}' to SharePoint folder '{module_folder.get('name')}' (item_id: {folder_item_id})")
                upload_result = driveitem_service.upload_file(
                    drive_public_id=drive.public_id,
                    parent_item_id=folder_item_id,
                    filename=filename,
                    content=file_content,
                    content_type=content_type
                )
                
                upload_status = upload_result.get("status_code")
                if upload_status not in [200, 201]:
                    error_msg = upload_result.get('message', 'Unknown error')
                    logger.error(f"Failed to upload '{filename}' to SharePoint: {error_msg} (status: {upload_status})")
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Failed to upload to SharePoint: {error_msg} (status: {upload_status})"
                    })
                    continue
                
                # Mark as uploaded
                uploaded_attachments[attachment_link.attachment_id] = filename
                synced_count += 1
                uploaded_item = upload_result.get("item", {})
                logger.info(f"Successfully synced attachment {attachment_link.attachment_id} for line item {line_item.id} to SharePoint as '{filename}' (item_id: {uploaded_item.get('item_id')}, web_url: {uploaded_item.get('web_url')})")
                
            except Exception as e:
                logger.exception(f"Error syncing attachment for line item {line_item.id if line_item.id else 'unknown'}")
                errors.append({
                    "line_item_id": line_item.id,
                    "line_item_public_id": line_item.public_id,
                    "error": f"Unexpected error: {str(e)}"
                })
        
        success = len(errors) == 0 or synced_count > 0
        message = f"Synced {synced_count} file(s) to SharePoint"
        if errors:
            message += f" with {len(errors)} error(s)"
        
        return {
            "success": success,
            "message": message,
            "synced_count": synced_count,
            "errors": errors
        }

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        vendor_public_id: str = None,
        payment_term_public_id: str = None,
        bill_date: str = None,
        due_date: str = None,
        bill_number: str = None,
        total_amount: float = None,
        memo: str = None,
        is_draft: bool = None,
    ) -> Optional[Bill]:
        """
        Update a bill by public ID.
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
        
        # Resolve payment_term_public_id to payment_term_id
        if payment_term_public_id is not None:
            from services.payment_term.business.service import PaymentTermService
            payment_term = PaymentTermService().read_by_public_id(public_id=payment_term_public_id)
            existing.payment_term_id = payment_term.id if payment_term else None
        
        if bill_date is not None:
            existing.bill_date = bill_date
        if due_date is not None:
            existing.due_date = due_date
        if bill_number is not None:
            existing.bill_number = bill_number
        if total_amount is not None:
            existing.total_amount = Decimal(str(total_amount))
        if memo is not None:
            existing.memo = memo
        if is_draft is not None:
            existing.is_draft = is_draft
        
        # Note: SharePoint sync is now handled by BillCompleteService when "Complete Bill" is clicked
        # This avoids duplicate uploads when the bill is finalized
        
        updated_bill = self.repo.update_by_id(existing)
        
        return updated_bill

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Bill]:
        """
        Delete a bill by public ID with cascading deletes.
        
        TODO: In Phase 10, validate tenant_id matches record's tenant
        
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
        from services.bill_line_item.business.service import BillLineItemService
        from services.bill_line_item_attachment.business.service import BillLineItemAttachmentService
        from services.bill_line_item_attachment.persistence.repo import BillLineItemAttachmentRepository
        from services.attachment.business.service import AttachmentService
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
                        from services.bill_line_item.persistence.repo import BillLineItemRepository
                        bill_line_item_repo = BillLineItemRepository()
                        bill_line_item_repo.delete_by_id(id=line_item.id)
                        logger.info(f"Deleted bill line item {line_item.id} (by ID, no public_id)")
                    except Exception as e:
                        logger.warning(f"Error deleting bill line item {line_item.id} by ID: {e}")
            except Exception as e:
                logger.warning(f"Error processing bill line item {line_item.id if line_item.id else 'unknown'}: {e}")
        
        # Step 4: Delete the Bill record
        return self.repo.delete_by_id(existing.id)
