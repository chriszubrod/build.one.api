# Python Standard Library Imports
import logging
import re
import time
from collections import defaultdict
from decimal import Decimal
from typing import Any, List, Optional

# Third-party Imports

# Local Imports
from entities.expense.business.model import Expense
from entities.expense.persistence.repo import ExpenseRepository
from entities.attachment.business.service import AttachmentService
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
    append_excel_rows,
)

from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)


class ExpenseService:
    """
    Service for Expense entity business operations.
    Mirrors Bill entity structure for complete flow.
    """

    def __init__(self, repo: Optional[ExpenseRepository] = None):
        """Initialize the ExpenseService."""
        self.repo = repo or ExpenseRepository()
        self.attachment_service = AttachmentService()
        self._expense_line_item_service = None
        self._expense_line_item_attachment_service = None
        self.project_service = ProjectService()
        self.vendor_service = VendorService()
        self.sub_cost_code_service = SubCostCodeService()
        self.module_service = ModuleService()
        self._project_module_connector: Optional[Any] = None
        self._project_excel_connector: Optional[Any] = None
        self._driveitem_service: Optional[Any] = None
        self._drive_repo: Optional[Any] = None
        self._qbo_purchase_connector: Optional[Any] = None
        self._qbo_auth_service: Optional[Any] = None

    @property
    def expense_line_item_service(self):
        if self._expense_line_item_service is None:
            from entities.expense_line_item.business.service import ExpenseLineItemService
            self._expense_line_item_service = ExpenseLineItemService()
        return self._expense_line_item_service

    @property
    def expense_line_item_attachment_service(self):
        if self._expense_line_item_attachment_service is None:
            from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
            self._expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
        return self._expense_line_item_attachment_service

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
    def qbo_purchase_connector(self):
        if self._qbo_purchase_connector is None:
            from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
            self._qbo_purchase_connector = PurchaseExpenseConnector()
        return self._qbo_purchase_connector

    @property
    def qbo_auth_service(self):
        if self._qbo_auth_service is None:
            from integrations.intuit.qbo.auth.business.service import QboAuthService
            self._qbo_auth_service = QboAuthService()
        return self._qbo_auth_service

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
        Complete an expense: finalize, upload attachments to module folders, sync to Excel, push to QBO.
        Mirrors Bill complete_bill flow.
        """
        expense = self.read_by_public_id(public_id=public_id)
        if not expense:
            return {
                "status_code": 404,
                "message": "Expense not found",
                "expense_finalized": False,
                "file_uploads": {},
                "excel_syncs": {},
                "qbo_sync": {},
                "errors": [],
            }

        if not expense.is_draft:
            logger.info(f"Expense {public_id} is already finalized")

        # Step 1: Finalize Expense
        try:
            finalized_expense = None
            max_retries = 3
            for attempt in range(max_retries):
                expense = self.read_by_public_id(public_id=public_id)
                if not expense:
                    return {
                        "status_code": 404,
                        "message": "Expense not found during finalization",
                        "expense_finalized": False,
                        "file_uploads": {},
                        "excel_syncs": {},
                        "qbo_sync": {},
                        "errors": [],
                    }
                vendor = self.vendor_service.read_by_id(id=expense.vendor_id) if expense.vendor_id else None
                if not vendor or not vendor.public_id:
                    return {
                        "status_code": 400,
                        "message": "Vendor not found for expense",
                        "expense_finalized": False,
                        "file_uploads": {},
                        "excel_syncs": {},
                        "qbo_sync": {},
                        "errors": [{"step": "finalize_expense", "error": "Vendor not found"}],
                    }
                finalized_expense = self.update_by_public_id(
                    public_id=public_id,
                    row_version=expense.row_version,
                    vendor_public_id=vendor.public_id,
                    expense_date=expense.expense_date,
                    reference_number=expense.reference_number,
                    total_amount=float(expense.total_amount) if expense.total_amount else None,
                    memo=expense.memo,
                    is_draft=False,
                )
                if finalized_expense:
                    logger.info(f"Expense {public_id} finalized on attempt {attempt + 1}")
                    break
                if attempt < max_retries - 1:
                    time.sleep(0.2)
            if not finalized_expense:
                return {
                    "status_code": 500,
                    "message": "Failed to finalize expense after retries",
                    "expense_finalized": False,
                    "file_uploads": {},
                    "excel_syncs": {},
                    "qbo_sync": {},
                    "errors": [{"step": "finalize_expense", "error": "Row version conflict after retries"}],
                }
        except Exception as e:
            logger.exception(f"Error finalizing expense {public_id}")
            return {
                "status_code": 500,
                "message": str(e),
                "expense_finalized": False,
                "file_uploads": {},
                "excel_syncs": {},
                "qbo_sync": {},
                "errors": [{"step": "finalize_expense", "error": str(e)}],
            }

        # Step 2: Finalize all ExpenseLineItems
        line_items = self.expense_line_item_service.read_by_expense_id(expense_id=expense.id)
        line_item_errors = []
        for line_item in line_items:
            if line_item.is_draft:
                try:
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
                        is_draft=False,
                    )
                except Exception as e:
                    logger.error(f"Error finalizing line item {line_item.id}: {e}")
                    line_item_errors.append({"line_item_id": line_item.id, "line_item_public_id": line_item.public_id, "error": str(e)})

        # Step 3: Upload attachments and sync to Excel
        file_upload_results = {}
        excel_sync_results = {}
        all_errors = line_item_errors.copy()
        line_items_by_project = defaultdict(list)
        line_items_without_project = []
        for li in line_items:
            if li.project_id:
                line_items_by_project[li.project_id].append(li)
            else:
                line_items_without_project.append(li)

        logger.info(f"Expense {public_id} complete: {len(line_items_by_project)} projects with line items, {len(line_items_without_project)} line items without project (skipped)")
        for li in line_items_without_project:
            logger.warning(f"  Line item {li.public_id} has no project_id, skipping SharePoint/Excel sync")

        for project_id, project_line_items in line_items_by_project.items():
            upload_result = self._upload_attachments_to_module_folder(
                expense=expense,
                line_items=project_line_items,
                project_id=project_id,
                expense_line_items_count=len(line_items),
            )
            file_upload_results[project_id] = upload_result
            if upload_result.get("errors"):
                all_errors.extend(upload_result["errors"])
            excel_result = self._sync_to_excel_workbook(
                expense=expense,
                line_items=project_line_items,
                project_id=project_id,
            )
            excel_sync_results[project_id] = excel_result
            if excel_result.get("errors"):
                all_errors.extend(excel_result["errors"])

        # Step 4: QBO push disabled (no reverse sync at this time)
        qbo_sync_result = {"success": True, "message": "Skipped (reverse sync disabled)", "qbo_purchase_id": None, "errors": []}

        has_errors = len(all_errors) > 0
        return {
            "status_code": 200 if not has_errors else 207,
            "message": "Expense completed successfully" + (f" with {len(all_errors)} error(s)" if has_errors else ""),
            "expense_finalized": True,
            "file_uploads": file_upload_results,
            "excel_syncs": excel_sync_results,
            "qbo_sync": qbo_sync_result,
            "errors": all_errors,
        }

    def _sync_to_excel_workbook(self, expense, line_items: List, project_id: int) -> dict:
        """Sync Expense and ExpenseLineItems to project Excel workbook. Mirrors Bill _sync_to_excel_workbook."""
        try:
            from entities.bill.business.service import find_insertion_row_for_subcostcode
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
            vendor = self.vendor_service.read_by_id(id=expense.vendor_id) if expense.vendor_id else None
            if not vendor:
                return {"success": False, "message": "Vendor not found", "synced_count": 0, "errors": [{"error": "Vendor not found"}]}
            worksheet_result = get_excel_used_range_values(drive_id=drive.drive_id, item_id=driveitem.item_id, worksheet_name=worksheet_name)
            worksheet_values = []
            if worksheet_result.get("status_code") == 200:
                worksheet_values = worksheet_result.get("range", {}).get("values", [])
            line_items_by_subcostcode = defaultdict(list)
            for li in line_items:
                line_items_by_subcostcode[li.sub_cost_code_id].append(li)
            errors = []
            synced_count = 0
            rows_to_append = []
            for sub_cost_code_id, subcostcode_line_items in line_items_by_subcostcode.items():
                sub_cost_code = self.sub_cost_code_service.read_by_id(id=str(sub_cost_code_id)) if sub_cost_code_id is not None else None
                sub_cost_code_number = (sub_cost_code.number or "") if sub_cost_code else ""
                cost_code_number = sub_cost_code_number.split(".")[0] if "." in sub_cost_code_number else sub_cost_code_number
                group_rows = []
                for line_item in subcostcode_line_items:
                    try:
                        expense_date = expense.expense_date[:10] if expense.expense_date else ""
                        vendor_name = vendor.name or ""
                        ref_number = expense.reference_number or ""
                        description = line_item.description or ""
                        price = float(line_item.price) if line_item.price is not None else 0.0
                        row = ["", cost_code_number, sub_cost_code_number, "", "", "", "", "", expense_date, vendor_name, ref_number, description, "Ck", price] + [""] * 12
                        group_rows.append(row)
                    except Exception as e:
                        errors.append({"line_item_id": line_item.id, "error": str(e)})
                if not group_rows:
                    continue
                insertion_row = find_insertion_row_for_subcostcode(worksheet_values, sub_cost_code_number) if sub_cost_code_number and worksheet_values else None
                if insertion_row:
                    insert_result = insert_excel_rows(drive_id=drive.drive_id, item_id=driveitem.item_id, worksheet_name=worksheet_name, row_index=insertion_row, values=group_rows)
                    if insert_result.get("status_code") in [200, 201]:
                        synced_count += len(group_rows)
                        for i, row in enumerate(group_rows):
                            worksheet_values.insert(insertion_row - 1 + i, row)
                    else:
                        rows_to_append.extend(group_rows)
                else:
                    rows_to_append.extend(group_rows)
            if rows_to_append:
                append_result = append_excel_rows(drive_id=drive.drive_id, item_id=driveitem.item_id, worksheet_name=worksheet_name, values=rows_to_append)
                if append_result.get("status_code") in [200, 201]:
                    synced_count += len(rows_to_append)
            return {"success": synced_count > 0 or not errors, "message": f"Synced {synced_count} row(s)", "synced_count": synced_count, "errors": errors}
        except Exception as e:
            logger.exception(f"Error syncing to Excel for project {project_id}")
            return {"success": False, "message": str(e), "synced_count": 0, "errors": [{"error": str(e)}]}

    def _upload_attachments_to_module_folder(
        self, expense, line_items: List, project_id: int, expense_line_items_count: int = 1
    ) -> dict:
        """Upload attachments to SharePoint module folder. Mirrors Bill _upload_attachments_to_module_folder."""
        try:
            module = self.module_service.read_by_name("Expenses") or self.module_service.read_by_name("Expense") or self.module_service.read_by_name("Bills")
            if not module:
                module = (self.module_service.read_all() or [None])[0]
            if not module:
                return {"success": False, "message": "No modules found", "synced_count": 0, "errors": [{"error": "No modules found"}]}
            module_folder = self.project_module_connector.get_folder_for_module(project_id=project_id, module_id=int(module.id))
            if not module_folder:
                return {"success": False, "message": f"Module folder not linked for project {project_id}", "synced_count": 0, "errors": [{"error": f"Module folder not linked for project {project_id}"}]}
            folder_ms_drive_id = module_folder.get("ms_drive_id")
            folder_item_id = module_folder.get("item_id")
            if not folder_ms_drive_id or not folder_item_id:
                return {"success": False, "message": "Module folder missing drive or item_id", "synced_count": 0, "errors": [{"error": "Module folder missing drive or item_id"}]}
            drive = self.drive_repo.read_by_id(folder_ms_drive_id)
            if not drive:
                return {"success": False, "message": "Drive not found", "synced_count": 0, "errors": [{"error": "Drive not found"}]}
            vendor = self.vendor_service.read_by_id(id=expense.vendor_id) if expense.vendor_id else None
            if not vendor:
                return {"success": False, "message": "Vendor not found", "synced_count": 0, "errors": [{"error": "Vendor not found"}]}
            project = self.project_service.read_by_id(id=str(project_id))
            if not project:
                return {"success": False, "message": f"Project {project_id} not found", "synced_count": 0, "errors": [{"error": f"Project {project_id} not found"}]}
            try:
                storage = AzureBlobStorage()
            except Exception as e:
                return {"success": False, "message": str(e), "synced_count": 0, "errors": [{"error": str(e)}]}
            synced_count = 0
            errors = []
            uploaded_attachments = {}
            for line_item in line_items:
                try:
                    if not line_item.public_id:
                        continue
                    attachment_link = self.expense_line_item_attachment_service.read_by_expense_line_item_id(expense_line_item_public_id=line_item.public_id)
                    if not attachment_link or not attachment_link.attachment_id:
                        continue
                    if attachment_link.attachment_id in uploaded_attachments:
                        synced_count += 1
                        continue
                    attachment = self.attachment_service.read_by_id(id=attachment_link.attachment_id)
                    if not attachment or not attachment.blob_url:
                        errors.append({"line_item_id": line_item.id, "line_item_public_id": line_item.public_id, "error": "Attachment not found or missing blob_url"})
                        continue
                    sub_cost_code_number = ""
                    if line_item.sub_cost_code_id:
                        scc = self.sub_cost_code_service.read_by_id(id=str(line_item.sub_cost_code_id))
                        if scc:
                            sub_cost_code_number = scc.number or ""
                    project_identifier = project.abbreviation or project.name or ""
                    vendor_abbreviation = vendor.abbreviation or vendor.name or ""
                    ref_number = expense.reference_number or ""
                    description = line_item.description or ""
                    price = f"${float(line_item.price):,.2f}" if line_item.price is not None else ""
                    expense_date = ""
                    if expense.expense_date:
                        parts = expense.expense_date[:10].split("-")
                        expense_date = f"{parts[1]}-{parts[2]}-{parts[0]}" if len(parts) == 3 else expense.expense_date[:10]
                    if expense_line_items_count > 1:
                        amount_str = f"${float(expense.total_amount):,.2f}" if expense.total_amount is not None else ""
                        filename_parts = [project_identifier, vendor_abbreviation, ref_number, "Multiple See Image", amount_str, expense_date]
                    else:
                        filename_parts = [project_identifier, vendor_abbreviation, ref_number, description, sub_cost_code_number, price, expense_date]
                    base_filename = re.sub(r'[<>:"/\\|?*]', '_', " - ".join(p for p in filename_parts if p))
                    file_extension = attachment.file_extension or ""
                    if not file_extension and attachment.original_filename and "." in attachment.original_filename:
                        file_extension = attachment.original_filename.rsplit(".", 1)[-1]
                    if not file_extension and attachment.content_type:
                        file_extension = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}.get(attachment.content_type, "")
                    if file_extension and not file_extension.startswith("."):
                        file_extension = "." + file_extension
                    sharepoint_filename = base_filename + file_extension
                    try:
                        file_content, metadata = storage.download_file(attachment.blob_url)
                    except Exception as e:
                        errors.append({"line_item_id": line_item.id, "error": str(e)})
                        continue
                    content_type = attachment.content_type or metadata.get("content_type", "application/octet-stream")
                    upload_result = self.driveitem_service.upload_file(drive_public_id=drive.public_id, parent_item_id=folder_item_id, filename=sharepoint_filename, content=file_content, content_type=content_type)
                    if upload_result.get("status_code") not in [200, 201]:
                        errors.append({"line_item_id": line_item.id, "error": upload_result.get("message", "Unknown error")})
                        continue
                    uploaded_attachments[attachment_link.attachment_id] = sharepoint_filename
                    synced_count += 1
                except Exception as e:
                    errors.append({"line_item_id": line_item.id, "error": str(e)})
            return {"success": synced_count > 0 or not errors, "message": f"Uploaded {synced_count} file(s)", "synced_count": synced_count, "errors": errors}
        except Exception as e:
            logger.exception(f"Error uploading attachments for project {project_id}")
            return {"success": False, "message": str(e), "synced_count": 0, "errors": [{"error": str(e)}]}
