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
    list_drive_root_children,
    list_drive_item_children,
    create_workbook_session,
    close_workbook_session,
)
from integrations.ms.sharepoint.drive.business.service import MsDriveService

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

    def create(self, *, tenant_id: int = 1, vendor_public_id: str, expense_date: str, reference_number: str, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True, is_credit: bool = False) -> Expense:
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
            is_credit=is_credit,
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
        is_credit: bool = None,
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
        if is_credit is not None:
            existing.is_credit = is_credit

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
                    total_amount=Decimal(str(expense.total_amount)) if expense.total_amount is not None else None,
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
                        rate=Decimal(str(line_item.rate)) if line_item.rate is not None else None,
                        amount=Decimal(str(line_item.amount)) if line_item.amount is not None else None,
                        is_billable=line_item.is_billable,
                        markup=Decimal(str(line_item.markup)) if line_item.markup is not None else None,
                        price=Decimal(str(line_item.price)) if line_item.price is not None else None,
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
            excel_result = self.sync_to_excel_workbook(expense=expense, line_items=project_line_items, project_id=project_id)
            excel_sync_results[project_id] = excel_result
            if excel_result.get("errors"):
                all_errors.extend(excel_result["errors"])

        # Step 3b: Upload to general receipts folder (520 - Current Receipts / yyyy / mm)
        receipts_upload_result = self._upload_to_general_receipts_folder(
            expense=expense,
            line_items=line_items,
        )
        if receipts_upload_result.get("errors"):
            all_errors.extend(receipts_upload_result["errors"])

        # Step 4: QBO push disabled (no reverse sync at this time)
        qbo_sync_result = {"success": True, "message": "Skipped (reverse sync disabled)", "qbo_purchase_id": None, "errors": []}

        has_errors = len(all_errors) > 0
        return {
            "status_code": 200 if not has_errors else 207,
            "message": "Expense completed successfully" + (f" with {len(all_errors)} error(s)" if has_errors else ""),
            "expense_finalized": True,
            "file_uploads": file_upload_results,
            "receipts_upload": receipts_upload_result,
            "excel_syncs": excel_sync_results,
            "qbo_sync": qbo_sync_result,
            "errors": all_errors,
        }

    def sync_to_excel_workbook(self, expense, line_items: List, project_id: int) -> dict:
        """
        Sync Expense and ExpenseLineItems to the project's Excel workbook.
        Mirrors BillService.sync_to_excel_workbook: idempotent via column Z
        public_id check, bottom-to-top insertion to avoid row-shift issues,
        and failed-insert → append fallback.
        """
        session_id = None
        try:
            from entities.bill.business.service import find_insertion_row_for_subcostcode

            # --- resolve workbook location ---
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

            session_id = create_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id)

            # --- read current worksheet ---
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

            # --- idempotency: collect existing public_ids from column Z (index 25) ---
            existing_public_ids = set()
            short_rows = 0
            for row in worksheet_values:
                if len(row) > 25:
                    val = row[25]
                    if val is not None and str(val).strip():
                        existing_public_ids.add(str(val).strip())
                else:
                    short_rows += 1
            if short_rows > 1 and not existing_public_ids:
                logger.warning(
                    f"Worksheet has {short_rows} row(s) with fewer than 26 columns. "
                    f"Column Z (reconciliation key) may not exist. Idempotency check may not prevent duplicates."
                )

            new_line_items = []
            for line_item in line_items:
                pid = str(line_item.public_id).strip() if line_item.public_id else ""
                if pid and pid in existing_public_ids:
                    logger.debug(f"ExpenseLineItem {pid} already in worksheet, skipping")
                else:
                    new_line_items.append(line_item)

            if not new_line_items:
                logger.info(f"All {len(line_items)} line item(s) already in worksheet, nothing to sync")
                return {"success": True, "message": f"All {len(line_items)} row(s) already synced", "synced_count": 0, "errors": []}

            if len(new_line_items) < len(line_items):
                logger.info(f"{len(line_items) - len(new_line_items)} line item(s) already in worksheet, syncing {len(new_line_items)} new")

            # --- group by SubCostCode, build rows, find insertion points ---
            # Row: A(empty), B(CostCode), C(SubCostCode), D-H(empty), I(Date),
            #      J(Vendor), K(RefNumber), L(Desc), M("Expense"), N(Price),
            #      O-Y(empty), Z(public_id)
            line_items_by_subcostcode = defaultdict(list)
            for li in new_line_items:
                line_items_by_subcostcode[li.sub_cost_code_id].append(li)

            errors = []
            synced_count = 0
            rows_to_append = []
            insert_groups = []  # (insertion_row, group_rows, sub_cost_code_number)

            for sub_cost_code_id, subcostcode_line_items in line_items_by_subcostcode.items():
                sub_cost_code = self.sub_cost_code_service.read_by_id(id=str(sub_cost_code_id)) if sub_cost_code_id is not None else None
                sub_cost_code_number = (sub_cost_code.number or "") if sub_cost_code else ""
                cost_code_number = sub_cost_code_number.split(".")[0] if "." in sub_cost_code_number else sub_cost_code_number

                group_rows = []
                for line_item in subcostcode_line_items:
                    try:
                        row = [
                            "",                                                                     # A: Empty
                            cost_code_number,                                                       # B: CostCode
                            sub_cost_code_number,                                                   # C: SubCostCode
                            "", "", "", "", "",                                                     # D-H: Empty
                            expense.expense_date[:10] if expense.expense_date else "",              # I: Expense Date
                            vendor.name or "",                                                      # J: Vendor
                            expense.reference_number or "",                                         # K: Reference Number
                            line_item.description or "",                                            # L: Description
                            "Expense Credit" if expense.is_credit else "Expense",                   # M: Type
                            float(line_item.price) if line_item.price is not None else 0,           # N: Price (numeric)
                            "", "", "", "", "", "", "", "", "", "", "",                              # O-Y: Empty
                            str(line_item.public_id) if line_item.public_id else "",                # Z: Reconciliation key
                        ]
                        group_rows.append(row)
                    except Exception as e:
                        logger.error(f"Error building Excel row for line item {line_item.id}: {e}")
                        errors.append({"line_item_id": line_item.id, "line_item_public_id": line_item.public_id, "error": str(e)})

                if not group_rows:
                    continue

                insertion_row = None
                if sub_cost_code_number and worksheet_values:
                    insertion_row = find_insertion_row_for_subcostcode(
                        worksheet_values=worksheet_values,
                        target_subcostcode=sub_cost_code_number,
                    )

                if insertion_row:
                    insert_groups.append((insertion_row, group_rows, sub_cost_code_number))
                else:
                    logger.info(f"SubCostCode {sub_cost_code_number or 'None'}: no match found, will append at end")
                    rows_to_append.extend(group_rows)

            # --- insert bottom-to-top so earlier inserts don't shift later positions ---
            insert_groups.sort(key=lambda g: (g[0], g[2]), reverse=True)

            for insertion_row, group_rows, sub_cost_code_number in insert_groups:
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
                    logger.info(f"Inserted {len(group_rows)} row(s) at row {insertion_row} for SubCostCode {sub_cost_code_number}")
                else:
                    error_msg = insert_result.get("message", "Unknown error")
                    logger.error(f"Failed to insert rows for SubCostCode {sub_cost_code_number}: {error_msg}")
                    errors.append({"sub_cost_code": sub_cost_code_number, "error": f"Failed to insert rows: {error_msg}"})
                    rows_to_append.extend(group_rows)

            # --- append remainder ---
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
                return {"success": True, "message": "No rows to sync", "synced_count": 0, "errors": []}

            logger.info(f"Successfully synced {synced_count} row(s) to Excel workbook")
            has_errors = len(errors) > 0
            return {
                "success": synced_count > 0 or not has_errors,
                "message": f"Synced {synced_count} row(s) to Excel workbook",
                "synced_count": synced_count,
                "errors": errors,
            }

        except Exception as e:
            logger.exception(f"Error syncing to Excel workbook for project {project_id}")
            return {"success": False, "message": f"Error syncing to Excel: {str(e)}", "synced_count": 0, "errors": [{"error": str(e)}]}
        finally:
            if session_id:
                close_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id, session_id=session_id)

    def sync_expenses_batch_to_excel(self, expense_line_pairs: List[tuple], project_id: int) -> dict:
        """
        Batch sync line items from multiple expenses to one project's Excel workbook.
        Single worksheet read + single batch insert per project, regardless of how
        many expenses contribute line items.  Used by the QBO pull sync script.

        The single-expense ``sync_to_excel_workbook`` remains for ``complete_expense``.

        Args:
            expense_line_pairs: list of (expense, [line_items]) tuples
            project_id: target project ID

        Returns:
            dict with success, synced_count, errors
        """
        session_id = None
        try:
            from entities.bill.business.service import find_insertion_row_for_subcostcode

            if not expense_line_pairs:
                return {"success": True, "message": "No expenses to sync", "synced_count": 0, "errors": []}

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

            # --- resolve vendors for all expenses (cached) ---
            vendor_cache = {}
            for expense, _ in expense_line_pairs:
                if expense.vendor_id and expense.vendor_id not in vendor_cache:
                    vendor_cache[expense.vendor_id] = self.vendor_service.read_by_id(id=expense.vendor_id)

            session_id = create_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id)

            # --- read worksheet once ---
            logger.info(f"Reading worksheet '{worksheet_name}' for project {project_id} (batch: {len(expense_line_pairs)} expense(s))")
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

            # --- build rows for every (expense, line_item) pair ---
            errors = []
            all_new_rows = []  # (sub_cost_code_id, row)
            for expense, line_items in expense_line_pairs:
                vendor = vendor_cache.get(expense.vendor_id)
                vendor_name = (vendor.name or "") if vendor else ""
                for li in line_items:
                    pid = str(li.public_id).strip() if li.public_id else ""
                    if pid and pid in existing_public_ids:
                        continue
                    try:
                        scc = self.sub_cost_code_service.read_by_id(id=str(li.sub_cost_code_id)) if li.sub_cost_code_id is not None else None
                        scc_number = (scc.number or "") if scc else ""
                        cc_number = scc_number.split(".")[0] if "." in scc_number else scc_number
                        row = [
                            "",                                                                 # A
                            cc_number,                                                          # B: CostCode
                            scc_number,                                                         # C: SubCostCode
                            "", "", "", "", "",                                                 # D-H
                            expense.expense_date[:10] if expense.expense_date else "",          # I: Date
                            vendor_name,                                                        # J: Vendor
                            expense.reference_number or "",                                     # K: Ref#
                            li.description or "",                                               # L: Description
                            "Expense Credit" if expense.is_credit else "Expense",               # M: Type
                            float(li.price) if li.price is not None else 0,                     # N: Price
                            "", "", "", "", "", "", "", "", "", "", "",                          # O-Y
                            pid,                                                                # Z: Reconciliation key
                        ]
                        all_new_rows.append((li.sub_cost_code_id, scc_number, cc_number, row))
                    except Exception as e:
                        logger.error(f"Error building Excel row for ExpenseLineItem {li.id}: {e}")
                        errors.append({"line_item_id": li.id, "error": str(e)})

            if not all_new_rows:
                logger.info(f"Project {project_id}: all line items already in worksheet, nothing to sync")
                return {"success": True, "message": "All rows already synced", "synced_count": 0, "errors": []}

            logger.info(f"Project {project_id}: {len(all_new_rows)} new row(s) to sync")

            # --- group by subcostcode, find insertion points ---
            from collections import defaultdict as _defaultdict
            groups_by_scc = _defaultdict(list)
            for sub_cost_code_id, scc_number, cc_number, row in all_new_rows:
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

            # --- insert bottom-to-top ---
            insert_groups.sort(key=lambda g: (g[0], g[2]), reverse=True)

            for insertion_row, group_rows, scc_number in insert_groups:
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
                    logger.info(f"Inserted {len(group_rows)} row(s) at row {insertion_row} for SubCostCode {scc_number}")
                else:
                    error_msg = insert_result.get("message", "Unknown error")
                    logger.error(f"Failed to insert rows for SubCostCode {scc_number}: {error_msg}")
                    errors.append({"sub_cost_code": scc_number, "error": f"Failed to insert rows: {error_msg}"})
                    rows_to_append.extend(group_rows)

            # --- append remainder ---
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

            logger.info(f"Project {project_id}: synced {synced_count} row(s) to Excel workbook")
            has_errors = len(errors) > 0
            return {
                "success": synced_count > 0 or not has_errors,
                "message": f"Synced {synced_count} row(s) to Excel workbook",
                "synced_count": synced_count,
                "errors": errors,
            }

        except Exception as e:
            logger.exception(f"Error batch-syncing to Excel workbook for project {project_id}")
            return {"success": False, "message": f"Error syncing to Excel: {str(e)}", "synced_count": 0, "errors": [{"error": str(e)}]}
        finally:
            if session_id:
                close_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id, session_id=session_id)

    def _upload_attachments_to_module_folder(
        self, expense, line_items: List, project_id: int, expense_line_items_count: int = 1
    ) -> dict:
        """Upload attachments to SharePoint module folder. Mirrors Bill _upload_attachments_to_module_folder."""
        try:
            module = self.module_service.read_by_name("Expenses") or self.module_service.read_by_name("Expense")
            if not module:
                return {"success": False, "message": "Expense module not found — ensure a module named 'Expenses' exists", "synced_count": 0, "errors": [{"error": "Expense module not found"}]}
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
                    exp_prefix = "EXP-CR" if expense.is_credit else "EXP"
                    if expense_line_items_count > 1:
                        amount_str = f"${float(expense.total_amount):,.2f}" if expense.total_amount is not None else ""
                        filename_parts = [exp_prefix, project_identifier, vendor_abbreviation, ref_number, "Multiple See Image", amount_str, expense_date]
                    else:
                        filename_parts = [exp_prefix, project_identifier, vendor_abbreviation, ref_number, description, sub_cost_code_number, price, expense_date]
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
            return {"success": not errors, "message": f"Uploaded {synced_count} file(s)", "synced_count": synced_count, "errors": errors}
        except Exception as e:
            logger.exception(f"Error uploading attachments for project {project_id}")
            return {"success": False, "message": str(e), "synced_count": 0, "errors": [{"error": str(e)}]}

    # -------------------------------------------------------------------------
    # General Receipts Folder — 520 - Current Receipts / yyyy / mm
    # -------------------------------------------------------------------------

    # Path segments from the SharePoint "Shared Documents" library root
    _RECEIPTS_FOLDER_PATH = [
        "General",
        "999 - Accounting",
        "01 - Banking & Credit Cards",
        "520 - Current Receipts",
    ]

    def _navigate_to_folder(self, drive_id: str, folder_path: list[str]) -> Optional[str]:
        """
        Navigate from the drive root through a list of folder names, returning
        the item_id of the final folder. Returns None if any segment is missing.
        """
        result = list_drive_root_children(drive_id)
        if result.get("status_code") != 200:
            logger.error(f"Could not list drive root: {result.get('message')}")
            return None

        current_items = result.get("items", [])
        current_item_id = None

        for segment in folder_path:
            match = next(
                (item for item in current_items if item.get("name") == segment and item.get("item_type") == "folder"),
                None,
            )
            if not match:
                logger.error(f"Folder segment '{segment}' not found in SharePoint")
                return None
            current_item_id = match["item_id"]
            child_result = list_drive_item_children(drive_id, current_item_id)
            if child_result.get("status_code") != 200:
                logger.error(f"Could not list children of '{segment}': {child_result.get('message')}")
                return None
            current_items = child_result.get("items", [])

        return current_item_id

    def _get_or_create_subfolder(self, drive_id: str, drive_public_id: str, parent_item_id: str, folder_name: str) -> Optional[str]:
        """
        Find a subfolder by name under parent_item_id. Create it if missing.
        Returns the item_id of the subfolder, or None on failure.
        """
        children = list_drive_item_children(drive_id, parent_item_id)
        if children.get("status_code") == 200:
            for child in children.get("items", []):
                if child.get("name") == folder_name and child.get("item_type") == "folder":
                    return child["item_id"]

        create_result = self.driveitem_service.create_folder(
            drive_public_id=drive_public_id,
            parent_item_id=parent_item_id,
            folder_name=folder_name,
        )
        if create_result.get("status_code") in [200, 201]:
            logger.info(f"Created subfolder '{folder_name}'")
            return create_result["item"]["id"]
        else:
            logger.error(f"Could not create subfolder '{folder_name}': {create_result.get('message')}")
            return None

    def _upload_to_general_receipts_folder(self, expense, line_items: List) -> dict:
        """
        Upload expense attachments to the general receipts folder:
        520 - Current Receipts / {yyyy} / {mm} / filename.ext

        Uses the same EXP filename convention as the project module folder upload.
        """
        try:
            if not expense.expense_date:
                return {"success": False, "message": "No expense date — cannot determine receipts folder", "synced_count": 0, "errors": []}

            date_parts = expense.expense_date[:10].split("-")
            if len(date_parts) != 3:
                return {"success": False, "message": f"Invalid expense date format: {expense.expense_date}", "synced_count": 0, "errors": []}

            year_folder = date_parts[0]   # "2026"
            month_folder = date_parts[1]  # "03"

            # Find the drive for "Shared Documents" on the RogersBuildLLC site
            drive_service = MsDriveService()
            all_drives = drive_service.read_all()
            shared_docs_drive = next(
                (d for d in all_drives if d.web_url and "RogersBuildLLC" in d.web_url and "Documents" in (d.name or "")),
                None,
            )
            if not shared_docs_drive:
                return {"success": False, "message": "Could not find 'Shared Documents' drive for RogersBuildLLC site", "synced_count": 0, "errors": []}

            graph_drive_id = shared_docs_drive.drive_id

            # Navigate to 520 - Current Receipts
            receipts_folder_id = self._navigate_to_folder(graph_drive_id, self._RECEIPTS_FOLDER_PATH)
            if not receipts_folder_id:
                return {"success": False, "message": "Could not navigate to '520 - Current Receipts' folder", "synced_count": 0, "errors": []}

            # Get-or-create yyyy folder
            year_item_id = self._get_or_create_subfolder(graph_drive_id, shared_docs_drive.public_id, receipts_folder_id, year_folder)
            if not year_item_id:
                return {"success": False, "message": f"Could not get/create year folder '{year_folder}'", "synced_count": 0, "errors": []}

            # Get-or-create mm folder
            month_item_id = self._get_or_create_subfolder(graph_drive_id, shared_docs_drive.public_id, year_item_id, month_folder)
            if not month_item_id:
                return {"success": False, "message": f"Could not get/create month folder '{month_folder}'", "synced_count": 0, "errors": []}

            # Upload attachments
            try:
                storage = AzureBlobStorage()
            except Exception as e:
                return {"success": False, "message": str(e), "synced_count": 0, "errors": [{"error": str(e)}]}

            vendor = self.vendor_service.read_by_id(id=expense.vendor_id) if expense.vendor_id else None
            vendor_abbreviation = (vendor.abbreviation or vendor.name or "Unknown") if vendor else "Unknown"

            synced_count = 0
            errors = []
            uploaded_attachments = {}

            # Resolve project names for line items (for filename)
            project_cache = {}
            for li in line_items:
                if li.project_id and li.project_id not in project_cache:
                    proj = self.project_service.read_by_id(id=str(li.project_id))
                    project_cache[li.project_id] = (proj.abbreviation or proj.name or "") if proj else ""

            for line_item in line_items:
                try:
                    if not line_item.public_id:
                        continue
                    attachment_link = self.expense_line_item_attachment_service.read_by_expense_line_item_id(
                        expense_line_item_public_id=line_item.public_id
                    )
                    if not attachment_link or not attachment_link.attachment_id:
                        continue
                    if attachment_link.attachment_id in uploaded_attachments:
                        synced_count += 1
                        continue
                    attachment = self.attachment_service.read_by_id(id=attachment_link.attachment_id)
                    if not attachment or not attachment.blob_url:
                        continue

                    # Build filename with EXP prefix
                    project_identifier = project_cache.get(line_item.project_id, "")
                    sub_cost_code_number = ""
                    if line_item.sub_cost_code_id:
                        scc = self.sub_cost_code_service.read_by_id(id=str(line_item.sub_cost_code_id))
                        if scc:
                            sub_cost_code_number = scc.number or ""
                    ref_number = expense.reference_number or ""
                    description = line_item.description or ""
                    price = f"${float(line_item.price):,.2f}" if line_item.price is not None else ""
                    expense_date_str = f"{date_parts[1]}-{date_parts[2]}-{date_parts[0]}"

                    exp_prefix = "EXP-CR" if expense.is_credit else "EXP"
                    if len(line_items) > 1:
                        amount_str = f"${float(expense.total_amount):,.2f}" if expense.total_amount is not None else ""
                        filename_parts = [exp_prefix, project_identifier, vendor_abbreviation, ref_number, "Multiple See Image", amount_str, expense_date_str]
                    else:
                        filename_parts = [exp_prefix, project_identifier, vendor_abbreviation, ref_number, description, sub_cost_code_number, price, expense_date_str]

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
                        errors.append({"line_item_id": line_item.id, "error": f"Blob download failed: {str(e)}"})
                        continue

                    content_type = attachment.content_type or metadata.get("content_type", "application/octet-stream")
                    upload_result = self.driveitem_service.upload_file(
                        drive_public_id=shared_docs_drive.public_id,
                        parent_item_id=month_item_id,
                        filename=sharepoint_filename,
                        content=file_content,
                        content_type=content_type,
                    )
                    if upload_result.get("status_code") not in [200, 201]:
                        errors.append({"line_item_id": line_item.id, "error": upload_result.get("message", "Unknown error")})
                        continue

                    uploaded_attachments[attachment_link.attachment_id] = sharepoint_filename
                    synced_count += 1
                    logger.info(f"Uploaded to receipts folder: {sharepoint_filename}")
                except Exception as e:
                    errors.append({"line_item_id": line_item.id, "error": str(e)})

            return {
                "success": not errors,
                "message": f"Uploaded {synced_count} file(s) to receipts folder ({year_folder}/{month_folder})",
                "synced_count": synced_count,
                "errors": errors,
            }

        except Exception as e:
            logger.exception("Error uploading to general receipts folder")
            return {"success": False, "message": str(e), "synced_count": 0, "errors": [{"error": str(e)}]}
