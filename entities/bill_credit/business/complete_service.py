# Python Standard Library Imports
import logging
import re
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional
from collections import defaultdict

# Third-party Imports

# Local Imports
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.bill_credit_line_item_attachment.business.service import BillCreditLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
from entities.project.business.service import ProjectService
from entities.vendor.business.service import VendorService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.module.business.service import ModuleService
from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import DriveItemProjectExcelConnector
from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
from integrations.ms.sharepoint.external.client import (
    get_excel_used_range_values,
    insert_excel_rows,
    append_excel_rows,
    create_workbook_session,
    close_workbook_session,
)
from entities.bill.business.service import find_insertion_row_for_subcostcode
from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)


class BillCreditCompleteService:
    """
    Service for orchestrating the complete bill credit process:
    1. Finalize BillCredit and BillCreditLineItems
    2. Upload attachments to SharePoint module folders
    """

    def __init__(self):
        """Initialize the BillCreditCompleteService."""
        self.bill_credit_service = BillCreditService()
        self.bill_credit_line_item_service = BillCreditLineItemService()
        self.bill_credit_line_item_attachment_service = BillCreditLineItemAttachmentService()
        self.attachment_service = AttachmentService()
        self.project_service = ProjectService()
        self.vendor_service = VendorService()
        self.sub_cost_code_service = SubCostCodeService()
        self.module_service = ModuleService()
        self.project_excel_connector = DriveItemProjectExcelConnector()
        self.project_module_connector = DriveItemProjectModuleConnector()
        self.driveitem_service = MsDriveItemService()
        self.drive_repo = MsDriveRepository()

    def complete_bill_credit(self, public_id: str) -> dict:
        """
        Complete a bill credit: finalize and upload attachments to module folders.
        
        Args:
            public_id: Public ID of the bill credit
            
        Returns:
            Dict with status_code, message, and detailed results for each step
        """
        # Step 1: Finalize BillCredit
        bill_credit = self.bill_credit_service.read_by_public_id(public_id=public_id)
        if not bill_credit:
            return {
                "status_code": 404,
                "message": "Bill credit not found",
                "bill_credit_finalized": False,
                "file_uploads": {},
                "errors": []
            }
        
        # Check if already finalized
        if not bill_credit.is_draft:
            logger.info(f"BillCredit {public_id} is already finalized")
        
        # Finalize BillCredit (set is_draft=False)
        # Use retry logic to handle race conditions with auto-save
        try:
            from entities.bill_credit.api.schemas import BillCreditUpdate
            
            finalized_bill_credit = None
            max_retries = 3
            
            for attempt in range(max_retries):
                # Re-read bill credit to get latest row_version (handles auto-save race condition)
                bill_credit = self.bill_credit_service.read_by_public_id(public_id=public_id)
                if not bill_credit:
                    return {
                        "status_code": 404,
                        "message": "Bill credit not found during finalization",
                        "bill_credit_finalized": False,
                        "file_uploads": {},
                        "errors": []
                    }
                
                # Get vendor to get vendor_public_id
                vendor = None
                if bill_credit.vendor_id:
                    vendor = self.vendor_service.read_by_id(id=bill_credit.vendor_id)
                
                if not vendor or not vendor.public_id:
                    return {
                        "status_code": 400,
                        "message": "Vendor not found for bill credit",
                        "bill_credit_finalized": False,
                        "file_uploads": {},
                        "errors": [{"step": "finalize_bill_credit", "error": "Vendor not found"}]
                    }
                
                finalized_bill_credit = self.bill_credit_service.update_by_public_id(
                    public_id=public_id,
                    row_version=bill_credit.row_version,
                    vendor_public_id=vendor.public_id,
                    credit_date=bill_credit.credit_date,
                    credit_number=bill_credit.credit_number,
                    total_amount=Decimal(str(bill_credit.total_amount)) if bill_credit.total_amount else None,
                    memo=bill_credit.memo,
                    is_draft=False
                )
                
                if finalized_bill_credit:
                    logger.info(f"BillCredit {public_id} finalized on attempt {attempt + 1}")
                    break
                else:
                    logger.warning(f"BillCredit {public_id} finalize attempt {attempt + 1} failed (row_version conflict?), retrying...")
                    if attempt < max_retries - 1:
                        time.sleep(0.2)  # Brief delay before retry
            
            if not finalized_bill_credit:
                return {
                    "status_code": 500,
                    "message": "Failed to finalize bill credit after retries (concurrent modification)",
                    "bill_credit_finalized": False,
                    "file_uploads": {},
                    "errors": [{"step": "finalize_bill_credit", "error": "Row version conflict after retries"}]
                }
        except Exception as e:
            logger.exception(f"Error finalizing bill credit {public_id}")
            return {
                "status_code": 500,
                "message": f"Error finalizing bill credit: {str(e)}",
                "bill_credit_finalized": False,
                "file_uploads": {},
                "errors": [{"step": "finalize_bill_credit", "error": str(e)}]
            }
        
        # Step 2: Finalize all BillCreditLineItems
        line_items = self.bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id=bill_credit.id)
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
                    
                    self.bill_credit_line_item_service.update_by_public_id(
                        public_id=line_item.public_id,
                        row_version=line_item.row_version,
                        bill_credit_public_id=public_id,
                        sub_cost_code_id=line_item.sub_cost_code_id,
                        project_public_id=project_public_id,
                        description=line_item.description,
                        quantity=Decimal(str(line_item.quantity)) if line_item.quantity is not None else None,
                        unit_price=Decimal(str(line_item.unit_price)) if line_item.unit_price is not None else None,
                        amount=Decimal(str(line_item.amount)) if line_item.amount is not None else None,
                        is_billable=line_item.is_billable,
                        billable_amount=Decimal(str(line_item.billable_amount)) if line_item.billable_amount is not None else None,
                        is_draft=False,
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
        
        print(f"\n--- Complete BillCredit {public_id} ---")
        print(f"  Total line items: {len(line_items)}")
        print(f"  Projects with line items: {len(line_items_by_project)}")
        print(f"  Line items without project (skipped): {len(line_items_without_project)}")
        
        logger.info(f"Complete BillCredit {public_id}: {len(line_items)} line items total")
        logger.info(f"  - {len(line_items_by_project)} projects with line items")
        logger.info(f"  - {len(line_items_without_project)} line items without project (will skip SharePoint sync)")
        
        if len(line_items_without_project) > 0:
            for li in line_items_without_project:
                logger.warning(f"  - Line item {li.public_id} has no project_id, skipping SharePoint sync")
        
        # Process each project
        excel_sync_results = {}
        for project_id, project_line_items in line_items_by_project.items():
            print(f"\n  Processing project {project_id} with {len(project_line_items)} line items")
            logger.info(f"Processing project {project_id} with {len(project_line_items)} line items")

            # Upload files to module folder
            upload_result = self._upload_attachments_to_module_folder(
                bill_credit=bill_credit,
                line_items=project_line_items,
                project_id=project_id
            )
            file_upload_results[project_id] = upload_result
            if upload_result.get("errors"):
                all_errors.extend(upload_result["errors"])

            # Excel workbook sync disabled (MS Graph path). The Box Excel mirror
            # below is independent of this — Box DETAILS-tab updates are driven
            # by the Box workbook mapping, not the MS Excel link.
            excel_result = {"success": True, "message": "Disabled", "synced_count": 0, "errors": []}
            excel_sync_results[project_id] = excel_result
            if excel_result.get("errors"):
                all_errors.extend(excel_result["errors"])

            # Box Excel mirror — enqueue a DETAILS-tab update for this project's
            # mapped Box workbook, alongside the (disabled) MS Excel sync.
            # Additive + failure-isolated: never affects the completion result.
            self._enqueue_box_excel(bill_credit=bill_credit, project_id=project_id)

        # Box mirror — enqueue line-item attachments to each mapped
        # project's Box folder. Additive + failure-isolated: never affects
        # the completion result.
        self._enqueue_box_uploads(bill_credit=bill_credit, line_items=line_items)

        # Determine overall status
        has_errors = len(all_errors) > 0
        status_code = 200 if not has_errors else 207  # 207 = Multi-Status (partial success)
        message = "Bill credit completed successfully"
        if has_errors:
            message += f" with {len(all_errors)} error(s)"

        return {
            "status_code": status_code,
            "message": message,
            "bill_credit_finalized": True,
            "file_uploads": file_upload_results,
            "excel_syncs": excel_sync_results,
            "errors": all_errors
        }

    def _enqueue_box_uploads(self, bill_credit, line_items: List) -> None:
        """
        Enqueue Box uploads for the bill credit's line-item attachments.

        Mirrors the SharePoint module-folder upload: one enqueue per unique
        (project, attachment) pair, routed to the project's mapped Box
        folder. Projects without a Box mapping are skipped (one info log
        per project). Additive + failure-isolated — any exception is logged
        and swallowed so Box can never affect the completion flow.
        """
        import os as _os
        if _os.getenv("ALLOW_BOX_WRITES", "").strip().lower() != "true":
            return  # gate closed — skip the DB legwork, not just the enqueue
        try:
            from integrations.box.folder.business.service import (
                BoxProjectFolderService,
                DOC_CLASS_INVOICES,
            )
            from integrations.box.outbox.business.service import BoxOutboxService

            folder_service = BoxProjectFolderService()
            box_outbox = BoxOutboxService()
            mapping_cache = {}  # project_id -> mapping dict or None
            enqueued_keys = set()  # (project_id, attachment_id)

            for line_item in line_items:
                try:
                    if not line_item.project_id or not line_item.public_id:
                        continue
                    project_id = line_item.project_id
                    if project_id not in mapping_cache:
                        # Vendor credit-memo docs file to the project's
                        # "14 - Invoices" (AP/'invoices') folder.
                        mapping_cache[project_id] = folder_service.read_mapping_by_project_id_and_class(
                            project_id, DOC_CLASS_INVOICES
                        )
                        if mapping_cache[project_id] is None:
                            logger.info(f"box.enqueue.skipped_unmapped_project project_id={project_id} doc_class=invoices")
                    mapping = mapping_cache[project_id]
                    if not mapping:
                        continue
                    attachment_link = self.bill_credit_line_item_attachment_service.read_by_bill_credit_line_item_id(
                        bill_credit_line_item_public_id=line_item.public_id
                    )
                    if not attachment_link or not attachment_link.attachment_id:
                        continue
                    if (project_id, attachment_link.attachment_id) in enqueued_keys:
                        continue
                    attachment = self.attachment_service.read_by_id(id=attachment_link.attachment_id)
                    if not attachment or not attachment.blob_url:
                        continue
                    box_outbox.enqueue_box_upload(
                        entity_type="bill_credit",
                        entity_public_id=str(bill_credit.public_id),
                        doc_kind="attachment",
                        blob_path=attachment.blob_url,
                        filename=attachment.original_filename or attachment.filename or "document",
                        content_type=attachment.content_type or "application/octet-stream",
                        box_folder_id=mapping["box_folder_id"],
                        attachment_id=attachment.id,
                        project_id=project_id,
                    )
                    enqueued_keys.add((project_id, attachment_link.attachment_id))
                except Exception as line_error:
                    logger.warning(
                        f"box.enqueue.failed bill_credit={bill_credit.public_id} line_item={line_item.id}: {line_error}"
                    )
        except Exception as e:
            logger.warning(f"box.enqueue.failed bill_credit={bill_credit.public_id}: {e}")

    def _enqueue_box_excel(self, bill_credit, project_id: int) -> None:
        """
        Enqueue a Box DETAILS-tab Excel update for this bill credit's project.

        Sibling of _enqueue_box_uploads, called from the same completion flow
        right where sync_to_excel_workbook (the MS Excel sync) would run. Looks
        up the project's mapped Box workbook; if mapped, enqueues a single
        update_box_excel outbox row (the drain handler re-fetches the entity +
        rebuilds rows + edits the .xlsx with openpyxl). Idempotency is via
        column Z, so one row per (entity, workbook) is safe.

        Additive + failure-isolated — early-returns when ALLOW_BOX_WRITES is not
        'true' (skip the DB legwork) and swallows every exception so Box can
        never affect the completion flow.
        """
        import os as _os
        if _os.getenv("ALLOW_BOX_WRITES", "").strip().lower() != "true":
            return  # gate closed — skip the DB legwork, not just the enqueue
        try:
            from integrations.box.excel.business.mapping_service import (
                BoxProjectWorkbookService,
            )
            from integrations.box.outbox.business.service import BoxOutboxService

            mapping = BoxProjectWorkbookService().read_by_project_id(project_id)
            if not mapping:
                logger.info(f"box.excel.skipped_unmapped_project project_id={project_id}")
                return
            BoxOutboxService().enqueue_box_excel(
                entity_type="bill_credit",
                entity_public_id=str(bill_credit.public_id),
                project_id=project_id,
                box_file_id=mapping["box_file_id"],
                worksheet_name=mapping["worksheet_name"],
            )
        except Exception as e:
            logger.warning(f"box.excel.enqueue.failed bill_credit={bill_credit.public_id} project_id={project_id}: {e}")

    def _upload_attachments_to_module_folder(
        self,
        bill_credit,
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
            # Get the BillCredit module (try multiple names)
            module = self.module_service.read_by_name("Bill Credits")
            if not module:
                module = self.module_service.read_by_name("BillCredits")
            if not module:
                module = self.module_service.read_by_name("Vendor Credits")
            if not module:
                module = self.module_service.read_by_name("Credits")
            if not module:
                # Fall back to Bills module if no specific BillCredit module exists
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
            if bill_credit.vendor_id:
                vendor = self.vendor_service.read_by_id(id=bill_credit.vendor_id)
            
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
                    attachment_link = self.bill_credit_line_item_attachment_service.read_by_bill_credit_line_item_id(
                        bill_credit_line_item_public_id=line_item.public_id
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
                    
                    # Generate SharePoint filename using final BillCredit/BillCreditLineItem values
                    # Use Project.Abbreviation if available, otherwise Project.Name
                    project_identifier = project.abbreviation or project.name or ""
                    vendor_abbreviation = vendor.abbreviation or vendor.name or ""
                    credit_number = bill_credit.credit_number or ""
                    description = line_item.description or ""
                    # Format amount with $ and commas (e.g., $10,000.00)
                    amount_str = ""
                    if line_item.amount is not None:
                        try:
                            amount_val = Decimal(str(line_item.amount))
                            amount_str = f"${amount_val:,.2f}"
                        except (ValueError, TypeError):
                            amount_str = f"${line_item.amount}"
                    # Format date as mm-dd-yyyy
                    credit_date = ""
                    if bill_credit.credit_date:
                        try:
                            # bill_credit.credit_date is in format "YYYY-MM-DD..." - convert to mm-dd-yyyy
                            date_str = bill_credit.credit_date[:10]  # Get YYYY-MM-DD part
                            parts = date_str.split("-")
                            if len(parts) == 3:
                                credit_date = f"{parts[1]}-{parts[2]}-{parts[0]}"  # mm-dd-yyyy
                        except Exception:
                            credit_date = bill_credit.credit_date[:10]  # Fallback to original
                    
                    filename_parts = [
                        project_identifier,
                        vendor_abbreviation,
                        credit_number,
                        description,
                        sub_cost_code_number,
                        amount_str,
                        credit_date
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

    def sync_to_excel_workbook(
        self,
        bill_credit,
        line_items: List,
        project_id: int
    ) -> dict:
        """
        Sync BillCredit and BillCreditLineItem data to the project's Excel workbook.
        Inserts rows after the last matching SubCostCode entry, or appends at end if not found.

        Returns:
            Dict with success, synced_count, and errors
        """
        session_id = None
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
            if bill_credit.vendor_id:
                vendor = self.vendor_service.read_by_id(id=bill_credit.vendor_id)

            if not vendor:
                return {
                    "success": False,
                    "message": "Vendor not found",
                    "synced_count": 0,
                    "errors": [{"error": "Vendor not found"}]
                }

            session_id = create_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id)

            # Read current worksheet data to find insertion points
            logger.info(f"Reading worksheet '{worksheet_name}' to determine insertion points")

            worksheet_result = get_excel_used_range_values(
                drive_id=drive.drive_id,
                item_id=driveitem.item_id,
                worksheet_name=worksheet_name,
                session_id=session_id,
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
                line_items_by_subcostcode[line_item.sub_cost_code_id].append(line_item)

            errors = []
            synced_count = 0
            rows_to_append = []

            # Process each SubCostCode group
            for sub_cost_code_id, subcostcode_line_items in line_items_by_subcostcode.items():
                sub_cost_code = None
                sub_cost_code_number = ""
                cost_code_number = ""

                if sub_cost_code_id:
                    sub_cost_code = self.sub_cost_code_service.read_by_id(id=str(sub_cost_code_id))
                    if sub_cost_code:
                        sub_cost_code_number = sub_cost_code.number or ""
                        if "." in sub_cost_code_number:
                            cost_code_number = sub_cost_code_number.split(".")[0]
                        else:
                            cost_code_number = sub_cost_code_number

                # Build rows for this SubCostCode group
                # Row structure: A(empty), B(CostCode), C(SubCostCode), D-H(empty), I(Date), J(Vendor), K(CreditNum), L(Desc), M("Credit"), N(Amount)
                group_rows = []
                for line_item in subcostcode_line_items:
                    try:
                        credit_date = bill_credit.credit_date[:10] if bill_credit.credit_date else ""
                        vendor_name = vendor.name or ""
                        credit_number = bill_credit.credit_number or ""
                        description = line_item.description or ""
                        # Column N: float (Decimal is not JSON-serializable — the
                        # Graph insert threw on every credit row, so credits never
                        # reached DETAILS), and NEGATED: BillCreditLineItem.Amount
                        # is stored positive, but a credit reduces the draw — the
                        # DETAILS ledger row must carry the negative value or every
                        # invoice with a credit overstates its draw total (HA-04:
                        # +$411.36 overstatement across 2 credits).
                        amount = -float(line_item.amount) if line_item.amount is not None else 0.0

                        row = [
                            "",                   # A: Empty
                            cost_code_number,     # B: CostCode
                            sub_cost_code_number, # C: SubCostCode
                            "",                   # D: Empty
                            "",                   # E: Empty
                            "",                   # F: Empty
                            "",                   # G: Empty
                            "",                   # H: Empty
                            credit_date,          # I: Credit Date
                            vendor_name,          # J: Vendor
                            credit_number,        # K: Credit Number
                            description,          # L: Description
                            "Credit",             # M: "Credit"
                            amount,               # N: Amount
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
                            str(line_item.public_id) if line_item.public_id else ""  # Z: BillCreditLineItem public_id (reconciliation key)
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
                    insert_result = insert_excel_rows(
                        drive_id=drive.drive_id,
                        item_id=driveitem.item_id,
                        worksheet_name=worksheet_name,
                        row_index=insertion_row,
                        values=group_rows,
                        session_id=session_id,
                    )

                    if insert_result.get("status_code") in [200, 201]:
                        synced_count += len(group_rows)
                        logger.info(f"Inserted {len(group_rows)} row(s) at row {insertion_row}")
                        # Update in-memory worksheet_values so subsequent groups use correct positions
                        for i, row in enumerate(group_rows):
                            worksheet_values.insert(insertion_row - 1 + i, row)
                    else:
                        error_msg = insert_result.get("message", "Unknown error")
                        logger.error(f"Failed to insert rows: {error_msg}")
                        errors.append({
                            "sub_cost_code": sub_cost_code_number,
                            "error": f"Failed to insert rows: {error_msg}"
                        })
                        rows_to_append.extend(group_rows)
                else:
                    logger.info(f"SubCostCode {sub_cost_code_number or 'None'}: no match found, will append at end")
                    rows_to_append.extend(group_rows)

            # Append any rows that didn't have matching SubCostCodes
            if rows_to_append:
                logger.info(f"Appending {len(rows_to_append)} row(s) to end of worksheet")
                append_result = append_excel_rows(
                    drive_id=drive.drive_id,
                    item_id=driveitem.item_id,
                    worksheet_name=worksheet_name,
                    values=rows_to_append,
                    session_id=session_id,
                )

                if append_result.get("status_code") in [200, 201]:
                    synced_count += len(rows_to_append)
                    logger.info(f"Appended {len(rows_to_append)} row(s)")
                else:
                    error_msg = append_result.get("message", "Unknown error")
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
        finally:
            if session_id:
                close_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id, session_id=session_id)

    def sync_bill_credits_batch_to_excel(self, bill_credit_line_pairs: List[tuple], project_id: int) -> dict:
        """
        Batch sync line items from multiple bill credits to one project's Excel workbook.
        Single worksheet read + batched outbox insert per project, regardless of how
        many credits contribute line items. Used by the QBO pull sync script.

        Unlike the single-credit ``sync_to_excel_workbook`` (which writes Excel directly
        via insert_excel_rows), this batch path is OUTBOX-backed — matching the Bill /
        Expense pull-sync pattern (async drain, ALLOW_MS_WRITES-gated).

        Args:
            bill_credit_line_pairs: list of (bill_credit, [line_items]) tuples
            project_id: target project ID

        Returns:
            dict with success, synced_count, errors
        """
        session_id = None
        try:
            if not bill_credit_line_pairs:
                return {"success": True, "message": "No bill credits to sync", "synced_count": 0, "errors": []}

            # --- resolve workbook location (once) ---
            excel_mapping = self.project_excel_connector.get_excel_for_project(project_id=project_id)
            if not excel_mapping:
                return {"success": False, "message": f"Excel not linked for project {project_id}", "synced_count": 0, "errors": [{"error": f"Excel not linked for project {project_id}"}]}

            worksheet_name = excel_mapping.get("worksheet_name")
            if not worksheet_name:
                return {"success": False, "message": "Worksheet name not found", "synced_count": 0, "errors": [{"error": "Worksheet name not found"}]}

            driveitem_repo = MsDriveItemRepository()
            items = driveitem_repo.read_all()
            driveitem = next((item for item in items if item.id == excel_mapping.get("id")), None)
            if not driveitem:
                return {"success": False, "message": "DriveItem not found", "synced_count": 0, "errors": [{"error": "DriveItem not found"}]}

            drive = self.drive_repo.read_by_id(driveitem.ms_drive_id)
            if not drive:
                return {"success": False, "message": "Drive not found", "synced_count": 0, "errors": [{"error": "Drive not found"}]}

            # --- resolve vendors for all credits (cached) ---
            vendor_cache = {}
            for bill_credit, _ in bill_credit_line_pairs:
                if bill_credit.vendor_id and bill_credit.vendor_id not in vendor_cache:
                    vendor_cache[bill_credit.vendor_id] = self.vendor_service.read_by_id(id=bill_credit.vendor_id)
            scc_cache = {}  # memoize SubCostCode reads across all line items in this batch

            session_id = create_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id)

            # --- read worksheet once ---
            logger.info(f"Reading worksheet '{worksheet_name}' for project {project_id} (batch: {len(bill_credit_line_pairs)} credit(s))")
            worksheet_result = get_excel_used_range_values(
                drive_id=drive.drive_id,
                item_id=driveitem.item_id,
                worksheet_name=worksheet_name,
                session_id=session_id,
            )
            worksheet_values = []
            if worksheet_result.get("status_code") == 200:
                range_data = worksheet_result.get("range", {})
                worksheet_values = range_data.get("values", [])
                logger.info(f"Worksheet has {len(worksheet_values)} rows")
            else:
                logger.warning(f"Could not read worksheet data: {worksheet_result.get('message')}. Will append at end.")

            # --- idempotency: column Z across ALL line items ---
            existing_public_ids = set()
            for row in worksheet_values:
                if len(row) > 25:
                    val = row[25]
                    if val is not None and str(val).strip():
                        existing_public_ids.add(str(val).strip())

            # --- build rows for every (bill_credit, line_item) pair ---
            errors = []
            all_new_rows = []  # (scc_number, row)
            for bill_credit, line_items in bill_credit_line_pairs:
                vendor = vendor_cache.get(bill_credit.vendor_id)
                vendor_name = (vendor.name or "") if vendor else ""
                for li in line_items:
                    pid = str(li.public_id).strip() if li.public_id else ""
                    if pid and pid in existing_public_ids:
                        continue
                    try:
                        scc = None
                        if li.sub_cost_code_id is not None:
                            scc_key = str(li.sub_cost_code_id)
                            if scc_key not in scc_cache:
                                scc_cache[scc_key] = self.sub_cost_code_service.read_by_id(id=scc_key)
                            scc = scc_cache[scc_key]
                        scc_number = (scc.number or "") if scc else ""
                        cc_number = scc_number.split(".")[0] if "." in scc_number else scc_number
                        # Credits carry no Price field; N is the line Amount. float() to keep
                        # the value JSON-serializable for the outbox row (mirrors Bill/Expense).
                        n_value = float(li.amount) if li.amount is not None else 0
                        row = [
                            "",                                                          # A
                            cc_number,                                                   # B: CostCode
                            scc_number,                                                  # C: SubCostCode
                            "", "", "", "", "",                                          # D-H
                            bill_credit.credit_date[:10] if bill_credit.credit_date else "",  # I: Date
                            vendor_name,                                                 # J: Vendor
                            bill_credit.credit_number or "",                             # K: Credit #
                            li.description or "",                                         # L: Description
                            "Credit",                                                    # M: Type
                            n_value,                                                     # N: Amount
                            "", "", "", "", "", "", "", "", "", "", "",                   # O-Y
                            pid,                                                         # Z: Reconciliation key
                        ]
                        all_new_rows.append((scc_number, row))
                    except Exception as e:
                        logger.error(f"Error building Excel row for BillCreditLineItem {li.id}: {e}")
                        errors.append({"line_item_id": li.id, "error": str(e)})

            if not all_new_rows:
                logger.info(f"Project {project_id}: all bill credit line items already in worksheet, nothing to sync")
                return {"success": True, "message": "All rows already synced", "synced_count": 0, "errors": []}

            logger.info(f"Project {project_id}: {len(all_new_rows)} new credit row(s) to sync")

            # --- group by subcostcode, find insertion points ---
            groups_by_scc = defaultdict(list)
            for scc_number, row in all_new_rows:
                groups_by_scc[scc_number].append(row)

            synced_count = 0
            rows_to_append = []
            insert_groups = []
            for scc_number, group_rows in groups_by_scc.items():
                insertion_row = None
                if scc_number and worksheet_values:
                    insertion_row = find_insertion_row_for_subcostcode(
                        worksheet_values=worksheet_values,
                        target_subcostcode=scc_number,
                    )
                if insertion_row:
                    insert_groups.append((insertion_row, group_rows, scc_number))
                else:
                    rows_to_append.extend(group_rows)

            insert_groups.sort(key=lambda g: g[0])

            from integrations.ms.outbox.business.service import MsOutboxService
            ms_outbox = MsOutboxService()
            # EntityPublicId is a UNIQUEIDENTIFIER; a batch row spans many entities, so
            # derive a deterministic GUID from the project. (Plain str(project_id) fails
            # the nvarchar->uniqueidentifier cast in the outbox sproc.)
            import uuid as _uuid
            batch_entity_public_id = str(_uuid.uuid5(_uuid.NAMESPACE_URL, f"ms-excel-batch:{project_id}"))

            for insertion_row, group_rows, scc_number in insert_groups:
                queued = ms_outbox.enqueue_excel_insert(
                    entity_type="BillCreditBatch",
                    entity_public_id=batch_entity_public_id,
                    drive_id=drive.drive_id,
                    item_id=driveitem.item_id,
                    worksheet_name=worksheet_name,
                    row_index=insertion_row,
                    values=group_rows,
                    session_id=None,
                )
                if queued is not None:
                    synced_count += len(group_rows)
                    logger.info(
                        f"Queued batch Excel insert of {len(group_rows)} row(s) at row "
                        f"{insertion_row} for SubCostCode {scc_number} (outbox {queued.public_id})"
                    )
                else:
                    errors.append({
                        "sub_cost_code": scc_number,
                        "error": "Excel insert enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)"
                    })

            if rows_to_append:
                logger.info(f"Queuing batch append of {len(rows_to_append)} row(s) to end of worksheet")
                queued = ms_outbox.enqueue_excel_append(
                    entity_type="BillCreditBatch",
                    entity_public_id=batch_entity_public_id,
                    drive_id=drive.drive_id,
                    item_id=driveitem.item_id,
                    worksheet_name=worksheet_name,
                    values=rows_to_append,
                    session_id=None,
                )
                if queued is not None:
                    synced_count += len(rows_to_append)
                    logger.info(f"Queued batch Excel append of {len(rows_to_append)} row(s) (outbox {queued.public_id})")
                else:
                    errors.append({"error": "Excel append enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)"})

            logger.info(f"Project {project_id}: synced {synced_count} credit row(s) to Excel workbook")
            has_errors = len(errors) > 0
            return {
                "success": synced_count > 0 or not has_errors,
                "message": f"Synced {synced_count} row(s) to Excel workbook",
                "synced_count": synced_count,
                "errors": errors,
            }

        except Exception as e:
            logger.exception(f"Error batch-syncing bill credits to Excel workbook for project {project_id}")
            return {"success": False, "message": f"Error syncing to Excel: {str(e)}", "synced_count": 0, "errors": [{"error": str(e)}]}
        finally:
            if session_id:
                close_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id, session_id=session_id)
