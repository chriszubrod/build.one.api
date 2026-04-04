# Python Standard Library Imports
import re
import logging
from decimal import Decimal
from typing import Any, Optional

# Third-party Imports

# Local Imports
from entities.invoice.business.model import Invoice
from entities.invoice.persistence.repo import InvoiceRepository
from entities.payment_term.business.service import PaymentTermService
from entities.project.business.service import ProjectService
from entities.module.business.service import ModuleService
from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)


class InvoiceService:
    """
    Service for Invoice entity business operations.
    """

    def __init__(self, repo: Optional[InvoiceRepository] = None):
        from entities.invoice_line_item.business.service import InvoiceLineItemService
        from entities.invoice_attachment.business.service import InvoiceAttachmentService
        from entities.invoice_line_item_attachment.business.service import InvoiceLineItemAttachmentService
        self.repo = repo or InvoiceRepository()
        self.invoice_line_item_service = InvoiceLineItemService()
        self.invoice_attachment_service = InvoiceAttachmentService()
        self.invoice_line_item_attachment_service = InvoiceLineItemAttachmentService()
        self.project_service = ProjectService()
        self.module_service = ModuleService()
        self._driveitem_service: Optional[Any] = None
        self._drive_repo: Optional[Any] = None
        self._project_module_connector: Optional[Any] = None
        self._project_excel_connector: Optional[Any] = None

    @property
    def driveitem_service(self):
        if self._driveitem_service is None:
            from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
            self._driveitem_service = MsDriveItemService()
        return self._driveitem_service

    @property
    def drive_repo(self):
        if self._drive_repo is None:
            from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
            self._drive_repo = MsDriveRepository()
        return self._drive_repo

    @property
    def project_module_connector(self):
        if self._project_module_connector is None:
            from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
            self._project_module_connector = DriveItemProjectModuleConnector()
        return self._project_module_connector

    @property
    def project_excel_connector(self):
        if self._project_excel_connector is None:
            from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import DriveItemProjectExcelConnector
            self._project_excel_connector = DriveItemProjectExcelConnector()
        return self._project_excel_connector

    def create(
        self,
        *,
        tenant_id: int = 1,
        project_public_id: str,
        payment_term_public_id: Optional[str] = None,
        invoice_date: str,
        due_date: str,
        invoice_number: str,
        total_amount: Optional[Decimal] = None,
        memo: Optional[str] = None,
        is_draft: bool = True,
    ) -> Invoice:
        if not project_public_id:
            raise ValueError("Project is required.")
        if not invoice_date:
            raise ValueError("Invoice date is required.")
        if not due_date:
            raise ValueError("Due date is required.")
        if not invoice_number:
            raise ValueError("Invoice number is required.")

        project = ProjectService().read_by_public_id(public_id=project_public_id)
        if not project:
            raise ValueError(f"Project with public_id '{project_public_id}' not found.")
        project_id = project.id

        payment_term_id = None
        if payment_term_public_id:
            payment_term = PaymentTermService().read_by_public_id(public_id=payment_term_public_id)
            if not payment_term:
                raise ValueError(f"Payment term with public_id '{payment_term_public_id}' not found.")
            payment_term_id = payment_term.id

        existing = self.repo.read_by_invoice_number_and_project_id(
            invoice_number=invoice_number, project_id=project_id
        )
        if existing:
            raise ValueError(
                f"An invoice with number '{invoice_number}' already exists for this project."
            )

        return self.repo.create(
            tenant_id=tenant_id,
            project_id=project_id,
            payment_term_id=payment_term_id,
            invoice_date=invoice_date,
            due_date=due_date,
            invoice_number=invoice_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=is_draft,
        )

    def read_all(self) -> list[Invoice]:
        return self.repo.read_all()

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        project_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        sort_by: str = "InvoiceDate",
        sort_direction: str = "DESC",
    ) -> list[Invoice]:
        return self.repo.read_paginated(
            page_number=page_number,
            page_size=page_size,
            search_term=search_term,
            project_id=project_id,
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
        project_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
    ) -> int:
        return self.repo.count(
            search_term=search_term,
            project_id=project_id,
            start_date=start_date,
            end_date=end_date,
            is_draft=is_draft,
        )

    def read_by_id(self, id: int) -> Optional[Invoice]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Invoice]:
        return self.repo.read_by_public_id(public_id)

    def read_by_invoice_number(self, invoice_number: str) -> Optional[Invoice]:
        return self.repo.read_by_invoice_number(invoice_number)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        project_public_id: str = None,
        payment_term_public_id: str = None,
        invoice_date: str = None,
        due_date: str = None,
        invoice_number: str = None,
        total_amount: float = None,
        memo: str = None,
        is_draft: bool = None,
    ) -> Optional[Invoice]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = row_version

        if project_public_id is not None:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            existing.project_id = project.id

        if payment_term_public_id is not None:
            payment_term = PaymentTermService().read_by_public_id(public_id=payment_term_public_id)
            existing.payment_term_id = payment_term.id if payment_term else None

        if invoice_date is not None:
            existing.invoice_date = invoice_date
        if due_date is not None:
            existing.due_date = due_date
        if invoice_number is not None:
            existing.invoice_number = invoice_number
        if total_amount is not None:
            existing.total_amount = Decimal(str(total_amount))
        if memo is not None:
            existing.memo = memo
        if is_draft is not None:
            existing.is_draft = is_draft

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Invoice]:
        """
        Delete an invoice by public ID with cascading deletes of line items and attachments.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None

        invoice_id = existing.id

        # Reset IsBilled on source line items before deleting
        line_items = self.invoice_line_item_service.read_by_invoice_id(invoice_id=invoice_id)
        for line_item in line_items:
            try:
                self._reset_source_as_unbilled(line_item)
            except Exception as e:
                logger.warning(f"Error resetting IsBilled for line item {line_item.id}: {e}")

        # Delete invoice line items
        for line_item in line_items:
            try:
                if line_item.id:
                    self.invoice_line_item_service.delete_by_public_id(public_id=line_item.public_id)
            except Exception as e:
                logger.warning(f"Error deleting invoice line item {line_item.id}: {e}")

        # Delete invoice attachments (link first, then attachment record + blob)
        from entities.attachment.business.service import AttachmentService
        from shared.storage import AzureBlobStorage
        attachment_service = AttachmentService()
        invoice_attachments = self.invoice_attachment_service.read_by_invoice_id(invoice_id=invoice_id)
        for inv_attachment in invoice_attachments:
            try:
                att = attachment_service.read_by_id(id=inv_attachment.attachment_id) if inv_attachment.attachment_id else None
                if inv_attachment.id:
                    self.invoice_attachment_service.delete_by_id(id=inv_attachment.id)
                if att:
                    if att.blob_url:
                        try:
                            AzureBlobStorage().delete_file(att.blob_url)
                        except Exception:
                            logger.warning(f"Failed to delete blob for attachment {att.public_id}")
                    attachment_service.delete_by_public_id(public_id=att.public_id)
            except Exception as e:
                logger.warning(f"Error deleting invoice attachment {inv_attachment.id}: {e}")

        return self.repo.delete_by_id(existing.id)

    def complete_invoice(self, public_id: str) -> dict:
        """
        Finalize an invoice: set is_draft=False on the invoice and all line items.
        Mark source line items as billed. Syncs to QBO if auth is configured.
        """
        invoice = self.read_by_public_id(public_id=public_id)
        if not invoice:
            return {"status_code": 404, "message": "Invoice not found", "invoice_finalized": False, "errors": []}

        if not invoice.is_draft:
            logger.info(f"Invoice {public_id} is already finalized")

        # Finalize invoice
        try:
            project = self.project_service.read_by_id(id=invoice.project_id) if invoice.project_id else None
            project_public_id = project.public_id if project else None

            payment_term_public_id = None
            if invoice.payment_term_id:
                payment_term = PaymentTermService().read_by_id(id=invoice.payment_term_id)
                if payment_term:
                    payment_term_public_id = payment_term.public_id

            finalized = self.update_by_public_id(
                public_id=public_id,
                row_version=invoice.row_version,
                project_public_id=project_public_id,
                payment_term_public_id=payment_term_public_id,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                invoice_number=invoice.invoice_number,
                total_amount=float(invoice.total_amount) if invoice.total_amount else None,
                memo=invoice.memo,
                is_draft=False,
            )

            if not finalized:
                return {
                    "status_code": 500,
                    "message": "Failed to finalize invoice (row-version conflict)",
                    "invoice_finalized": False,
                    "errors": [{"step": "finalize_invoice", "error": "Row version conflict"}],
                }
        except Exception as e:
            logger.exception(f"Error finalizing invoice {public_id}")
            return {
                "status_code": 500,
                "message": f"Error finalizing invoice: {str(e)}",
                "invoice_finalized": False,
                "errors": [{"step": "finalize_invoice", "error": str(e)}],
            }

        # Finalize line items and mark source items as billed
        line_items = self.invoice_line_item_service.read_by_invoice_id(invoice_id=invoice.id)
        errors = []
        for line_item in line_items:
            if line_item.is_draft:
                try:
                    self.invoice_line_item_service.update_by_public_id(
                        public_id=line_item.public_id,
                        row_version=line_item.row_version,
                        invoice_public_id=public_id,
                        source_type=line_item.source_type,
                        description=line_item.description,
                        amount=float(line_item.amount) if line_item.amount else None,
                        markup=float(line_item.markup) if line_item.markup else None,
                        price=float(line_item.price) if line_item.price else None,
                        is_draft=False,
                    )
                except Exception as e:
                    logger.error(f"Error finalizing invoice line item {line_item.id}: {e}")
                    errors.append({"line_item_id": line_item.id, "error": str(e)})

            # Mark source line item as billed
            try:
                self._mark_source_as_billed(line_item)
            except Exception as e:
                logger.warning(f"Error marking source as billed for line item {line_item.id}: {e}")
                errors.append({"line_item_id": line_item.id, "error": f"mark_billed: {str(e)}"})

        # Step 3b: Upload attachments to SharePoint
        sharepoint_result = self._upload_to_sharepoint(invoice=finalized, line_items=line_items)
        if not sharepoint_result.get("success"):
            errors.extend(sharepoint_result.get("errors", []))
            logger.warning(f"SharePoint upload completed with errors for invoice {public_id}: {sharepoint_result.get('message')}")
        else:
            logger.info(f"SharePoint upload complete for invoice {public_id}: {sharepoint_result.get('message')}")

        # Excel workbook sync disabled
        excel_result = {"success": True, "message": "Disabled", "synced_count": 0, "errors": []}

        # QBO push sync disabled
        qbo_result = {"success": True, "message": "Disabled", "errors": []}

        status_code = 200 if not errors else 207
        return {
            "status_code": status_code,
            "message": "Invoice completed successfully" + (f" with {len(errors)} error(s)" if errors else ""),
            "invoice_finalized": True,
            "sharepoint_upload": sharepoint_result,
            "excel_sync": excel_result,
            "qbo_sync": qbo_result,
            "errors": errors,
        }

    def sync_to_excel_workbook(self, invoice, line_items: list, project_id: int) -> dict:
        """
        Update the DRAW REQUEST column (H) in the project Excel workbook for each
        source line item row that was previously written by the Bill/Expense sync.

        Each Bill/Expense sync row stores the source line item's public_id in column Z
        as a reconciliation key. This method scans the worksheet, finds those rows, and
        writes the invoice number into column H ("DRAW REQUEST").

        Manual line items have no source row in the worksheet and are skipped.
        """
        from integrations.ms.sharepoint.external.client import (
            get_excel_used_range_values,
            update_excel_range,
        )
        from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository

        try:
            excel_mapping = self.project_excel_connector.get_excel_for_project(project_id=project_id)
            if not excel_mapping:
                return {"success": False, "message": f"Excel workbook not linked for project {project_id}", "synced_count": 0, "errors": []}

            worksheet_name = excel_mapping.get("worksheet_name")
            if not worksheet_name:
                return {"success": False, "message": "Worksheet name not found in Excel mapping", "synced_count": 0, "errors": []}

            driveitem = next(
                (item for item in MsDriveItemRepository().read_all() if item.id == excel_mapping.get("id")),
                None,
            )
            if not driveitem:
                return {"success": False, "message": "DriveItem not found for Excel workbook", "synced_count": 0, "errors": []}

            drive = self.drive_repo.read_by_id(driveitem.ms_drive_id)
            if not drive:
                return {"success": False, "message": "Drive not found for Excel workbook", "synced_count": 0, "errors": []}

            # Read current worksheet data
            worksheet_result = get_excel_used_range_values(
                drive_id=drive.drive_id,
                item_id=driveitem.item_id,
                worksheet_name=worksheet_name,
            )
            if worksheet_result.get("status_code") != 200:
                return {"success": False, "message": f"Could not read worksheet: {worksheet_result.get('message')}", "synced_count": 0, "errors": []}

            range_data = worksheet_result.get("range", {})
            worksheet_values = range_data.get("values", [])
            range_address = range_data.get("address", "")

            if not worksheet_values:
                return {"success": True, "message": "Worksheet is empty, nothing to update", "synced_count": 0, "errors": []}

            # Parse the range address to determine starting column and row.
            # Address format: "SheetName!B2:Z150" or "B2:Z150"
            addr = range_address.split("!")[-1] if "!" in range_address else range_address
            addr_match = re.match(r"([A-Z]+)(\d+)", addr)
            start_col_letter = addr_match.group(1) if addr_match else "A"
            start_row_num = int(addr_match.group(2)) if addr_match else 1

            def col_letter_to_index(letters: str) -> int:
                """0-based index: A=0, B=1, ..., Z=25"""
                result = 0
                for ch in letters:
                    result = result * 26 + (ord(ch) - ord("A") + 1)
                return result - 1

            start_col_index = col_letter_to_index(start_col_letter)
            # Column Z = index 25 absolute; relative to range start:
            z_col_relative = col_letter_to_index("Z") - start_col_index
            # Column H = index 7 absolute; relative to range start:
            h_col_relative = col_letter_to_index("H") - start_col_index

            if z_col_relative < 0 or z_col_relative >= len(worksheet_values[0]) if worksheet_values else True:
                return {"success": False, "message": "Column Z (reconciliation key) is outside the used range", "synced_count": 0, "errors": []}

            # Build a lookup: source_public_id → worksheet row number (1-based, absolute)
            key_to_row = {}
            for row_idx, row in enumerate(worksheet_values):
                if len(row) > z_col_relative:
                    cell_val = row[z_col_relative]
                    if cell_val and isinstance(cell_val, str) and len(cell_val) == 36:
                        key_to_row[cell_val.lower()] = start_row_num + row_idx

            # Collect source public_ids for each line item
            errors = []
            synced_count = 0
            invoice_number = invoice.invoice_number or ""

            for line_item in line_items:
                source_public_id = None
                try:
                    if line_item.source_type == "BillLineItem" and line_item.bill_line_item_id:
                        from entities.bill_line_item.business.service import BillLineItemService
                        bill_li = BillLineItemService().read_by_id(line_item.bill_line_item_id)
                        source_public_id = str(bill_li.public_id) if bill_li else None

                    elif line_item.source_type == "ExpenseLineItem" and line_item.expense_line_item_id:
                        from entities.expense_line_item.business.service import ExpenseLineItemService
                        expense_li = ExpenseLineItemService().read_by_id(line_item.expense_line_item_id)
                        source_public_id = str(expense_li.public_id) if expense_li else None

                    elif line_item.source_type == "BillCreditLineItem" and line_item.bill_credit_line_item_id:
                        from entities.bill_credit_line_item.business.service import BillCreditLineItemService
                        credit_li = BillCreditLineItemService().read_by_id(line_item.bill_credit_line_item_id)
                        source_public_id = str(credit_li.public_id) if credit_li else None

                    # Manual lines have no source row in the worksheet — skip
                    if not source_public_id:
                        continue

                    ws_row = key_to_row.get(source_public_id.lower())
                    if not ws_row:
                        logger.info(f"No worksheet row found for source public_id {source_public_id} (InvoiceLineItem {line_item.id})")
                        continue

                    # Update column H ("DRAW REQUEST") in this row
                    cell_address = f"H{ws_row}"
                    update_result = update_excel_range(
                        drive_id=drive.drive_id,
                        item_id=driveitem.item_id,
                        worksheet_name=worksheet_name,
                        range_address=cell_address,
                        values=[[invoice_number]],
                    )
                    if update_result.get("status_code") == 200:
                        synced_count += 1
                        logger.info(f"Updated {cell_address} = '{invoice_number}' for source {source_public_id}")
                    else:
                        err = f"Failed to update {cell_address}: {update_result.get('message')}"
                        logger.error(err)
                        errors.append({"line_item_id": line_item.id, "error": err})

                except Exception as e:
                    logger.error(f"Error syncing InvoiceLineItem {line_item.id} to Excel: {e}")
                    errors.append({"line_item_id": line_item.id, "error": str(e)})

            return {
                "success": not errors,
                "message": f"Updated {synced_count} row(s) in Excel workbook",
                "synced_count": synced_count,
                "errors": errors,
            }

        except Exception as e:
            logger.exception(f"Error syncing invoice to Excel workbook for project {project_id}")
            return {"success": False, "message": f"Error syncing to Excel: {str(e)}", "synced_count": 0, "errors": [{"error": str(e)}]}

    def get_billable_items_for_project(self, project_public_id: str, invoice_public_id: str = None) -> dict:
        """
        Fetch billable, unbilled line items for a project.
        Returns {"ready": [...], "draft": [...]} where ready items are finalized
        and draft items still need review before they can be billed.
        """
        from shared.database import get_connection

        project = self.project_service.read_by_public_id(public_id=project_public_id)
        if not project:
            raise ValueError(f"Project with public_id '{project_public_id}' not found.")

        already_linked_bill = set()
        already_linked_expense = set()
        already_linked_credit = set()

        if invoice_public_id:
            invoice = self.read_by_public_id(public_id=invoice_public_id)
            if invoice and invoice.id:
                existing_line_items = self.invoice_line_item_service.read_by_invoice_id(invoice_id=invoice.id)
                for li in existing_line_items:
                    if li.bill_line_item_id:
                        already_linked_bill.add(li.bill_line_item_id)
                    if li.expense_line_item_id:
                        already_linked_expense.add(li.expense_line_item_id)
                    if li.bill_credit_line_item_id:
                        already_linked_credit.add(li.bill_credit_line_item_id)

        items = []
        project_id = project.id

        with get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT bli.Id, bli.PublicId, bli.Description, bli.Amount, bli.Markup, bli.Price,
                       b.PublicId AS ParentPublicId, b.BillNumber AS ParentNumber,
                       b.BillDate AS SourceDate, v.Name AS VendorName,
                       scc.Number AS SubCostCodeNumber, scc.Name AS SubCostCodeName,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.BillLineItem bli
                JOIN dbo.Bill b ON b.Id = bli.BillId
                LEFT JOIN dbo.Vendor v ON v.Id = b.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = bli.SubCostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.BillLineItemAttachment blia
                    JOIN dbo.Attachment a ON a.Id = blia.AttachmentId
                    WHERE blia.BillLineItemId = bli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE bli.ProjectId = ?
                  AND bli.IsBillable = 1
                  AND (bli.IsBilled = 0 OR bli.IsBilled IS NULL)
                  AND bli.IsDraft = 0
            """, [project_id])
            for row in cursor.fetchall():
                if row.Id not in already_linked_bill:
                    items.append({
                        "source_type": "BillLineItem",
                        "source_id": row.Id,
                        "source_public_id": str(row.PublicId),
                        "parent_number": row.ParentNumber,
                        "parent_public_id": str(row.ParentPublicId),
                        "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                        "vendor_name": row.VendorName or "",
                        "description": row.Description,
                        "amount": float(row.Amount) if row.Amount is not None else None,
                        "markup": float(row.Markup) if row.Markup is not None else None,
                        "price": float(row.Price) if row.Price is not None else None,
                        "sub_cost_code_number": row.SubCostCodeNumber,
                        "sub_cost_code_name": row.SubCostCodeName,
                        "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                    })

            cursor.execute("""
                SELECT eli.Id, eli.PublicId, eli.Description, eli.Amount, eli.Markup, eli.Price,
                       e.PublicId AS ParentPublicId, e.ReferenceNumber AS ParentNumber,
                       e.ExpenseDate AS SourceDate, v.Name AS VendorName,
                       scc.Number AS SubCostCodeNumber, scc.Name AS SubCostCodeName,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.ExpenseLineItem eli
                JOIN dbo.Expense e ON e.Id = eli.ExpenseId
                LEFT JOIN dbo.Vendor v ON v.Id = e.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = eli.SubCostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.ExpenseLineItemAttachment elia
                    JOIN dbo.Attachment a ON a.Id = elia.AttachmentId
                    WHERE elia.ExpenseLineItemId = eli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE eli.ProjectId = ?
                  AND eli.IsBillable = 1
                  AND (eli.IsBilled = 0 OR eli.IsBilled IS NULL)
                  AND eli.IsDraft = 0
            """, [project_id])
            for row in cursor.fetchall():
                if row.Id not in already_linked_expense:
                    items.append({
                        "source_type": "ExpenseLineItem",
                        "source_id": row.Id,
                        "source_public_id": str(row.PublicId),
                        "parent_number": row.ParentNumber,
                        "parent_public_id": str(row.ParentPublicId),
                        "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                        "vendor_name": row.VendorName or "",
                        "description": row.Description,
                        "amount": float(row.Amount) if row.Amount is not None else None,
                        "markup": float(row.Markup) if row.Markup is not None else None,
                        "price": float(row.Price) if row.Price is not None else None,
                        "sub_cost_code_number": row.SubCostCodeNumber,
                        "sub_cost_code_name": row.SubCostCodeName,
                        "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                    })

            cursor.execute("""
                SELECT bcli.Id, bcli.PublicId, bcli.Description, bcli.Amount, bcli.BillableAmount,
                       bc.PublicId AS ParentPublicId, bc.CreditNumber AS ParentNumber,
                       bc.CreditDate AS SourceDate, v.Name AS VendorName,
                       scc.Number AS SubCostCodeNumber, scc.Name AS SubCostCodeName,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.BillCreditLineItem bcli
                JOIN dbo.BillCredit bc ON bc.Id = bcli.BillCreditId
                LEFT JOIN dbo.Vendor v ON v.Id = bc.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = bcli.SubCostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.BillCreditLineItemAttachment bclia
                    JOIN dbo.Attachment a ON a.Id = bclia.AttachmentId
                    WHERE bclia.BillCreditLineItemId = bcli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE bcli.ProjectId = ?
                  AND bcli.IsBillable = 1
                  AND (bcli.IsBilled = 0 OR bcli.IsBilled IS NULL)
                  AND bcli.IsDraft = 0
            """, [project_id])
            for row in cursor.fetchall():
                if row.Id not in already_linked_credit:
                    items.append({
                        "source_type": "BillCreditLineItem",
                        "source_id": row.Id,
                        "source_public_id": str(row.PublicId),
                        "parent_number": row.ParentNumber,
                        "parent_public_id": str(row.ParentPublicId),
                        "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                        "vendor_name": row.VendorName or "",
                        "description": row.Description,
                        "amount": float(row.Amount) if row.Amount is not None else None,
                        "markup": None,
                        "price": float(row.BillableAmount) if row.BillableAmount is not None else None,
                        "sub_cost_code_number": row.SubCostCodeNumber,
                        "sub_cost_code_name": row.SubCostCodeName,
                        "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                    })

            # Draft billable items (IsDraft=1) — need review before they can be billed
            draft_items = []

            cursor.execute("""
                SELECT bli.Id, bli.PublicId, bli.Description, bli.Amount, bli.Markup, bli.Price,
                       b.PublicId AS ParentPublicId, b.BillNumber AS ParentNumber,
                       b.BillDate AS SourceDate, v.Name AS VendorName,
                       scc.Number AS SubCostCodeNumber, scc.Name AS SubCostCodeName
                FROM dbo.BillLineItem bli
                JOIN dbo.Bill b ON b.Id = bli.BillId
                LEFT JOIN dbo.Vendor v ON v.Id = b.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = bli.SubCostCodeId
                WHERE bli.ProjectId = ?
                  AND bli.IsBillable = 1
                  AND (bli.IsBilled = 0 OR bli.IsBilled IS NULL)
                  AND bli.IsDraft = 1
            """, [project_id])
            for row in cursor.fetchall():
                draft_items.append({
                    "source_type": "BillLineItem",
                    "parent_number": row.ParentNumber,
                    "parent_public_id": str(row.ParentPublicId),
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "description": row.Description,
                    "amount": float(row.Amount) if row.Amount is not None else None,
                    "markup": float(row.Markup) if row.Markup is not None else None,
                    "price": float(row.Price) if row.Price is not None else None,
                    "sub_cost_code_number": row.SubCostCodeNumber,
                    "sub_cost_code_name": row.SubCostCodeName,
                })

            cursor.execute("""
                SELECT eli.Id, eli.PublicId, eli.Description, eli.Amount, eli.Markup, eli.Price,
                       e.PublicId AS ParentPublicId, e.ReferenceNumber AS ParentNumber,
                       e.ExpenseDate AS SourceDate, v.Name AS VendorName,
                       scc.Number AS SubCostCodeNumber, scc.Name AS SubCostCodeName
                FROM dbo.ExpenseLineItem eli
                JOIN dbo.Expense e ON e.Id = eli.ExpenseId
                LEFT JOIN dbo.Vendor v ON v.Id = e.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = eli.SubCostCodeId
                WHERE eli.ProjectId = ?
                  AND eli.IsBillable = 1
                  AND (eli.IsBilled = 0 OR eli.IsBilled IS NULL)
                  AND eli.IsDraft = 1
            """, [project_id])
            for row in cursor.fetchall():
                draft_items.append({
                    "source_type": "ExpenseLineItem",
                    "parent_number": row.ParentNumber,
                    "parent_public_id": str(row.ParentPublicId),
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "description": row.Description,
                    "amount": float(row.Amount) if row.Amount is not None else None,
                    "markup": float(row.Markup) if row.Markup is not None else None,
                    "price": float(row.Price) if row.Price is not None else None,
                    "sub_cost_code_number": row.SubCostCodeNumber,
                    "sub_cost_code_name": row.SubCostCodeName,
                })

            cursor.execute("""
                SELECT bcli.Id, bcli.PublicId, bcli.Description, bcli.Amount, bcli.BillableAmount,
                       bc.PublicId AS ParentPublicId, bc.CreditNumber AS ParentNumber,
                       bc.CreditDate AS SourceDate, v.Name AS VendorName,
                       scc.Number AS SubCostCodeNumber, scc.Name AS SubCostCodeName
                FROM dbo.BillCreditLineItem bcli
                JOIN dbo.BillCredit bc ON bc.Id = bcli.BillCreditId
                LEFT JOIN dbo.Vendor v ON v.Id = bc.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = bcli.SubCostCodeId
                WHERE bcli.ProjectId = ?
                  AND bcli.IsBillable = 1
                  AND bcli.IsDraft = 1
            """, [project_id])
            for row in cursor.fetchall():
                draft_items.append({
                    "source_type": "BillCreditLineItem",
                    "parent_number": row.ParentNumber,
                    "parent_public_id": str(row.ParentPublicId),
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "description": row.Description,
                    "amount": float(row.Amount) if row.Amount is not None else None,
                    "markup": None,
                    "price": float(row.BillableAmount) if row.BillableAmount is not None else None,
                    "sub_cost_code_number": row.SubCostCodeNumber,
                    "sub_cost_code_name": row.SubCostCodeName,
                })

            cursor.close()

        return {"ready": items, "draft": draft_items}

    def get_next_invoice_number(self, project_public_id: str) -> str:
        """
        Given a project, determine the next invoice number.
        Pattern: {project.abbreviation}-{N} where N is max existing + 1.
        If no existing invoices, starts at 1.
        """
        project = self.project_service.read_by_public_id(public_id=project_public_id)
        if not project:
            raise ValueError(f"Project with public_id '{project_public_id}' not found.")

        prefix = (project.abbreviation or "INV").upper()

        invoices = self.repo.read_paginated(
            page_number=1,
            page_size=10000,
            project_id=project.id,
        )

        max_num = 0
        pattern = re.compile(rf'^{re.escape(prefix)}-(\d+)$', re.IGNORECASE)
        for inv in invoices:
            if inv.invoice_number:
                m = pattern.match(inv.invoice_number.strip())
                if m:
                    num = int(m.group(1))
                    if num > max_num:
                        max_num = num

        return f"{prefix}-{max_num + 1}"

    def _upload_to_sharepoint(self, invoice, line_items: list) -> dict:
        """
        Upload invoice line item attachments and the PDF packet to the Invoices SharePoint module folder.
        Mirrors Expense._upload_attachments_to_module_folder pattern.
        """
        from shared.database import get_connection
        from entities.attachment.business.service import AttachmentService

        try:
            # 1. Resolve the Invoices module folder
            module = self.module_service.read_by_name("Invoices") or self.module_service.read_by_name("Invoice")
            if not module:
                return {"success": False, "message": "Invoices module not found — ensure a module named 'Invoices' exists", "synced_count": 0, "errors": [{"error": "Invoices module not found"}]}

            if not invoice.project_id:
                return {"success": False, "message": "Invoice has no project assigned", "synced_count": 0, "errors": [{"error": "Invoice has no project"}]}

            module_folder = self.project_module_connector.get_folder_for_module(project_id=invoice.project_id, module_id=int(module.id))
            if not module_folder:
                return {"success": False, "message": "Invoices module folder not configured for this project", "synced_count": 0, "errors": [{"error": "Invoices module folder not configured for this project"}]}

            folder_ms_drive_id = module_folder.get("ms_drive_id")
            folder_item_id = module_folder.get("item_id")
            if not folder_ms_drive_id or not folder_item_id:
                return {"success": False, "message": "Module folder missing drive or item_id", "synced_count": 0, "errors": [{"error": "Module folder missing drive or item_id"}]}

            drive = self.drive_repo.read_by_id(folder_ms_drive_id)
            if not drive:
                return {"success": False, "message": "Drive not found", "synced_count": 0, "errors": [{"error": "Drive not found"}]}

            try:
                storage = AzureBlobStorage()
            except Exception as e:
                return {"success": False, "message": str(e), "synced_count": 0, "errors": [{"error": str(e)}]}

            att_service = AttachmentService()
            invoice_number = invoice.invoice_number or invoice.public_id
            errors = []
            synced_count = 0
            uploaded_attachment_ids: set = set()

            # Create (or reuse) a subfolder named after the invoice number
            safe_folder_name = re.sub(r'[<>:"/\\|?*]', '_', invoice_number)
            folder_result = self.driveitem_service.create_folder(
                drive_public_id=drive.public_id,
                parent_item_id=folder_item_id,
                folder_name=safe_folder_name,
            )
            if folder_result.get("status_code") not in [200, 201] or not folder_result.get("item"):
                return {"success": False, "message": f"Failed to create invoice subfolder: {folder_result.get('message')}", "synced_count": 0, "errors": [{"error": folder_result.get("message")}]}
            upload_folder_item_id = folder_result["item"]["item_id"]

            # 2. Collect attachment metadata for all line item source types
            bill_ids = [li.bill_line_item_id for li in line_items if li.source_type == "BillLineItem" and li.bill_line_item_id]
            expense_ids = [li.expense_line_item_id for li in line_items if li.source_type == "ExpenseLineItem" and li.expense_line_item_id]
            credit_ids = [li.bill_credit_line_item_id for li in line_items if li.source_type == "BillCreditLineItem" and li.bill_credit_line_item_id]
            manual_public_ids = [li.public_id for li in line_items if li.source_type == "Manual" and li.public_id]

            attachment_rows = []  # list of dicts with attachment metadata + display fields

            with get_connection() as conn:
                cursor = conn.cursor()

                if bill_ids:
                    ph = ",".join("?" * len(bill_ids))
                    cursor.execute(f"""
                        SELECT a.Id, a.BlobUrl, a.ContentType, a.FileExtension, a.OriginalFilename,
                               ISNULL(v.Name, '') AS VendorName, b.BillNumber AS ParentNumber,
                               ISNULL(ili.Description, '') AS Description,
                               ISNULL(scc.Number, '') AS SccNumber,
                               ili.Price,
                               CONVERT(VARCHAR(10), b.BillDate, 120) AS SourceDate
                        FROM dbo.BillLineItemAttachment blia
                        JOIN dbo.Attachment a ON a.Id = blia.AttachmentId
                        JOIN dbo.BillLineItem bli ON bli.Id = blia.BillLineItemId
                        JOIN dbo.Bill b ON b.Id = bli.BillId
                        LEFT JOIN dbo.Vendor v ON v.Id = b.VendorId
                        LEFT JOIN dbo.InvoiceLineItem ili ON ili.BillLineItemId = blia.BillLineItemId
                        LEFT JOIN dbo.SubCostCode scc ON scc.Id = ili.SubCostCodeId
                        WHERE blia.BillLineItemId IN ({ph})
                    """, bill_ids)
                    for row in cursor.fetchall():
                        attachment_rows.append({
                            "attachment_id": row.Id, "blob_url": row.BlobUrl,
                            "content_type": row.ContentType, "file_extension": row.FileExtension,
                            "original_filename": row.OriginalFilename,
                            "vendor_name": row.VendorName, "parent_number": row.ParentNumber,
                            "description": row.Description, "scc_number": row.SccNumber,
                            "price": row.Price, "source_date": row.SourceDate,
                        })

                if expense_ids:
                    ph = ",".join("?" * len(expense_ids))
                    cursor.execute(f"""
                        SELECT a.Id, a.BlobUrl, a.ContentType, a.FileExtension, a.OriginalFilename,
                               ISNULL(v.Name, '') AS VendorName, e.ReferenceNumber AS ParentNumber,
                               ISNULL(ili.Description, '') AS Description,
                               ISNULL(scc.Number, '') AS SccNumber,
                               ili.Price,
                               CONVERT(VARCHAR(10), e.ExpenseDate, 120) AS SourceDate
                        FROM dbo.ExpenseLineItemAttachment elia
                        JOIN dbo.Attachment a ON a.Id = elia.AttachmentId
                        JOIN dbo.ExpenseLineItem eli ON eli.Id = elia.ExpenseLineItemId
                        JOIN dbo.Expense e ON e.Id = eli.ExpenseId
                        LEFT JOIN dbo.Vendor v ON v.Id = e.VendorId
                        LEFT JOIN dbo.InvoiceLineItem ili ON ili.ExpenseLineItemId = elia.ExpenseLineItemId
                        LEFT JOIN dbo.SubCostCode scc ON scc.Id = ili.SubCostCodeId
                        WHERE elia.ExpenseLineItemId IN ({ph})
                    """, expense_ids)
                    for row in cursor.fetchall():
                        attachment_rows.append({
                            "attachment_id": row.Id, "blob_url": row.BlobUrl,
                            "content_type": row.ContentType, "file_extension": row.FileExtension,
                            "original_filename": row.OriginalFilename,
                            "vendor_name": row.VendorName, "parent_number": row.ParentNumber,
                            "description": row.Description, "scc_number": row.SccNumber,
                            "price": row.Price, "source_date": row.SourceDate,
                        })

                if credit_ids:
                    ph = ",".join("?" * len(credit_ids))
                    cursor.execute(f"""
                        SELECT a.Id, a.BlobUrl, a.ContentType, a.FileExtension, a.OriginalFilename,
                               ISNULL(v.Name, '') AS VendorName, bc.CreditNumber AS ParentNumber,
                               ISNULL(ili.Description, '') AS Description,
                               ISNULL(scc.Number, '') AS SccNumber,
                               ili.Price,
                               CONVERT(VARCHAR(10), bc.CreditDate, 120) AS SourceDate
                        FROM dbo.BillCreditLineItemAttachment bclia
                        JOIN dbo.Attachment a ON a.Id = bclia.AttachmentId
                        JOIN dbo.BillCreditLineItem bcli ON bcli.Id = bclia.BillCreditLineItemId
                        JOIN dbo.BillCredit bc ON bc.Id = bcli.BillCreditId
                        LEFT JOIN dbo.Vendor v ON v.Id = bc.VendorId
                        LEFT JOIN dbo.InvoiceLineItem ili ON ili.BillCreditLineItemId = bclia.BillCreditLineItemId
                        LEFT JOIN dbo.SubCostCode scc ON scc.Id = ili.SubCostCodeId
                        WHERE bclia.BillCreditLineItemId IN ({ph})
                    """, credit_ids)
                    for row in cursor.fetchall():
                        attachment_rows.append({
                            "attachment_id": row.Id, "blob_url": row.BlobUrl,
                            "content_type": row.ContentType, "file_extension": row.FileExtension,
                            "original_filename": row.OriginalFilename,
                            "vendor_name": row.VendorName, "parent_number": row.ParentNumber,
                            "description": row.Description, "scc_number": row.SccNumber,
                            "price": row.Price, "source_date": row.SourceDate,
                        })

                if manual_public_ids:
                    ph = ",".join("?" * len(manual_public_ids))
                    cursor.execute(f"""
                        SELECT a.Id, a.BlobUrl, a.ContentType, a.FileExtension, a.OriginalFilename,
                               '' AS VendorName, '' AS ParentNumber,
                               ISNULL(ili.Description, '') AS Description,
                               ISNULL(scc.Number, '') AS SccNumber,
                               ili.Price, '' AS SourceDate
                        FROM dbo.InvoiceLineItemAttachment ilia
                        JOIN dbo.Attachment a ON a.Id = ilia.AttachmentId
                        JOIN dbo.InvoiceLineItem ili ON ili.Id = ilia.InvoiceLineItemId
                        LEFT JOIN dbo.SubCostCode scc ON scc.Id = ili.SubCostCodeId
                        WHERE ili.PublicId IN ({ph})
                    """, manual_public_ids)
                    for row in cursor.fetchall():
                        attachment_rows.append({
                            "attachment_id": row.Id, "blob_url": row.BlobUrl,
                            "content_type": row.ContentType, "file_extension": row.FileExtension,
                            "original_filename": row.OriginalFilename,
                            "vendor_name": row.VendorName, "parent_number": row.ParentNumber,
                            "description": row.Description, "scc_number": row.SccNumber,
                            "price": row.Price, "source_date": row.SourceDate,
                        })

                cursor.close()

            # 3. Upload each unique attachment
            for row_data in attachment_rows:
                att_id = row_data["attachment_id"]
                if att_id in uploaded_attachment_ids:
                    synced_count += 1
                    continue
                blob_url = row_data["blob_url"]
                if not blob_url:
                    errors.append({"attachment_id": att_id, "error": "Missing blob URL"})
                    continue

                vendor = row_data["vendor_name"] or ""
                parent_num = row_data["parent_number"] or ""
                description = row_data["description"] or ""
                scc = row_data["scc_number"] or ""
                price_str = f"${float(row_data['price']):,.2f}" if row_data["price"] is not None else ""
                source_date = row_data["source_date"] or ""

                filename_parts = [invoice_number, vendor, parent_num, description, scc, price_str, source_date]
                base_filename = re.sub(r'[<>:"/\\|?*]', '_', " - ".join(p for p in filename_parts if p))

                file_extension = row_data["file_extension"] or ""
                if not file_extension and row_data["original_filename"] and "." in (row_data["original_filename"] or ""):
                    file_extension = row_data["original_filename"].rsplit(".", 1)[-1]
                if not file_extension and row_data["content_type"]:
                    file_extension = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}.get(row_data["content_type"], "")
                if file_extension and not file_extension.startswith("."):
                    file_extension = "." + file_extension

                sharepoint_filename = base_filename + file_extension

                try:
                    file_content, metadata = storage.download_file(blob_url)
                except Exception as e:
                    errors.append({"attachment_id": att_id, "error": str(e)})
                    continue

                content_type = row_data["content_type"] or metadata.get("content_type", "application/octet-stream")
                upload_result = self.driveitem_service.upload_file(
                    drive_public_id=drive.public_id,
                    parent_item_id=upload_folder_item_id,
                    filename=sharepoint_filename,
                    content=file_content,
                    content_type=content_type,
                )
                if upload_result.get("status_code") not in [200, 201]:
                    errors.append({"attachment_id": att_id, "error": upload_result.get("message", "Unknown error")})
                    continue

                uploaded_attachment_ids.add(att_id)
                synced_count += 1

            # 4. Upload packet PDF if one has already been generated
            existing_links = self.invoice_attachment_service.read_by_invoice_id(invoice_id=invoice.id)
            for link in existing_links:
                if not link.attachment_id:
                    continue
                packet_att = att_service.read_by_id(link.attachment_id)
                if not packet_att or packet_att.category != "invoice_packet" or not packet_att.blob_url:
                    continue
                try:
                    file_content, _ = storage.download_file(packet_att.blob_url)
                    packet_filename = re.sub(r'[<>:"/\\|?*]', '_', invoice_number) + " - Packet.pdf"
                    upload_result = self.driveitem_service.upload_file(
                        drive_public_id=drive.public_id,
                        parent_item_id=upload_folder_item_id,
                        filename=packet_filename,
                        content=file_content,
                        content_type="application/pdf",
                    )
                    if upload_result.get("status_code") in [200, 201]:
                        synced_count += 1
                    else:
                        errors.append({"packet": True, "error": upload_result.get("message", "Unknown error")})
                except Exception as e:
                    errors.append({"packet": True, "error": str(e)})

            return {"success": not errors, "message": f"Uploaded {synced_count} file(s)", "synced_count": synced_count, "errors": errors}

        except Exception as e:
            logger.exception("Error uploading invoice attachments to SharePoint")
            return {"success": False, "message": str(e), "synced_count": 0, "errors": [{"error": str(e)}]}

    def _sync_billed_status_to_qbo(self, line_items: list, realm_id: str) -> None:
        """
        After invoice completion, re-push each affected QBO Bill/Purchase with
        BillableStatus = HasBeenBilled so they no longer appear as unbilled in QBO.
        Deduplicates by parent bill/expense — one QBO API call per affected bill.
        """
        from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
        from entities.bill_line_item.business.service import BillLineItemService

        bill_connector = BillBillConnector()
        bill_li_svc = BillLineItemService()
        updated_bill_ids: set = set()

        for line_item in line_items:
            if line_item.source_type == "BillLineItem" and line_item.bill_line_item_id:
                source = bill_li_svc.read_by_id(line_item.bill_line_item_id)
                if source and source.bill_id and source.bill_id not in updated_bill_ids:
                    try:
                        bill_connector.update_has_been_billed_in_qbo(source.bill_id, realm_id)
                        updated_bill_ids.add(source.bill_id)
                    except Exception as e:
                        logger.warning(f"Failed to update HasBeenBilled for QBO Bill (bill_id={source.bill_id}): {e}")

        # Expense/Purchase side: log for now — purchase connector update can be added if needed
        for line_item in line_items:
            if line_item.source_type == "ExpenseLineItem" and line_item.expense_line_item_id:
                logger.info(
                    f"ExpenseLineItem {line_item.expense_line_item_id} billed — "
                    f"QBO Purchase HasBeenBilled update not yet implemented"
                )

    def _mark_source_as_billed(self, line_item) -> None:
        """Mark the source line item (BillLineItem, ExpenseLineItem, BillCreditLineItem) as billed."""
        if line_item.source_type == "BillLineItem" and line_item.bill_line_item_id:
            from entities.bill_line_item.business.service import BillLineItemService
            svc = BillLineItemService()
            source = svc.read_by_id(id=line_item.bill_line_item_id)
            if source and not source.is_billed:
                from entities.bill.business.service import BillService
                bill = BillService().read_by_id(id=source.bill_id) if source.bill_id else None
                bill_public_id = bill.public_id if bill else None
                if bill_public_id:
                    svc.update_by_public_id(
                        public_id=source.public_id,
                        row_version=source.row_version,
                        bill_public_id=bill_public_id,
                        is_billed=True,
                    )

        elif line_item.source_type == "ExpenseLineItem" and line_item.expense_line_item_id:
            from entities.expense_line_item.business.service import ExpenseLineItemService
            svc = ExpenseLineItemService()
            source = svc.read_by_id(id=line_item.expense_line_item_id)
            if source and not source.is_billed:
                from entities.expense.business.service import ExpenseService
                expense = ExpenseService().read_by_id(id=source.expense_id) if source.expense_id else None
                expense_public_id = expense.public_id if expense else None
                if expense_public_id:
                    svc.update_by_public_id(
                        public_id=source.public_id,
                        row_version=source.row_version,
                        expense_public_id=expense_public_id,
                        is_billed=True,
                    )

        elif line_item.source_type == "BillCreditLineItem" and line_item.bill_credit_line_item_id:
            from entities.bill_credit_line_item.business.service import BillCreditLineItemService
            from entities.bill_credit_line_item.api.schemas import BillCreditLineItemUpdate
            svc = BillCreditLineItemService()
            source = svc.read_by_id(id=line_item.bill_credit_line_item_id)
            if source and not source.is_billed:
                from entities.bill_credit.business.service import BillCreditService
                credit = BillCreditService().read_by_id(id=source.bill_credit_id) if source.bill_credit_id else None
                credit_public_id = credit.public_id if credit else None
                if credit_public_id:
                    update_schema = BillCreditLineItemUpdate(
                        row_version=source.row_version,
                        bill_credit_public_id=credit_public_id,
                        is_billed=True,
                    )
                    svc.update_by_public_id(
                        public_id=source.public_id,
                        bill_credit_line_item=update_schema,
                    )

    def _reset_source_as_unbilled(self, line_item) -> None:
        """Reset IsBilled=False on the source line item when an invoice is deleted."""
        if line_item.source_type == "BillLineItem" and line_item.bill_line_item_id:
            from entities.bill_line_item.business.service import BillLineItemService
            svc = BillLineItemService()
            source = svc.read_by_id(id=line_item.bill_line_item_id)
            if source and source.is_billed:
                from entities.bill.business.service import BillService
                bill = BillService().read_by_id(id=source.bill_id) if source.bill_id else None
                if bill:
                    svc.update_by_public_id(
                        public_id=source.public_id,
                        row_version=source.row_version,
                        bill_public_id=bill.public_id,
                        is_billed=False,
                    )

        elif line_item.source_type == "ExpenseLineItem" and line_item.expense_line_item_id:
            from entities.expense_line_item.business.service import ExpenseLineItemService
            svc = ExpenseLineItemService()
            source = svc.read_by_id(id=line_item.expense_line_item_id)
            if source and source.is_billed:
                from entities.expense.business.service import ExpenseService
                expense = ExpenseService().read_by_id(id=source.expense_id) if source.expense_id else None
                if expense:
                    svc.update_by_public_id(
                        public_id=source.public_id,
                        row_version=source.row_version,
                        expense_public_id=expense.public_id,
                        is_billed=False,
                    )

        elif line_item.source_type == "BillCreditLineItem" and line_item.bill_credit_line_item_id:
            from entities.bill_credit_line_item.business.service import BillCreditLineItemService
            from entities.bill_credit_line_item.api.schemas import BillCreditLineItemUpdate
            svc = BillCreditLineItemService()
            source = svc.read_by_id(id=line_item.bill_credit_line_item_id)
            if source and source.is_billed:
                from entities.bill_credit.business.service import BillCreditService
                credit = BillCreditService().read_by_id(id=source.bill_credit_id) if source.bill_credit_id else None
                if credit:
                    update_schema = BillCreditLineItemUpdate(
                        row_version=source.row_version,
                        bill_credit_public_id=credit.public_id,
                        is_billed=False,
                    )
                    svc.update_by_public_id(
                        public_id=source.public_id,
                        bill_credit_line_item=update_schema,
                    )
