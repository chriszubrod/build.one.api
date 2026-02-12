# Python Standard Library Imports
import logging
import re
import time
from collections import defaultdict
from decimal import Decimal
from typing import Any, List, Optional

# Third-party Imports

# Local Imports
from entities.attachment.business.service import AttachmentService
from entities.bill.api.schemas import BillUpdate
from entities.bill.business.model import Bill
from entities.bill.persistence.repo import BillRepository
from entities.bill_line_item.api.schemas import BillLineItemUpdate
from entities.bill_line_item_attachment.persistence.repo import BillLineItemAttachmentRepository
from entities.payment_term.business.service import PaymentTermService
from entities.project.business.service import ProjectService
from entities.vendor.business.service import VendorService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.module.business.service import ModuleService

from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import DriveItemProjectExcelConnector
from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.external.client import (
    get_excel_used_range_values,
    insert_excel_rows,
    append_excel_rows
)

from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)


def find_insertion_row_for_subcostcode(worksheet_values: List[List[Any]], target_subcostcode: str) -> Optional[int]:
    """
    Find the row number where a new line item should be inserted based on SubCostCode.
    
    Logic:
    1. Find all rows where Column C matches the target SubCostCode (the "block")
    2. Within that block:
       - First priority: Insert after the last row that has BOTH Date (Column I) AND Payable To (Column J)
       - Fallback: If no row has both Date and Payable To, insert after the SECOND row with the SubCostCode
    
    Args:
        worksheet_values: 2D array of cell values from the worksheet
        target_subcostcode: The SubCostCode number to match (e.g., "65.03" or "37")
    
    Returns:
        The 1-based row number where the new row should be inserted,
        or None if no matching SubCostCode found (append at end)
    """
    if not worksheet_values:
        return None
    
    # Collect all matching rows for this SubCostCode
    matching_rows = []  # List of (excel_row, has_date_and_payable)
    
    # Skip row 0 (header row in 0-based index = row 1 in Excel)
    for row_index, row in enumerate(worksheet_values):
        # Skip header row (row_index 0 = Excel row 1)
        if row_index == 0:
            continue
        
        # Excel row number is 1-based
        excel_row = row_index + 1
        
        # Get column C (index 2) - SubCostCode
        col_c_value = row[2] if len(row) > 2 else None
        
        # Get column I (index 8) - DATE
        col_i_value = row[8] if len(row) > 8 else None
        
        # Get column J (index 9) - Payable To (Vendor)
        col_j_value = row[9] if len(row) > 9 else None
        
        # Normalize the SubCostCode for comparison
        subcostcode_match = False
        if col_c_value is not None:
            col_c_str = str(col_c_value).strip()
            target_str = str(target_subcostcode).strip()
            
            # Skip empty values
            if not col_c_str:
                continue
            
            # Try exact match first
            if col_c_str == target_str:
                subcostcode_match = True
            else:
                # Try numeric comparison (65.03 == 65.03, 37 == 37.0)
                try:
                    if float(col_c_str) == float(target_str):
                        subcostcode_match = True
                except (ValueError, TypeError):
                    pass
        
        if subcostcode_match:
            # Check if this row has both Date (Column I) AND Payable To (Column J)
            has_date = col_i_value is not None and str(col_i_value).strip() != ""
            has_payable = col_j_value is not None and str(col_j_value).strip() != ""
            has_date_and_payable = has_date and has_payable
            
            matching_rows.append((excel_row, has_date_and_payable))
    
    if not matching_rows:
        logger.info(f"SubCostCode '{target_subcostcode}': No matching rows found, will append at end")
        return None
    
    # Find the last row with both Date and Payable To
    last_row_with_data = None
    for excel_row, has_date_and_payable in matching_rows:
        if has_date_and_payable:
            last_row_with_data = excel_row
    
    if last_row_with_data is not None:
        # Insert after the last row with Date and Payable To
        logger.info(f"SubCostCode '{target_subcostcode}': Found data row at {last_row_with_data}, inserting at row {last_row_with_data + 1}")
        return last_row_with_data + 1
    
    # Fallback: Insert after the second row with the SubCostCode (if exists)
    if len(matching_rows) >= 2:
        second_row = matching_rows[1][0]
        logger.info(f"SubCostCode '{target_subcostcode}': No data rows, inserting after second template row at {second_row}, inserting at row {second_row + 1}")
        return second_row + 1
    elif len(matching_rows) == 1:
        # Only one matching row, insert after it
        first_row = matching_rows[0][0]
        logger.info(f"SubCostCode '{target_subcostcode}': Only one template row at {first_row}, inserting at row {first_row + 1}")
        return first_row + 1
    
    logger.info(f"SubCostCode '{target_subcostcode}': No matching rows found, will append at end")
    return None


