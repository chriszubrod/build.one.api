# Python Standard Library Imports
import logging
import re
from typing import Any, Dict, List, Optional
from collections import defaultdict

# Third-party Imports

# Local Imports
from entities.expense.business.service import ExpenseService
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
from entities.project.business.service import ProjectService
from entities.vendor.business.service import VendorService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.module.business.service import ModuleService
from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)


class ExpenseCompleteService:
    """
    Service for orchestrating the complete expense process:
    1. Finalize Expense and ExpenseLineItems
    2. Upload attachments to SharePoint module folders
    """

    def __init__(self):
        """Initialize the ExpenseCompleteService."""
        self.expense_service = ExpenseService()
        self.expense_line_item_service = ExpenseLineItemService()
        self.expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
        self.attachment_service = AttachmentService()
        self.project_service = ProjectService()
        self.vendor_service = VendorService()
        self.sub_cost_code_service = SubCostCodeService()
        self.module_service = ModuleService()
        self.project_module_connector = DriveItemProjectModuleConnector()
        self.driveitem_service = MsDriveItemService()
        self.drive_repo = MsDriveRepository()

    def complete_expense(self, public_id: str) -> dict:
        """
        Complete an expense: finalize and upload attachments to module folders.
        
        Args:
            public_id: Public ID of the expense
            
        Returns:
            Dict with status_code, message, and detailed results for each step
        """
        # Step 1: Finalize Expense
        expense = self.expense_service.read_by_public_id(public_id=public_id)
        if not expense:
            return {
                "status_code": 404,
                "message": "Expense not found",
                "expense_finalized": False,
                "file_uploads": {},
                "errors": []
            }
        
        # Check if already finalized
        if not expense.is_draft:
            logger.info(f"Expense {public_id} is already finalized")
        
        # Finalize Expense (set is_draft=False)
        # Use retry logic to handle race conditions with auto-save
        try:
            import time
            
            finalized_expense = None
            max_retries = 3
            
            for attempt in range(max_retries):
                # Re-read expense to get latest row_version (handles auto-save race condition)
                expense = self.expense_service.read_by_public_id(public_id=public_id)
                if not expense:
                    return {
                        "status_code": 404,
                        "message": "Expense not found during finalization",
                        "expense_finalized": False,
                        "file_uploads": {},
                        "errors": []
                    }
                
                # Get vendor to get vendor_public_id
                vendor = None
                if expense.vendor_id:
                    vendor = self.vendor_service.read_by_id(id=expense.vendor_id)
                
                if not vendor or not vendor.public_id:
                    return {
                        "status_code": 400,
                        "message": "Vendor not found for expense",
                        "expense_finalized": False,
                        "file_uploads": {},
                        "errors": [{"step": "finalize_expense", "error": "Vendor not found"}]
                    }
                
                finalized_expense = self.expense_service.update_by_public_id(
                    public_id=public_id,
                    row_version=expense.row_version,
                    vendor_public_id=vendor.public_id,
                    expense_date=expense.expense_date,
                    reference_number=expense.reference_number,
                    total_amount=float(expense.total_amount) if expense.total_amount else None,
                    memo=expense.memo,
                    is_draft=False
                )
                
                if finalized_expense:
                    logger.info(f"Expense {public_id} finalized on attempt {attempt + 1}")
                    break
                else:
                    logger.warning(f"Expense {public_id} finalize attempt {attempt + 1} failed (row_version conflict?), retrying...")
                    if attempt < max_retries - 1:
                        time.sleep(0.2)  # Brief delay before retry
            
            if not finalized_expense:
                return {
                    "status_code": 500,
                    "message": "Failed to finalize expense after retries (concurrent modification)",
                    "expense_finalized": False,
                    "file_uploads": {},
                    "errors": [{"step": "finalize_expense", "error": "Row version conflict after retries"}]
                }
        except Exception as e:
            logger.exception(f"Error finalizing expense {public_id}")
            return {
                "status_code": 500,
                "message": f"Error finalizing expense: {str(e)}",
                "expense_finalized": False,
                "file_uploads": {},
                "errors": [{"step": "finalize_expense", "error": str(e)}]
            }
        
        # Step 2: Finalize all ExpenseLineItems
        line_items = self.expense_line_item_service.read_by_expense_id(expense_id=expense.id)
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
                    
                    self.expense_line_item_service.update_by_public_id(
                        public_id=line_item.public_id,
                        row_version=line_item.row_version,
                        expense_public_id=public_id,
                        sub_cost_code_id=line_item.sub_cost_code_id,
                        project_public_id=project_public_id,
                        description=line_item.description,
                        quantity=line_item.quantity,
                        rate=float(line_item.rate) if line_item.rate else None,
                        amount=float(line_item.amount) if line_item.amount else None,
                        is_billable=line_item.is_billable,
                        markup=float(line_item.markup) if line_item.markup else None,
                        price=float(line_item.price) if line_item.price else None,
                        is_draft=False
                    )
                except Exception as e:
                    logger.error(f"Error finalizing line item {line_item.id}: {e}")
                    line_item_errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": str(e)
                    })
        
        # Step 3: Upload attachments to module folders
        file_upload_results = {}
        all_errors = line_item_errors.copy()
        
        # Group line items by project_id
        line_items_by_project = defaultdict(list)
        line_items_without_project = []
        for line_item in line_items:
            if line_item.project_id:
                line_items_by_project[line_item.project_id].append(line_item)
            else:
                line_items_without_project.append(line_item)
        
        logger.info(f"Complete Expense {public_id}: {len(line_items)} line items total")
        logger.info(f"  - {len(line_items_by_project)} projects with line items")
        logger.info(f"  - {len(line_items_without_project)} line items without project (will skip SharePoint sync)")
        
        # Process each project
        for project_id, project_line_items in line_items_by_project.items():
            logger.info(f"Processing project {project_id} with {len(project_line_items)} line items")
            # Upload files to module folder
            upload_result = self._upload_attachments_to_module_folder(
                expense=expense,
                line_items=project_line_items,
                project_id=project_id
            )
            file_upload_results[project_id] = upload_result
            if upload_result.get("errors"):
                all_errors.extend(upload_result["errors"])
        
        # Determine overall status
        has_errors = len(all_errors) > 0
        status_code = 200 if not has_errors else 207  # 207 = Multi-Status (partial success)
        message = "Expense completed successfully"
        if has_errors:
            message += f" with {len(all_errors)} error(s)"
        
        return {
            "status_code": status_code,
            "message": message,
            "expense_finalized": True,
            "file_uploads": file_upload_results,
            "errors": all_errors
        }

    def _upload_attachments_to_module_folder(
        self,
        expense,
        line_items: List,
        project_id: int
    ) -> dict:
        """
        Upload attachments for line items to the project's module folder in SharePoint.
        Downloads from Azure Blob Storage and uploads to SharePoint with final filename.
        
        Returns:
            Dict with success, synced_count, and errors
        """
        try:
            # Get the Expenses module (try multiple names)
            module = self.module_service.read_by_name("Expenses")
            if not module:
                module = self.module_service.read_by_name("Expense")
            if not module:
                module = self.module_service.read_by_name("Bills")
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
            if expense.vendor_id:
                vendor = self.vendor_service.read_by_id(id=expense.vendor_id)
            
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
            
            logger.info(f"SharePoint sync: Processing {len(line_items)} line items for project {project_id}")
            
            # Process each line item
            for line_item in line_items:
                logger.info(f"  Checking line item {line_item.public_id}")
                try:
                    if not line_item.public_id:
                        continue
                    
                    # Get attachment link
                    attachment_link = self.expense_line_item_attachment_service.read_by_expense_line_item_id(
                        expense_line_item_public_id=line_item.public_id
                    )
                    
                    if not attachment_link or not attachment_link.attachment_id:
                        logger.info(f"    No attachment for line item {line_item.public_id}")
                        continue  # No attachment for this line item
                    
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
                    
                    # Generate SharePoint filename using final Expense/ExpenseLineItem values
                    project_identifier = project.abbreviation or project.name or ""
                    vendor_abbreviation = vendor.abbreviation or vendor.name or ""
                    reference_number = expense.reference_number or ""
                    description = line_item.description or ""
                    # Format price with $ and commas
                    price = ""
                    if line_item.price is not None:
                        try:
                            price_val = float(line_item.price)
                            price = f"${price_val:,.2f}"
                        except (ValueError, TypeError):
                            price = f"${line_item.price}"
                    # Format date as mm-dd-yyyy
                    expense_date = ""
                    if expense.expense_date:
                        try:
                            date_str = expense.expense_date[:10]
                            parts = date_str.split("-")
                            if len(parts) == 3:
                                expense_date = f"{parts[1]}-{parts[2]}-{parts[0]}"
                        except Exception:
                            expense_date = expense.expense_date[:10]
                    
                    filename_parts = [
                        project_identifier,
                        vendor_abbreviation,
                        reference_number,
                        description,
                        sub_cost_code_number,
                        price,
                        expense_date
                    ]
                    filename_parts = [part for part in filename_parts if part]
                    base_filename = " - ".join(filename_parts)
                    base_filename = re.sub(r'[<>:"/\\|?*]', '_', base_filename)
                    
                    # Get file extension
                    file_extension = attachment.file_extension or ""
                    if not file_extension and attachment.original_filename:
                        if '.' in attachment.original_filename:
                            file_extension = attachment.original_filename.rsplit('.', 1)[-1]
                    if not file_extension and attachment.content_type:
                        content_type_map = {
                            'application/pdf': 'pdf',
                            'image/jpeg': 'jpg',
                            'image/png': 'png',
                            'image/gif': 'gif',
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