class BillService:
    """
    Service for Bill entity business operations.
    """

    def __init__(self, repo: Optional[BillRepository] = None):
        """Initialize the BillService."""
        from entities.bill_line_item.business.service import BillLineItemService
        from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
        self.repo = repo or BillRepository()
        self.bill_line_item_service = BillLineItemService()
        self.bill_line_item_attachment_service = BillLineItemAttachmentService()
        self.attachment_service = AttachmentService()
        self.project_service = ProjectService()
        self.vendor_service = VendorService()
        self.sub_cost_code_service = SubCostCodeService()
        self.module_service = ModuleService()
        self._project_module_connector: Optional[Any] = None
        self._project_excel_connector: Optional[Any] = None
        self._driveitem_service: Optional[Any] = None
        self._drive_repo: Optional[Any] = None
        self._qbo_bill_connector: Optional[Any] = None
        self._qbo_auth_service: Optional[Any] = None

    @property
    def project_module_connector(self):
        if self._project_module_connector is None:
            self._project_module_connector = DriveItemProjectModuleConnector()
        return self._project_module_connector

    @property
    def project_excel_connector(self):
        if self._project_excel_connector is None:
            self._project_excel_connector = DriveItemProjectExcelConnector()
        return self._project_excel_connector

    @property
    def driveitem_service(self):
        if self._driveitem_service is None:
            self._driveitem_service = MsDriveItemService()
        return self._driveitem_service

    @property
    def drive_repo(self):
        if self._drive_repo is None:
            self._drive_repo = MsDriveRepository()
        return self._drive_repo

    @property
    def qbo_bill_connector(self):
        if self._qbo_bill_connector is None:
            from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
            self._qbo_bill_connector = BillBillConnector()
        return self._qbo_bill_connector

    @property
    def qbo_auth_service(self):
        if self._qbo_auth_service is None:
            from integrations.intuit.qbo.auth.business.service import QboAuthService
            self._qbo_auth_service = QboAuthService()
        return self._qbo_auth_service

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

            payment_term = PaymentTermService().read_by_public_id(public_id=payment_term_public_id)

            if not payment_term:
                raise ValueError(f"Payment term with public_id '{payment_term_public_id}' not found.")

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
        bill_line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill.id)
        
        if not bill_line_items:
            return {
                "success": True,
                "message": "No line items to sync",
                "synced_count": 0,
                "errors": []
            }
        
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
        module = self.module_service.read_by_name("Bills")

        if not module:
            # Try "Invoices" as alternative
            module = self.module_service.read_by_name("Invoices")
        
        if not module:
            # Get first available module as fallback
            all_modules = self.module_service.read_all()

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
                
                attachment_link = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
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
                attachment = self.attachment_service.read_by_id(id=attachment_link.attachment_id)
                if not attachment or not attachment.blob_url:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": "Attachment not found or missing blob_url"
                    })
                    continue
                
                # Get project
                project = self.project_service.read_by_id(id=str(line_item.project_id))
                if not project:
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Project {line_item.project_id} not found"
                    })
                    continue
                
                # Get ProjectModule driveitem
                module_folder = self.project_module_connector.get_folder_for_module(
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
                drive = self.drive_repo.read_by_id(folder_ms_drive_id)
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
                    sub_cost_code = self.sub_cost_code_service.read_by_id(id=str(line_item.sub_cost_code_id))
                    if sub_cost_code:
                        sub_cost_code_number = sub_cost_code.number or ""
                
                # Generate filename
                # When bill has >1 line item: {Project} - {Vendor} - {BillNumber} - Multiple See Image - {Amount} - {Date}
                # Otherwise: {Project.Name} - {Vendor} - {Bill.Number} - {Description} - {SubCostCode.Number} - {Price} - {BillDate}
                project_abbreviation = project.name or ""
                vendor_abbreviation = vendor.abbreviation or vendor.name or ""
                bill_number = bill.bill_number or ""
                bill_date = ""
                if bill.bill_date:
                    try:
                        date_str = bill.bill_date[:10]
                        parts = date_str.split("-")
                        if len(parts) == 3:
                            bill_date = f"{parts[1]}-{parts[2]}-{parts[0]}"  # mm-dd-yyyy
                        else:
                            bill_date = bill.bill_date[:10]
                    except Exception:
                        bill_date = bill.bill_date[:10]
                if len(bill_line_items) > 1:
                    amount_str = ""
                    if bill.total_amount is not None:
                        try:
                            amount_str = f"${float(bill.total_amount):,.2f}"
                        except (ValueError, TypeError):
                            amount_str = f"${bill.total_amount}"
                    filename_parts = [
                        project_abbreviation,
                        vendor_abbreviation,
                        bill_number,
                        "Multiple See Image",
                        amount_str,
                        bill_date
                    ]
                else:
                    description = line_item.description or ""
                    price = str(line_item.price) if line_item.price is not None else ""
                    bill_date = bill.bill_date[:10] if bill.bill_date else ""  # YYYY-MM-DD for single-line (original)
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
                upload_result = self.driveitem_service.upload_file(
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
            from entities.payment_term.business.service import PaymentTermService
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
        
        # Note: SharePoint sync is performed in complete_bill when "Complete Bill" is clicked.
        # This avoids duplicate uploads when the bill is finalized.
        
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
                        from entities.bill_line_item.persistence.repo import BillLineItemRepository
                        bill_line_item_repo = BillLineItemRepository()
                        bill_line_item_repo.delete_by_id(id=line_item.id)
                        logger.info(f"Deleted bill line item {line_item.id} (by ID, no public_id)")
                    except Exception as e:
                        logger.warning(f"Error deleting bill line item {line_item.id} by ID: {e}")
            except Exception as e:
                logger.warning(f"Error processing bill line item {line_item.id if line_item.id else 'unknown'}: {e}")
        
        # Step 4: Delete the Bill record
        return self.repo.delete_by_id(existing.id)


    def _rename_invoice_blob_on_complete(
        self, public_id: str, line_items: list, all_errors: list
    ) -> None:
        """
        On Mark Complete: rename invoice blob from invoices/{invoice_number}.pdf to {bill.public_id}.pdf
        and update the Attachment record. Only renames when blob_url is under invoices/.
        On failure, appends to all_errors and does not update the DB (DB never points at a missing blob).
        """
        if not line_items:
            return
        link = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
            bill_line_item_public_id=line_items[0].public_id
        )
        if not link or not link.attachment_id:
            return
        attachment = self.attachment_service.read_by_id(id=link.attachment_id)
        if not attachment or not attachment.blob_url or "invoices/" not in attachment.blob_url:
            return
        new_blob_name = f"{public_id}.pdf"
        storage = AzureBlobStorage()
        try:
            file_content, _ = storage.download_file(attachment.blob_url)
            new_url = storage.upload_file(
                blob_name=new_blob_name,
                file_content=file_content,
                content_type=attachment.content_type or "application/pdf",
            )
            self.attachment_service.update_by_public_id(
                public_id=attachment.public_id,
                row_version=attachment.row_version,
                blob_url=new_url,
                filename=new_blob_name,
                original_filename=new_blob_name,
            )
            storage.delete_file(attachment.blob_url)
            logger.info(f"Renamed invoice blob to {new_blob_name}")
        except AzureBlobStorageError as e:
            logger.exception(f"Blob rename failed for bill {public_id}")
            all_errors.append({"step": "rename_invoice_blob", "error": str(e)})
        except Exception as e:
            logger.exception(f"Blob rename failed for bill {public_id}")
            all_errors.append({"step": "rename_invoice_blob", "error": str(e)})

    def complete_bill(self, public_id: str) -> dict:
        """
        Complete a bill: finalize, upload attachments to module folders, and sync to Excel workbooks.
        
        Args:
            public_id: Public ID of the bill
            
        Returns:
            Dict with status_code, message, and detailed results for each step
        """
        # Step 1: Finalize Bill
        bill = self.read_by_public_id(public_id=public_id)
        if not bill:
            return {
                "status_code": 404,
                "message": "Bill not found",
                "bill_finalized": False,
                "file_uploads": {},
                "excel_syncs": {},
                "qbo_sync": {},
                "errors": []
            }
        
        # Check if already finalized
        if not bill.is_draft:
            logger.info(f"Bill {public_id} is already finalized")
        
        # Finalize Bill (set is_draft=False)
        # Use retry logic to handle race conditions with auto-save
        try:
            
            finalized_bill = None
            max_retries = 3
            
            for attempt in range(max_retries):
                # Re-read bill to get latest row_version (handles auto-save race condition)
                bill = self.read_by_public_id(public_id=public_id)
                if not bill:
                    return {
                        "status_code": 404,
                        "message": "Bill not found during finalization",
                        "bill_finalized": False,
                        "file_uploads": {},
                        "excel_syncs": {},
                        "qbo_sync": {},
                        "errors": []
                    }
                
                # Get vendor to get vendor_public_id
                vendor = None
                if bill.vendor_id:
                    vendor = self.vendor_service.read_by_id(id=bill.vendor_id)
                
                if not vendor or not vendor.public_id:
                    return {
                        "status_code": 400,
                        "message": "Vendor not found for bill",
                        "bill_finalized": False,
                        "file_uploads": {},
                        "excel_syncs": {},
                        "qbo_sync": {},
                        "errors": [{"step": "finalize_bill", "error": "Vendor not found"}]
                    }
                
                # Get payment term public_id if set
                payment_term_public_id = None
                if bill.payment_term_id:
                    payment_term = PaymentTermService().read_by_id(id=bill.payment_term_id)
                    if payment_term:
                        payment_term_public_id = payment_term.public_id
                
                bill_update = BillUpdate(
                    row_version=bill.row_version,
                    vendor_public_id=vendor.public_id,
                    payment_term_public_id=payment_term_public_id,
                    bill_date=bill.bill_date,
                    due_date=bill.due_date,
                    bill_number=bill.bill_number,
                    total_amount=bill.total_amount,
                    memo=bill.memo,
                    is_draft=False
                )
                
                finalized_bill = self.update_by_public_id(public_id=public_id, **bill_update.model_dump())
                
                if finalized_bill:
                    logger.info(f"Bill {public_id} finalized on attempt {attempt + 1}")
                    break
                else:
                    logger.warning(f"Bill {public_id} finalize attempt {attempt + 1} failed (row_version conflict?), retrying...")
                    if attempt < max_retries - 1:
                        time.sleep(0.2)  # Brief delay before retry
            
            if not finalized_bill:
                return {
                    "status_code": 500,
                    "message": "Failed to finalize bill after retries (concurrent modification)",
                    "bill_finalized": False,
                    "file_uploads": {},
                    "excel_syncs": {},
                    "qbo_sync": {},
                    "errors": [{"step": "finalize_bill", "error": "Row version conflict after retries"}]
                }
        except Exception as e:
            logger.exception(f"Error finalizing bill {public_id}")
            return {
                "status_code": 500,
                "message": f"Error finalizing bill: {str(e)}",
                "bill_finalized": False,
                "file_uploads": {},
                "excel_syncs": {},
                "qbo_sync": {},
                "errors": [{"step": "finalize_bill", "error": str(e)}]
            }
        
        # Step 2: Finalize all BillLineItems
        line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill.id)
        line_item_errors = []
        for line_item in line_items:
            if line_item.is_draft:
                try:
                    # Get project_public_id if line_item has project_id
                    project_public_id = None
                    if line_item.project_id:
                        project = self.project_service.read_by_id(id=str(line_item.project_id))
                        if project:
                            project_public_id = project.public_id
                    
                    line_item_update = BillLineItemUpdate(
                        row_version=line_item.row_version,
                        bill_public_id=public_id,
                        sub_cost_code_id=line_item.sub_cost_code_id,
                        project_public_id=project_public_id,
                        description=line_item.description,
                        quantity=line_item.quantity,
                        rate=line_item.rate,
                        amount=line_item.amount,
                        is_billable=line_item.is_billable,
                        markup=line_item.markup,
                        price=line_item.price,
                        is_draft=False
                    )
                    self.bill_line_item_service.update_by_public_id(
                        public_id=line_item.public_id,
                        **line_item_update.model_dump()
                    )
                except Exception as e:
                    logger.error(f"Error finalizing line item {line_item.id}: {e}")
                    line_item_errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": str(e)
                    })
        
        # Step 2b: Rename invoice blob from invoices/... to {public_id}.pdf (contract labor invoice)
        self._rename_invoice_blob_on_complete(public_id=public_id, line_items=line_items, all_errors=line_item_errors)

        # Step 3: Upload attachments to module folders and sync to Excel
        file_upload_results = {}
        excel_sync_results = {}
        all_errors = line_item_errors.copy()
        
        # Group line items by project_id
        line_items_by_project = defaultdict(list)
        line_items_without_project = []
        for line_item in line_items:
            if line_item.project_id:
                line_items_by_project[line_item.project_id].append(line_item)
            else:
                line_items_without_project.append(line_item)
        
        logger.info(f"Complete Bill {public_id}: {len(line_items)} line items total")
        logger.info(f"  - {len(line_items_by_project)} projects with line items")
        logger.info(f"  - {len(line_items_without_project)} line items without project (will skip SharePoint sync)")
        
        if len(line_items_without_project) > 0:
            for li in line_items_without_project:
                logger.warning(f"  - Line item {li.public_id} has no project_id, skipping SharePoint sync")
        
        # Process each project
        for project_id, project_line_items in line_items_by_project.items():
            logger.info(f"Processing project {project_id} with {len(project_line_items)} line items")
            # Upload files to module folder
            upload_result = self._upload_attachments_to_module_folder(
                bill=bill,
                line_items=project_line_items,
                project_id=project_id,
                bill_line_items_count=len(line_items)
            )
            file_upload_results[project_id] = upload_result
            if upload_result.get("errors"):
                all_errors.extend(upload_result["errors"])
            
            # Sync to Excel workbook
            excel_result = self._sync_to_excel_workbook(
                bill=bill,
                line_items=project_line_items,
                project_id=project_id
            )
            excel_sync_results[project_id] = excel_result
            if excel_result.get("errors"):
                all_errors.extend(excel_result["errors"])
        
        # Step 5: Push Bill to Intuit QuickBooks Online
        qbo_sync_result = self._sync_to_qbo(bill=finalized_bill)
        if qbo_sync_result.get("errors"):
            all_errors.extend(qbo_sync_result["errors"])
        
        # Determine overall status
        has_errors = len(all_errors) > 0
        status_code = 200 if not has_errors else 207  # 207 = Multi-Status (partial success)
        message = "Bill completed successfully"
        if has_errors:
            message += f" with {len(all_errors)} error(s)"
        
        return {
            "status_code": status_code,
            "message": message,
            "bill_finalized": True,
            "file_uploads": file_upload_results,
            "excel_syncs": excel_sync_results,
            "qbo_sync": qbo_sync_result,
            "errors": all_errors
        }

    def _sync_to_qbo(self, bill) -> dict:
        """
        Push a completed Bill to Intuit QuickBooks Online.
        
        Args:
            bill: The finalized Bill record
            
        Returns:
            Dict with success, message, qbo_bill_id, and errors
        """
        try:

            qbo_auth = self.qbo_auth_service.ensure_valid_token()

            if not qbo_auth:

                logger.warning("No valid QBO auth found, skipping QBO sync")

                return {
                    "success": False,
                    "message": "No valid QBO authentication found",
                    "qbo_bill_id": None,
                    "errors": [{"step": "qbo_sync", "error": "No valid QBO authentication"}]
                }

            realm_id = qbo_auth.realm_id

            if not realm_id:

                logger.warning("QBO auth has no realm_id, skipping QBO sync")

                return {
                    "success": False,
                    "message": "QBO auth missing realm_id",
                    "qbo_bill_id": None,
                    "errors": [{"step": "qbo_sync", "error": "QBO auth missing realm_id"}]
                }

            logger.info(f"Syncing Bill {bill.public_id} to QBO realm {realm_id}")
            
            qbo_bill = self.qbo_bill_connector.sync_to_qbo_bill(bill=bill, realm_id=realm_id)
            
            if qbo_bill:

                logger.info(f"Successfully synced Bill {bill.public_id} to QBO as QboBill {qbo_bill.id} (qbo_id: {qbo_bill.qbo_id})")
                
                return {
                    "success": True,
                    "message": f"Synced to QBO Bill {qbo_bill.qbo_id}",
                    "qbo_bill_id": qbo_bill.qbo_id,
                    "errors": []
                }

            else:

                logger.error(f"QBO sync returned None for Bill {bill.public_id}")

                return {
                    "success": False,
                    "message": "QBO sync returned no result",
                    "qbo_bill_id": None,
                    "errors": [{"step": "qbo_sync", "error": "QBO sync returned no result"}]
                }

        except ValueError as e:

            error_msg = str(e)
            
            logger.warning(f"QBO sync skipped for Bill {bill.public_id}: {error_msg}")
            
            return {
                "success": False,
                "message": f"QBO sync skipped: {error_msg}",
                "qbo_bill_id": None,
                "errors": [{"step": "qbo_sync", "error": error_msg}]
            }

        except Exception as e:

            logger.exception(f"Error syncing Bill {bill.public_id} to QBO")

            return {
                "success": False,
                "message": f"QBO sync error: {str(e)}",
                "qbo_bill_id": None,
                "errors": [{"step": "qbo_sync", "error": str(e)}]
            }

    def _sync_to_excel_workbook(
        self,
        bill,
        line_items: List,
        project_id: int
    ) -> dict:
        """
        Sync Bill and BillLineItem data to the project's Excel workbook.
        Inserts rows after the last matching SubCostCode entry, or appends at end if not found.
        
        Returns:
            Dict with success, synced_count, and errors
        """
        try:

            excel_mapping = self.project_excel_connector.get_excel_for_project(project_id=project_id)

            if not excel_mapping:

                return {
                    "success": False,
                    "message": f"Excel workbook not linked for project {project_id}",
                    "synced_count": 0,
                    "errors": [{"error": f"Excel workbook not linked for project {project_id}"}]
                }
            
            worksheet_name = excel_mapping.get("worksheet_name")

            if not worksheet_name:

                return {
                    "success": False,
                    "message": "Worksheet name not found in Excel mapping",
                    "synced_count": 0,
                    "errors": [{"error": "Worksheet name not found"}]
                }

            driveitem_repo = MsDriveItemRepository()

            items = driveitem_repo.read_all()

            driveitem = next((item for item in items if item.id == excel_mapping.get("id")), None)

            if not driveitem:

                return {
                    "success": False,
                    "message": "DriveItem not found for Excel workbook",
                    "synced_count": 0,
                    "errors": [{"error": "DriveItem not found"}]
                }
            
            drive = self.drive_repo.read_by_id(driveitem.ms_drive_id)

            if not drive:

                return {
                    "success": False,
                    "message": "Drive not found for Excel workbook",
                    "synced_count": 0,
                    "errors": [{"error": "Drive not found"}]
                }
            
            vendor = None

            if bill.vendor_id:
                vendor = self.vendor_service.read_by_id(id=bill.vendor_id)
            
            if not vendor:
                return {
                    "success": False,
                    "message": "Vendor not found",
                    "synced_count": 0,
                    "errors": [{"error": "Vendor not found"}]
                }
            
            # Read current worksheet data to find insertion points
            logger.info(f"Reading worksheet '{worksheet_name}' to determine insertion points")
            
            worksheet_result = get_excel_used_range_values(
                drive_id=drive.drive_id,
                item_id=driveitem.item_id,
                worksheet_name=worksheet_name
            )
            
            worksheet_values = []
            if worksheet_result.get("status_code") == 200:
                range_data = worksheet_result.get("range", {})
                worksheet_values = range_data.get("values", [])
                logger.info(f"Worksheet has {len(worksheet_values)} rows")
            else:
                logger.warning(f"Could not read worksheet data: {worksheet_result.get('message')}. Will append at end.")
            
            # Group line items by SubCostCode
            line_items_by_subcostcode = defaultdict(list)
            for line_item in line_items:
                sub_cost_code_id = line_item.sub_cost_code_id if line_item.sub_cost_code_id else None
                line_items_by_subcostcode[sub_cost_code_id].append(line_item)
            
            errors = []
            synced_count = 0
            rows_to_append = []  # Rows that couldn't find a SubCostCode match
            
            # Process each SubCostCode group
            for sub_cost_code_id, subcostcode_line_items in line_items_by_subcostcode.items():
                # Get SubCostCode details
                sub_cost_code = None
                sub_cost_code_number = ""
                cost_code_number = ""
                
                if sub_cost_code_id:
                    sub_cost_code = self.sub_cost_code_service.read_by_id(id=str(sub_cost_code_id))
                    if sub_cost_code:
                        sub_cost_code_number = sub_cost_code.number or ""
                        # Derive CostCode from SubCostCode (e.g., "65" from "65.03")
                        if "." in sub_cost_code_number:
                            cost_code_number = sub_cost_code_number.split(".")[0]
                        else:
                            cost_code_number = sub_cost_code_number
                
                # Build rows for this SubCostCode group
                # Row structure: A(empty), B(CostCode), C(SubCostCode), D-H(empty), I(Date), J(Vendor), K(BillNum), L(Desc), M("Ck"), N(Price)
                group_rows = []
                for line_item in subcostcode_line_items:
                    try:
                        bill_date = bill.bill_date[:10] if bill.bill_date else ""  # YYYY-MM-DD
                        vendor_name = vendor.name or ""
                        bill_number = bill.bill_number or ""
                        description = line_item.description or ""
                        price = float(line_item.price) if line_item.price is not None else 0.0
                        
                        # Build full row (columns A through Z = 26 columns)
                        row = [
                            "",                   # A: Empty
                            cost_code_number,     # B: CostCode
                            sub_cost_code_number, # C: SubCostCode
                            "",                   # D: Empty
                            "",                   # E: Empty
                            "",                   # F: Empty
                            "",                   # G: Empty
                            "",                   # H: Empty
                            bill_date,            # I: Bill Date
                            vendor_name,          # J: Vendor
                            bill_number,          # K: Bill Number
                            description,          # L: Description
                            "Ck",                 # M: "Ck"
                            price,                # N: Price
                            "",                   # O: Empty
                            "",                   # P: Empty
                            "",                   # Q: Empty
                            "",                   # R: Empty
                            "",                   # S: Empty
                            "",                   # T: Empty
                            "",                   # U: Empty
                            "",                   # V: Empty
                            "",                   # W: Empty
                            "",                   # X: Empty
                            "",                   # Y: Empty
                            ""                    # Z: Empty
                        ]
                        group_rows.append(row)
                    except Exception as e:
                        logger.error(f"Error building Excel row for line item {line_item.id}: {e}")
                        errors.append({
                            "line_item_id": line_item.id,
                            "line_item_public_id": line_item.public_id,
                            "error": f"Error building Excel row: {str(e)}"
                        })
                
                if not group_rows:
                    continue
                
                # Find insertion row for this SubCostCode
                insertion_row = None
                if sub_cost_code_number and worksheet_values:
                    insertion_row = find_insertion_row_for_subcostcode(
                        worksheet_values=worksheet_values,
                        target_subcostcode=sub_cost_code_number
                    )
                    if insertion_row:
                        logger.info(f"SubCostCode {sub_cost_code_number}: inserting at row {insertion_row}")
                
                if insertion_row:
                    # Insert rows at the calculated position
                    insert_result = insert_excel_rows(
                        drive_id=drive.drive_id,
                        item_id=driveitem.item_id,
                        worksheet_name=worksheet_name,
                        row_index=insertion_row,
                        values=group_rows
                    )
                    
                    if insert_result.get("status_code") in [200, 201]:
                        synced_count += len(group_rows)
                        logger.info(f"Inserted {len(group_rows)} row(s) at row {insertion_row}")
                        # Update worksheet_values to account for inserted rows
                        # This is important for subsequent SubCostCode groups
                        for i, row in enumerate(group_rows):
                            # Insert at the correct position (0-based index is insertion_row - 1)
                            worksheet_values.insert(insertion_row - 1 + i, row)
                    else:
                        error_msg = insert_result.get('message', 'Unknown error')
                        logger.error(f"Failed to insert rows: {error_msg}")
                        errors.append({
                            "sub_cost_code": sub_cost_code_number,
                            "error": f"Failed to insert rows: {error_msg}"
                        })
                        # Fall back to appending these rows
                        rows_to_append.extend(group_rows)
                else:
                    # No matching SubCostCode found, append at end
                    logger.info(f"SubCostCode {sub_cost_code_number or 'None'}: no match found, will append at end")
                    rows_to_append.extend(group_rows)
            
            # Append any rows that didn't have matching SubCostCodes
            if rows_to_append:
                logger.info(f"Appending {len(rows_to_append)} row(s) to end of worksheet")
                append_result = append_excel_rows(
                    drive_id=drive.drive_id,
                    item_id=driveitem.item_id,
                    worksheet_name=worksheet_name,
                    values=rows_to_append
                )
                
                if append_result.get("status_code") in [200, 201]:
                    synced_count += len(rows_to_append)
                    logger.info(f"Appended {len(rows_to_append)} row(s)")
                else:
                    error_msg = append_result.get('message', 'Unknown error')
                    logger.error(f"Failed to append rows: {error_msg}")
                    errors.append({"error": f"Failed to append rows: {error_msg}"})
            
            if synced_count == 0 and not errors:
                return {
                    "success": True,
                    "message": "No rows to sync",
                    "synced_count": 0,
                    "errors": errors
                }
            
            logger.info(f"Successfully synced {synced_count} row(s) to Excel workbook")
            
            has_errors = len(errors) > 0
            return {
                "success": synced_count > 0 or not has_errors,
                "message": f"Synced {synced_count} row(s) to Excel workbook",
                "synced_count": synced_count,
                "errors": errors
            }
            
        except Exception as e:
            logger.exception(f"Error syncing to Excel workbook for project {project_id}")
            return {
                "success": False,
                "message": f"Error syncing to Excel: {str(e)}",
                "synced_count": 0,
                "errors": [{"error": str(e)}]
            }


    def _upload_attachments_to_module_folder(
        self,
        bill,
        line_items: List,
        project_id: int,
        bill_line_items_count: int = 1
    ) -> dict:
        """
        Upload attachments for line items to the project's module folder in SharePoint.
        Downloads from Azure Blob Storage and uploads to SharePoint with final filename.
        When bill has >1 line item, filename: {Project} - {Vendor} - {BillNumber} - Multiple See Image - {Amount} - {Date}.
        
        Returns:
            Dict with success, synced_count, and errors
        """
        try:
            # Get the Bill module
            module = self.module_service.read_by_name("Bills")
            if not module:
                module = self.module_service.read_by_name("Bill")
            if not module:
                module = self.module_service.read_by_name("Invoices")
            if not module:
                all_modules = self.module_service.read_all()
                module = all_modules[0] if all_modules else None
            
            if not module:
                return {
                    "success": False,
                    "message": "No modules found",
                    "synced_count": 0,
                    "errors": [{"error": "No modules found"}]
                }
            
            module_id = int(module.id) if module.id else None
            
            # Get module folder for this project
            module_folder = self.project_module_connector.get_folder_for_module(
                project_id=project_id,
                module_id=module_id
            )
            
            if not module_folder:
                return {
                    "success": False,
                    "message": f"Module folder not linked for project {project_id}",
                    "synced_count": 0,
                    "errors": [{"error": f"Module folder not linked for project {project_id}"}]
                }
            
            # Get drive
            folder_ms_drive_id = module_folder.get("ms_drive_id")
            folder_item_id = module_folder.get("item_id")
            if not folder_ms_drive_id or not folder_item_id:
                return {
                    "success": False,
                    "message": "Module folder missing drive or item_id",
                    "synced_count": 0,
                    "errors": [{"error": "Module folder missing drive or item_id"}]
                }
            
            drive = self.drive_repo.read_by_id(folder_ms_drive_id)
            if not drive:
                return {
                    "success": False,
                    "message": "Drive not found",
                    "synced_count": 0,
                    "errors": [{"error": "Drive not found"}]
                }
            
            # Get vendor
            vendor = None
            if bill.vendor_id:
                vendor = self.vendor_service.read_by_id(id=bill.vendor_id)
            
            if not vendor:
                return {
                    "success": False,
                    "message": "Vendor not found",
                    "synced_count": 0,
                    "errors": [{"error": "Vendor not found"}]
                }
            
            # Get project
            project = self.project_service.read_by_id(id=str(project_id))
            if not project:
                return {
                    "success": False,
                    "message": f"Project {project_id} not found",
                    "synced_count": 0,
                    "errors": [{"error": f"Project {project_id} not found"}]
                }
            
            # Initialize Azure Blob Storage
            try:
                storage = AzureBlobStorage()
            except Exception as e:
                return {
                    "success": False,
                    "message": f"Failed to initialize storage: {str(e)}",
                    "synced_count": 0,
                    "errors": [{"error": f"Failed to initialize storage: {str(e)}"}]
                }
            
            synced_count = 0
            errors = []
            uploaded_attachments = {}  # Track to avoid duplicates
            
            print(f"  SharePoint sync: Processing {len(line_items)} line items for project {project_id}")
            logger.info(f"SharePoint sync: Processing {len(line_items)} line items for project {project_id}")
            
            # Process each line item
            for line_item in line_items:
                print(f"    Checking line item {line_item.public_id}")
                logger.info(f"  Checking line item {line_item.public_id}")
                try:
                    if not line_item.public_id:
                        continue
                    
                    # Get attachment link
                    attachment_link = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
                        bill_line_item_public_id=line_item.public_id
                    )
                    
                    if not attachment_link or not attachment_link.attachment_id:
                        print(f"      -> No attachment for this line item")
                        logger.info(f"    No attachment for line item {line_item.public_id}")
                        continue  # No attachment for this line item
                    
                    print(f"      -> Found attachment, attachment_id={attachment_link.attachment_id}")
                    logger.info(f"    Found attachment link for line item {line_item.public_id}, attachment_id={attachment_link.attachment_id}")
                    
                    # Check if already uploaded
                    if attachment_link.attachment_id in uploaded_attachments:
                        logger.info(f"Attachment {attachment_link.attachment_id} already uploaded, skipping")
                        synced_count += 1
                        continue
                    
                    # Get attachment record
                    attachment = self.attachment_service.read_by_id(id=attachment_link.attachment_id)
                    if not attachment or not attachment.blob_url:
                        errors.append({
                            "line_item_id": line_item.id,
                            "line_item_public_id": line_item.public_id,
                            "error": "Attachment not found or missing blob_url"
                        })
                        continue
                    
                    # Get SubCostCode for filename
                    sub_cost_code_number = ""
                    if line_item.sub_cost_code_id:
                        sub_cost_code = self.sub_cost_code_service.read_by_id(id=str(line_item.sub_cost_code_id))
                        if sub_cost_code:
                            sub_cost_code_number = sub_cost_code.number or ""
                    
                    # Generate SharePoint filename using final Bill/BillLineItem values
                    # When bill has >1 line item: {Project} - {Vendor} - {BillNumber} - Multiple See Image - {Amount} - {Date}
                    project_identifier = project.abbreviation or project.name or ""
                    vendor_abbreviation = vendor.abbreviation or vendor.name or ""
                    bill_number = bill.bill_number or ""
                    # Format price with $ and commas (e.g., $10,000.00)
                    price = ""
                    if line_item.price is not None:
                        try:
                            price_val = float(line_item.price)
                            price = f"${price_val:,.2f}"
                        except (ValueError, TypeError):
                            price = f"${line_item.price}"
                    # Format date as mm-dd-yyyy
                    bill_date = ""
                    if bill.bill_date:
                        try:
                            date_str = bill.bill_date[:10]
                            parts = date_str.split("-")
                            if len(parts) == 3:
                                bill_date = f"{parts[1]}-{parts[2]}-{parts[0]}"  # mm-dd-yyyy
                            else:
                                bill_date = bill.bill_date[:10]
                        except Exception:
                            bill_date = bill.bill_date[:10]  # Fallback to original
                    if bill_line_items_count > 1:
                        amount_str = ""
                        if bill.total_amount is not None:
                            try:
                                amount_str = f"${float(bill.total_amount):,.2f}"
                            except (ValueError, TypeError):
                                amount_str = f"${bill.total_amount}"
                        filename_parts = [
                            project_identifier,
                            vendor_abbreviation,
                            bill_number,
                            "Multiple See Image",
                            amount_str,
                            bill_date
                        ]
                    else:
                        description = line_item.description or ""
                        filename_parts = [
                            project_identifier,
                            vendor_abbreviation,
                            bill_number,
                            description,
                            sub_cost_code_number,
                            price,
                            bill_date
                        ]
                    filename_parts = [part for part in filename_parts if part]
                    base_filename = " - ".join(filename_parts)
                    base_filename = re.sub(r'[<>:"/\\|?*]', '_', base_filename)
                    
                    # Get file extension - try multiple sources
                    file_extension = attachment.file_extension or ""
                    if not file_extension and attachment.original_filename:
                        # Try to extract from original filename
                        if '.' in attachment.original_filename:
                            file_extension = attachment.original_filename.rsplit('.', 1)[-1]
                    if not file_extension and attachment.content_type:
                        # Try to infer from content type
                        content_type_map = {
                            'application/pdf': 'pdf',
                            'image/jpeg': 'jpg',
                            'image/png': 'png',
                            'image/gif': 'gif',
                            'application/msword': 'doc',
                            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
                            'application/vnd.ms-excel': 'xls',
                            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
                        }
                        file_extension = content_type_map.get(attachment.content_type, '')
                    
                    if file_extension and not file_extension.startswith("."):
                        file_extension = "." + file_extension
                    
                    sharepoint_filename = base_filename + file_extension
                    
                    # Download from Azure Blob Storage
                    try:
                        logger.info(f"Downloading attachment from Azure: {attachment.blob_url}")
                        file_content, metadata = storage.download_file(attachment.blob_url)
                        logger.info(f"Downloaded {len(file_content)} bytes")
                    except AzureBlobStorageError as e:
                        error_msg = str(e)
                        logger.error(f"Failed to download: {error_msg}")
                        errors.append({
                            "line_item_id": line_item.id,
                            "line_item_public_id": line_item.public_id,
                            "error": f"Failed to download from Azure: {error_msg}"
                        })
                        continue
                    except Exception as e:
                        logger.exception("Error downloading attachment")
                        errors.append({
                            "line_item_id": line_item.id,
                            "line_item_public_id": line_item.public_id,
                            "error": f"Error downloading: {str(e)}"
                        })
                        continue
                    
                    # Upload to SharePoint
                    content_type = attachment.content_type or metadata.get("content_type", "application/octet-stream")
                    logger.info(f"Uploading '{sharepoint_filename}' to SharePoint folder '{module_folder.get('name')}'")
                    
                    upload_result = self.driveitem_service.upload_file(
                        drive_public_id=drive.public_id,
                        parent_item_id=folder_item_id,
                        filename=sharepoint_filename,
                        content=file_content,
                        content_type=content_type
                    )
                    
                    upload_status = upload_result.get("status_code")
                    if upload_status not in [200, 201]:
                        error_msg = upload_result.get('message', 'Unknown error')
                        logger.error(f"SharePoint upload failed: {error_msg}")
                        errors.append({
                            "line_item_id": line_item.id,
                            "line_item_public_id": line_item.public_id,
                            "error": f"SharePoint upload failed: {error_msg}"
                        })
                        continue
                    
                    # Success
                    uploaded_attachments[attachment_link.attachment_id] = sharepoint_filename
                    synced_count += 1
                    uploaded_item = upload_result.get("item", {})
                    logger.info(f"Uploaded to SharePoint: '{sharepoint_filename}' (item_id: {uploaded_item.get('item_id')})")
                    
                    # Store the DriveItem-Attachment mapping for direct lookup later
                    ms_driveitem_id = uploaded_item.get("id")
                    if ms_driveitem_id and attachment.id:
                        try:
                            from integrations.ms.sharepoint.driveitem.connector.attachment.business.service import DriveItemAttachmentConnector
                            attachment_connector = DriveItemAttachmentConnector()
                            link_result = attachment_connector.link_driveitem_to_attachment(
                                attachment_id=attachment.id,
                                ms_driveitem_id=ms_driveitem_id
                            )
                            if link_result.get("status_code") in [200, 201]:
                                logger.info(f"Linked attachment {attachment.id} to DriveItem {ms_driveitem_id}")
                            else:
                                logger.warning(f"Could not link attachment to DriveItem: {link_result.get('message')}")
                        except Exception as link_error:
                            logger.warning(f"Error linking attachment to DriveItem: {link_error}")
                    
                except Exception as e:
                    logger.exception(f"Error processing line item {line_item.id}")
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Unexpected error: {str(e)}"
                    })
            
            success = synced_count > 0 or len(errors) == 0
            message = f"Uploaded {synced_count} file(s) to SharePoint"
            if errors:
                message += f" with {len(errors)} error(s)"
            
            return {
                "success": success,
                "message": message,
                "synced_count": synced_count,
                "errors": errors
            }
            
        except Exception as e:
            logger.exception(f"Error uploading attachments for project {project_id}")
            return {
                "success": False,
                "message": f"Error: {str(e)}",
                "synced_count": 0,
                "errors": [{"error": str(e)}]
            }
