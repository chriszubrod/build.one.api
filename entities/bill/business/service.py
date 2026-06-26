# Python Standard Library Imports
import logging
import re
import time
from collections import defaultdict
from decimal import Decimal
from typing import Any, List, Optional

# Third-party Imports

# Local Imports
from shared.access import assert_can_access_bill
from shared.authz import current_user_id, current_is_system_admin
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
    append_excel_rows,
    create_workbook_session,
    close_workbook_session,
)

from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)


def find_insertion_row_for_subcostcode(worksheet_values: List[List[Any]], target_subcostcode: str) -> Optional[int]:
    """
    Find the row number where a new line item should be inserted based on SubCostCode.

    Logic:
    1. Find all rows where Column C matches the target SubCostCode (the "block")
    2. Within that block, find the last row that has BOTH Date (Column I) AND Vendor (Column J)
    3. Insert AFTER that last data row
    4. Fallback: if no data rows, insert two rows after the first matching row
    5. If no matching SubCostCode at all, return None (append at end)

    Args:
        worksheet_values: 2D array of cell values from the worksheet
        target_subcostcode: The SubCostCode number to match (e.g., "65.03" or "37")

    Returns:
        The 1-based row number where the new row should be inserted,
        or None if no matching SubCostCode found (append at end)
    """
    if not worksheet_values:
        return None

    matching_rows = []  # List of (excel_row, has_date_and_vendor)

    for row_index, row in enumerate(worksheet_values):
        if row_index == 0:
            continue  # Skip header row

        excel_row = row_index + 1  # 1-based

        col_c_value = row[2] if len(row) > 2 else None
        if col_c_value is None:
            continue
        col_c_str = str(col_c_value).strip()
        if not col_c_str:
            continue

        target_str = str(target_subcostcode).strip()

        # Match: exact string or numeric equivalence
        subcostcode_match = col_c_str == target_str
        if not subcostcode_match:
            try:
                subcostcode_match = float(col_c_str) == float(target_str)
            except (ValueError, TypeError):
                pass

        if subcostcode_match:
            col_i = row[8] if len(row) > 8 else None
            col_j = row[9] if len(row) > 9 else None
            has_data = (
                col_i is not None and str(col_i).strip() != ""
                and col_j is not None and str(col_j).strip() != ""
            )
            matching_rows.append((excel_row, has_data))

    if not matching_rows:
        logger.info(f"SubCostCode '{target_subcostcode}': No matching rows found, will append at end")
        return None

    # Find the last row with data (Date + Vendor)
    last_data_row = None
    for excel_row, has_data in matching_rows:
        if has_data:
            last_data_row = excel_row

    if last_data_row is not None:
        # Insert after the last data row
        logger.info(f"SubCostCode '{target_subcostcode}': inserting after last data row {last_data_row}")
        return last_data_row + 1

    # Fallback: no data rows in block, insert two rows after the first matching row
    first_match_row = matching_rows[0][0]
    logger.info(f"SubCostCode '{target_subcostcode}': no data rows, inserting two rows after first match row {first_match_row}")
    return first_match_row + 2


# ---------------------------------------------------------------------------
# QBO deep-link URL builder.
# ---------------------------------------------------------------------------
# Module-level + pure so the test surface is trivial. Realm is included in the
# query string so the link 404s cleanly (instead of opening a stranger's
# company file) when the user's last QBO session was a different realm.

def _build_qbo_bill_url(qbo_id: str, realm_id: str) -> str:
    return f"https://app.qbo.intuit.com/app/bill?txnId={qbo_id}&realmId={realm_id}"


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
        self._attachable_attachment_connector: Optional[Any] = None

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

    @property
    def attachable_attachment_connector(self):
        if self._attachable_attachment_connector is None:
            from integrations.intuit.qbo.attachable.connector.attachment.business.service import AttachableAttachmentConnector
            self._attachable_attachment_connector = AttachableAttachmentConnector()
        return self._attachable_attachment_connector

    def create(self, *, tenant_id: int = 1, user_id: Optional[int] = None, vendor_public_id: Optional[str] = None, payment_term_public_id: Optional[str] = None, bill_date: str, due_date: str, bill_number: Optional[str] = None, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True, intake_source: Optional[str] = None, intake_source_detail: Optional[str] = None, source_email_message_public_id: Optional[str] = None, attachment_public_id: Optional[str] = None,
               require_attachment: bool = True,
               line_description: Optional[str] = None, line_quantity: Optional[int] = None,
               line_rate: Optional[Decimal] = None, line_amount: Optional[Decimal] = None,
               line_markup: Optional[Decimal] = None, line_price: Optional[Decimal] = None,
               line_is_billable: Optional[bool] = None,
               line_sub_cost_code_id: Optional[int] = None,
               line_project_public_id: Optional[str] = None) -> Bill:
        """
        Create a new bill.

        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            user_id: dbo.[User].Id of the caller. When provided AND the bill
                     is created in draft, an auto-Submitted Review row is
                     written for the new bill (audit-of-submission).
            vendor_public_id: Vendor public ID (required)
            payment_term_public_id: Payment term public ID (optional)
            bill_date: Bill date
            due_date: Due date
            bill_number: Bill number
            total_amount: Total amount (optional)
            memo: Memo (optional)
            is_draft: Whether bill is in draft state
            intake_source: How this bill arrived. One of 'manual' | 'agent' |
                           'script'. Set-once at create. Routers compute this
                           from the JWT username; scripts pass explicitly.
            intake_source_detail: Specific actor — username, agent name, or
                                   script name. Disambiguates within a source.
            attachment_public_id: REQUIRED (universal rule). UUID of an Attachment
                                   row that was uploaded ahead of this call. Must
                                   be application/pdf. The server creates a
                                   placeholder BillLineItem and links the
                                   attachment to it via BillLineItemAttachment.
        """
        # Universal rule: every Bill must have a PDF attachment from the moment
        # of creation — EXCEPT bills projected from an external system of record
        # (QBO pull), which legitimately have no local PDF. Those callers pass
        # require_attachment=False; their line items are created by the connector,
        # not the placeholder-attachment path below. When an attachment IS supplied
        # we always validate it. Fail fast before touching any DB.
        if require_attachment and not attachment_public_id:
            raise ValueError("Attachment is required. Upload a PDF first and pass attachment_public_id.")
        attachment = None
        if attachment_public_id:
            attachment = self.attachment_service.read_by_public_id(public_id=attachment_public_id)
            if not attachment:
                # Auto-bridge fallback: if the public_id points at an
                # EmailAttachment instead of an Attachment, bridge it on the fly
                # and continue. Saves the agent one round-trip and the human a
                # confusing "not found" when the wrong UUID type is passed.
                from entities.email_message.business.service import EmailAttachmentBridgeService
                from entities.email_message.persistence.repo import EmailAttachmentRepository
                ea = EmailAttachmentRepository().read_by_public_id(attachment_public_id)
                if not ea:
                    raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found.")
                bridged = EmailAttachmentBridgeService().bridge(
                    email_attachment_public_id=attachment_public_id
                )
                logger.info(
                    "BillService.create auto-bridged EmailAttachment %s → Attachment %s",
                    attachment_public_id, bridged.public_id,
                )
                attachment_public_id = str(bridged.public_id)
                attachment = bridged
            if attachment.content_type != "application/pdf":
                raise ValueError(
                    f"Attachment must be application/pdf; got '{attachment.content_type}'."
                )

        if not bill_date:
            raise ValueError("Bill date is required.")
        if not due_date:
            raise ValueError("Due date is required.")

        # Resolve vendor — required for non-drafts, optional for drafts
        vendor_id = None
        if vendor_public_id:
            vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
            if not vendor:
                raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
            vendor_id = vendor.id
        elif not is_draft:
            raise ValueError("Vendor is required.")

        # Bill number — required for non-drafts, optional for drafts
        if not bill_number and not is_draft:
            raise ValueError("Bill number is required.")

        # Resolve payment_term_public_id to payment_term_id
        payment_term_id = None

        if payment_term_public_id:

            payment_term = PaymentTermService().read_by_public_id(public_id=payment_term_public_id)

            if not payment_term:
                raise ValueError(f"Payment term with public_id '{payment_term_public_id}' not found.")

            payment_term_id = payment_term.id

        # Resolve source EmailMessage UUID → BIGINT for the FK column.
        # We do this BEFORE the duplicate check so we can opportunistically
        # backfill the link on an already-existing Bill that came in via a
        # non-email intake path.
        source_email_message_id = None
        if source_email_message_public_id:
            from entities.email_message.business.service import EmailMessageService
            source_email = EmailMessageService().read_by_public_id(public_id=source_email_message_public_id)
            if not source_email:
                raise ValueError(f"EmailMessage with public_id '{source_email_message_public_id}' not found.")
            source_email_message_id = source_email.id

        # Duplicate check — only when both vendor and bill number are present
        if vendor_id and bill_number:
            existing = self.repo.read_by_bill_number_and_vendor_id(bill_number=bill_number, vendor_id=vendor_id, bill_date=bill_date)
            if existing:
                # Opportunistic source-email backfill: if this create_bill
                # call carries an email source AND the existing Bill row
                # has no source linked yet, stamp the link so the email
                # dedup trail is preserved. The underlying sproc filters
                # on SourceEmailMessageId IS NULL — won't overwrite a
                # link to a different email.
                link_msg = ""
                if source_email_message_id is not None:
                    try:
                        linked = self.repo.link_source_email_message(
                            bill_id=existing.id,
                            source_email_message_id=source_email_message_id,
                        )
                        if linked:
                            link_msg = (
                                f" Linked this email source to existing "
                                f"Bill (Bill.PublicId={existing.public_id}) "
                                f"so the dedup trail is preserved."
                            )
                        else:
                            link_msg = (
                                f" Existing Bill already has a source email "
                                f"linked — no change to that field."
                            )
                    except Exception as link_error:
                        logger.exception(
                            "Failed to backfill source email on existing Bill %s: %s",
                            existing.public_id, link_error,
                        )
                        link_msg = (
                            f" (also tried to link source email to existing "
                            f"Bill but failed: {link_error})"
                        )

                raise ValueError(
                    f"A bill with BillNumber '{bill_number}' and this date "
                    f"already exists for this vendor "
                    f"(Bill.PublicId={existing.public_id}, IsDraft={existing.is_draft}). "
                    f"Please update the existing bill instead of creating a new one.{link_msg}"
                )

        bill = self.repo.create(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            payment_term_id=payment_term_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=is_draft,
            intake_source=intake_source,
            intake_source_detail=intake_source_detail,
            source_email_message_id=source_email_message_id,
            created_by_user_id=current_user_id.get(),
        )

        # Universal-PDF rule: the Bill is meaningless without its attachment,
        # so every newly-created bill carries one. The attachment hangs off a
        # placeholder BillLineItem created here. The user fills in its fields
        # (description, sub-cost-code, qty, rate, etc.) on the Edit page or
        # adds more line items alongside it. If either of these inserts fails
        # we roll back the bill — having a Bill row without its required
        # attachment violates the invariant.
        #
        # Skipped entirely when no attachment was supplied (require_attachment=False,
        # i.e. QBO-projected bills): those get their real line items from the
        # connector's _sync_line_items, so we must NOT create an empty placeholder.
        if attachment_public_id:
            try:
                # Resolve project_public_id → project_id (BillLineItemService
                # may take either; we resolve here to keep the call explicit).
                line_kwargs: dict = {
                    "tenant_id": tenant_id,
                    "bill_public_id": bill.public_id,
                    # is_billable defaults True for summary-line use; the
                    # caller can override by passing line_is_billable=False
                    # explicitly. None (omitted) → True.
                    "is_billable": True if line_is_billable is None else bool(line_is_billable),
                }
                if line_description is not None:
                    line_kwargs["description"] = line_description
                if line_quantity is not None:
                    line_kwargs["quantity"] = line_quantity
                if line_rate is not None:
                    line_kwargs["rate"] = line_rate
                if line_amount is not None:
                    line_kwargs["amount"] = line_amount
                if line_markup is not None:
                    line_kwargs["markup"] = line_markup
                if line_price is not None:
                    line_kwargs["price"] = line_price
                if line_sub_cost_code_id is not None:
                    line_kwargs["sub_cost_code_id"] = line_sub_cost_code_id
                if line_project_public_id is not None:
                    line_kwargs["project_public_id"] = line_project_public_id
                placeholder_line_item = self.bill_line_item_service.create(**line_kwargs)
                self.bill_line_item_attachment_service.create(
                    tenant_id=tenant_id,
                    bill_line_item_public_id=placeholder_line_item.public_id,
                    attachment_public_id=attachment_public_id,
                )
            except Exception as attach_error:
                logger.exception(
                    "Failed to attach PDF to new bill %s; rolling back the bill row.",
                    bill.public_id,
                )
                try:
                    self.repo.delete_by_id(id=bill.id)
                except Exception:
                    logger.exception(
                        "Best-effort rollback of bill %s also failed; manual cleanup may be required.",
                        bill.public_id,
                    )
                raise ValueError(f"Failed to attach PDF to new bill: {attach_error}")

        # Auto-Submit hook. Creating a draft bill IS submitting it for review
        # — when the caller has supplied enough data for the reviewer email
        # to resolve real recipients. The gate:
        #
        #   - is_draft must be True (already-finalized bills have no review
        #     window).
        #   - user_id must be set (no auth context = don't fabricate audit).
        #   - line_project_public_id must be present. Recipient resolution
        #     walks Bill → BillLineItem → Project → UserProject → PM/Owner,
        #     so without a project on the line item the resolver returns an
        #     empty TO/CC and notification_service.py:288 short-circuits the
        #     "In Review" advance. That was the 2026-06-25 K06988 bug:
        #     manual UI creates landed with a placeholder line item (no
        #     project), the auto-fire shipped a blank-bodied BCC-only email,
        #     and the workflow transition was skipped.
        #
        # We deliberately do NOT also gate on line_sub_cost_code_id even
        # though SCC shows up in the notification body. bill_specialist's
        # prompt explicitly leaves SCC null on invoice-driven creates ("SCC
        # is for the human to apply during review"), so requiring it would
        # silently disable the auto-fire for the entire email pipeline —
        # the load-bearing happy path the hook exists to serve. A missing
        # SCC just renders an empty cell in the email body; reviewers fill
        # it in via their email reply (the reviewer-reply automation hooks
        # parse it back out).
        #
        # The bill-folder pipeline doesn't pass user_id and therefore never
        # reaches this hook regardless — folder bills land as plain drafts
        # and the user must click Submit for Review on the BillEdit page
        # after coding the line items. Manual UI creates are the same shape.
        #
        # The notification hook itself lives in ReviewService.create's Bill
        # block, so any future caller that writes a Submitted Review row
        # for a Bill — agent, manual submit, scripted backfill — fires the
        # notification uniformly. Failures don't roll back the bill (a
        # missing review row is recoverable via the manual Submit endpoint;
        # a missing bill is not).
        if is_draft and user_id is not None and line_project_public_id is not None:
            try:
                from entities.review.business.service import ReviewService
                from entities.review_status.business.service import ReviewStatusService

                first_status = ReviewStatusService().get_first_status()
                if first_status is None:
                    logger.warning(
                        "Bill %s created but no initial ReviewStatus is configured; "
                        "skipping auto-Submit Review write.",
                        bill.public_id,
                    )
                else:
                    ReviewService().create(
                        review_status_id=first_status.id,
                        user_id=user_id,
                        comments=None,
                        bill_id=bill.id,
                        # Wave 3 Phase E: link the Submitted row back to
                        # the source vendor email for the Web UI's final-
                        # review surface. NULL when the bill came from a
                        # non-email path.
                        email_message_id=source_email_message_id,
                    )
                    # The notification + the Review→"In Review" advance both
                    # fire from inside ReviewService.create's Bill block —
                    # no extra plumbing needed here.
            except Exception as review_error:
                logger.exception(
                    "Failed to auto-write Submitted Review row for bill %s: %s",
                    bill.public_id, review_error,
                )

        return bill

    def read_all(self) -> list[Bill]:
        """
        Read bills, scoped by UserProject for non-admin actors.
        """
        return self.repo.read_all(
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

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
        conn=None,
    ) -> list[Bill]:
        """
        Read bills with pagination and filtering, scoped by UserProject.
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
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        conn=None,
    ) -> int:
        """
        Count bills matching filter criteria, scoped by UserProject.
        """
        return self.repo.count(
            search_term=search_term,
            vendor_id=vendor_id,
            start_date=start_date,
            end_date=end_date,
            is_draft=is_draft,
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def read_by_id(self, id: int) -> Optional[Bill]:
        """
        Read a bill by ID.
        """
        bill = self.repo.read_by_id(id)
        if bill is None:
            return None
        assert_can_access_bill(bill.id)
        return bill

    def read_by_public_id(self, public_id: str) -> Optional[Bill]:
        """
        Read a bill by public ID.
        """
        bill = self.repo.read_by_public_id(public_id)
        if bill is None:
            return None
        assert_can_access_bill(bill.id)
        return bill

    def get_qbo_bill_url(self, bill_id: int) -> Optional[str]:
        """
        Return a clickable deep link to the bill in QuickBooks Online, or
        None if the bill has no synced QBO counterpart yet (drafted but
        not pushed). Realm is included so the link 404s cleanly when the
        active QBO session belongs to a different realm.

        Used by `GET /api/v1/get/bill/{public_id}` to attach
        `qbo_bill_url` to the response.
        """
        assert_can_access_bill(bill_id)
        info = self.repo.read_qbo_link_info(bill_id=bill_id)
        if not info:
            return None
        return _build_qbo_bill_url(info["qbo_id"], info["qbo_realm_id"])

    def read_by_bill_number(self, bill_number: str) -> Optional[Bill]:
        """
        Read a bill by bill number.
        """
        bill = self.repo.read_by_bill_number(bill_number)
        if bill is None:
            return None
        assert_can_access_bill(bill.id)
        return bill

    def read_by_bill_number_and_vendor_public_id(self, bill_number: str, vendor_public_id: str) -> Optional[Bill]:
        """
        Read a bill by bill number and vendor public ID.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)

        if not vendor:
            return None

        bill = self.repo.read_by_bill_number_and_vendor_id(bill_number=bill_number, vendor_id=vendor.id)
        if bill is None:
            return None
        assert_can_access_bill(bill.id)
        return bill

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
                            amount_str = f"${Decimal(str(bill.total_amount)):,.2f}"
                        except Exception:
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

        # Duplicate check when completing (transitioning from draft to non-draft)
        if is_draft is False and existing.vendor_id and existing.bill_number:
            duplicate = self.repo.read_by_bill_number_and_vendor_id(
                bill_number=existing.bill_number,
                vendor_id=existing.vendor_id,
                bill_date=existing.bill_date,
            )
            if duplicate and str(duplicate.public_id).upper() != str(public_id).upper():
                raise ValueError(
                    f"A bill with BillNumber '{existing.bill_number}' already exists for this vendor. "
                    f"Please update the existing bill instead of creating a new one."
                )

        # Note: SharePoint sync is performed in complete_bill when "Complete Bill" is clicked.
        # This avoids duplicate uploads when the bill is finalized.

        updated_bill = self.repo.update_by_id(existing)
        
        return updated_bill

    def apply_reviewer_decision(
        self,
        *,
        bill_public_id: str,
        decision: str,
        reviewer_email: str,
        sub_cost_code_public_id: Optional[str] = None,
        description: Optional[str] = None,
        raw_reply_text: Optional[str] = None,
        reviewer_email_message_public_id: Optional[str] = None,
    ) -> dict:
        """Apply a Project Manager / Owner's emailed review decision to a Bill.

        Wave 3 Phase A — orchestrates the three side-effects of a
        reviewer's reply: update the summary BillLineItem (on approval),
        transition the Review state, persist the raw reply text as the
        Review's Comments column.

        Authorization: `reviewer_email` must match a User with
        `UserProject` → Role 'Project Manager' or 'Owner' on any project
        the bill spans (same recipient set the notification went to).

        Idempotency / safety: the bill must still be a draft. Once
        `IsDraft=False` is set (via `complete_bill`), this method refuses
        — the human has taken final responsibility.

        decision ∈ {'approved', 'rejected'} — 'rejected' is also used for
        "needs revision" / questions; the agent puts the human's text in
        `raw_reply_text` and the AP reviewer reads it.

        On approval the agent must supply `sub_cost_code_public_id`. The
        summary line's `description` is updated when supplied; null leaves
        the existing description in place.

        Returns: dict with `decision_applied`, the new `review_status`
        name, the matched `reviewer_user_id`, and the bill's `is_draft`
        for the agent to compose its final outcome.
        """
        from entities.bill_line_item.business.service import BillLineItemService
        from entities.review.business.recipient_service import ReviewRecipientService
        from entities.review_status.business.service import ReviewStatusService
        from entities.review.persistence.repo import ReviewRepository

        if decision not in ("approved", "rejected"):
            raise ValueError(
                f"decision must be 'approved' or 'rejected'; got '{decision}'"
            )

        # 1. Find the bill.
        bill = self.read_by_public_id(public_id=bill_public_id)
        if bill is None or bill.id is None:
            raise ValueError(f"Bill with public_id '{bill_public_id}' not found.")

        # 2. Draft-only guard — once Completed, the human has the final word.
        if not bool(bill.is_draft):
            raise ValueError(
                f"Bill {bill_public_id} is no longer a draft "
                "(Complete already pressed); reviewer decisions cannot be "
                "applied. The human must edit directly."
            )

        # 3. Authorization: reviewer_email must match a PM/Owner recipient.
        envelope = ReviewRecipientService().resolve_for_bill(bill_id=bill.id)
        all_recipients = envelope["to"] + envelope["cc"]
        normalized_email = (reviewer_email or "").strip().lower()
        match = next(
            (
                r for r in all_recipients
                if r.email and r.email.strip().lower() == normalized_email
            ),
            None,
        )
        if match is None:
            raise ValueError(
                f"Sender '{reviewer_email}' is not an authorized reviewer for "
                f"this bill (must be Project Manager or Owner on the project)."
            )
        reviewer_user_id = match.user_id

        # 4. Approval → update the summary BillLineItem (SCC + description).
        if decision == "approved":
            if not sub_cost_code_public_id:
                raise ValueError(
                    "sub_cost_code_public_id is required when decision='approved'."
                )
            scc = SubCostCodeService().read_by_public_id(public_id=sub_cost_code_public_id)
            if scc is None:
                raise ValueError(
                    f"SubCostCode with public_id '{sub_cost_code_public_id}' not found."
                )

            bli_service = BillLineItemService()
            line_items = bli_service.read_by_bill_id(bill_id=bill.id)
            if not line_items:
                raise ValueError(
                    f"Bill {bill_public_id} has no line items to apply the SCC to."
                )
            # Convention: the email-driven flow creates exactly one summary
            # BillLineItem carrying the attachment. If multiple lines exist
            # (rare, manual edits), apply to the first.
            summary_line = line_items[0]
            bli_service.update_by_public_id(
                public_id=summary_line.public_id,
                row_version=summary_line.row_version,
                sub_cost_code_id=int(scc.id),
                description=description if description is not None else None,
            )

        # 5. Transition the Review state. Approval → advance; rejection
        # → decline. Both write a new Review row (insert-only audit
        # trail) with the reviewer as the user_id and the raw reply text
        # as comments.
        # Resolve the target ReviewStatus directly. Per the locked
        # multi-reviewer semantics: every authorized reply is
        # independently authoritative; latest reply wins; the only gate
        # is `Bill.IsDraft` (above). An approval jumps straight to the
        # terminal non-declined status ("Approved"); a rejection jumps
        # to the declined status. We bypass build_advance_payload's
        # one-step-per-reply chain because a PM approval IS the
        # approval, not "moved to in-review".
        comments = (raw_reply_text or "").strip() or None
        review_statuses = ReviewStatusService().read_all()
        if decision == "approved":
            target = next(
                (s for s in review_statuses if s.is_final and not s.is_declined),
                None,
            )
            if target is None:
                raise ValueError(
                    "No terminal non-declined ReviewStatus configured "
                    "(expected one with IsFinal=true AND IsDeclined=false)."
                )
        else:  # rejected
            target = next(
                (s for s in review_statuses if s.is_declined),
                None,
            )
            if target is None:
                raise ValueError(
                    "No declined ReviewStatus configured (expected one with IsDeclined=true)."
                )

        # Resolve the reviewer's reply EmailMessage so the new Review
        # row references it (Wave 3 Phase E: per-row email link for the
        # Web UI's final-review surface). NULL when caller didn't pass
        # one (e.g., manual web-UI invocation rather than email-driven).
        email_message_id: Optional[int] = None
        if reviewer_email_message_public_id:
            from entities.email_message.business.service import EmailMessageService
            em = EmailMessageService().read_by_public_id(public_id=reviewer_email_message_public_id)
            if em is not None:
                email_message_id = em.id

        # Insert a new Review row directly. Insert-only audit trail —
        # repeat replies at the same status are captured as duplicate
        # audit rows, not no-ops. This matches the locked semantic
        # ("interpret every reply") and gives us a per-reply trail.
        new_review = ReviewRepository().create(
            review_status_id=target.id,
            user_id=reviewer_user_id,
            comments=comments,
            bill_id=bill.id,
            expense_id=None,
            bill_credit_id=None,
            invoice_id=None,
            email_message_id=email_message_id,
            created_by_user_id=reviewer_user_id,
        )

        # 6. Look up the new status name for the response payload.
        rs = ReviewStatusService().read_by_id(id=new_review.review_status_id) if new_review.review_status_id else None
        new_status_name = rs.name if rs else None

        return {
            "decision_applied": decision,
            "review_status": new_status_name,
            "reviewer_user_id": reviewer_user_id,
            "is_draft": True,
            "bill_public_id": bill_public_id,
        }

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
        bill_line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill_id)

        # Step 3: Delete each BillLineItem and its associated attachments
        bill_line_item_attachment_repo = BillLineItemAttachmentRepository()
        
        # Initialize storage once (may fail if config is missing, handle gracefully)
        storage = None
        try:
            storage = AzureBlobStorage()
        except Exception as e:
            logger.warning(f"Could not initialize Azure Blob Storage for file deletion: {e}")
        
        for line_item in bill_line_items:
            # Step 3a+3b: Clean up attachments (non-fatal — line item delete must still run)
            try:
                if line_item.public_id:
                    attachment_link = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
                        bill_line_item_public_id=line_item.public_id
                    )

                    if attachment_link and attachment_link.id:
                        # Only delete the link — leave Attachment + blob untouched
                        try:
                            bill_line_item_attachment_repo.delete_by_id(id=attachment_link.id)
                            logger.info(f"Deleted bill line item attachment link {attachment_link.id}")
                        except Exception as e:
                            logger.warning(f"Error deleting bill line item attachment link {attachment_link.id}: {e}")
            except Exception as e:
                logger.warning(f"Error cleaning up attachments for line item {line_item.id}: {e}")

            # Step 3c: Delete the BillLineItem record (must run even if attachment cleanup failed)
            try:
                if line_item.id and line_item.public_id:
                    self.bill_line_item_service.delete_by_public_id(public_id=line_item.public_id)
                    logger.info(f"Deleted bill line item {line_item.id}")
                elif line_item.id:
                    from entities.bill_line_item.persistence.repo import BillLineItemRepository
                    BillLineItemRepository().delete_by_id(id=line_item.id)
                    logger.info(f"Deleted bill line item {line_item.id} (by ID, no public_id)")
            except Exception as e:
                logger.error(f"Failed to delete bill line item {line_item.id}: {e}")
                raise ValueError(f"Cannot delete bill: failed to delete line item {line_item.id}") from e

        # Step 3.5: Clear remaining child rows that FK to Bill, otherwise the
        # final DELETE trips a REFERENCE constraint (e.g. FK_Review_Bill).
        # Review rows are insert-only audit history everywhere else; this is
        # the one path that removes them — when the parent Bill is deleted.
        try:
            from entities.review.persistence.repo import ReviewRepository
            ReviewRepository().delete_by_bill_id(bill_id)
            logger.info(f"Deleted Review rows for bill {bill_id}")
        except Exception as e:
            logger.error(f"Failed to delete Review rows for bill {bill_id}: {e}")
            raise ValueError(f"Cannot delete bill: failed to delete Review rows for bill {bill_id}") from e

        # MsMessageBill links (email-message ↔ bill) also FK to Bill.
        try:
            from integrations.ms.mail.message.connector.bill.persistence.repo import MsMessageBillRepository
            ms_message_bill_repo = MsMessageBillRepository()
            for link in ms_message_bill_repo.read_by_bill_id(bill_id):
                if link.public_id:
                    ms_message_bill_repo.delete_by_public_id(public_id=link.public_id)
            logger.info(f"Deleted MsMessageBill links for bill {bill_id}")
        except Exception as e:
            logger.error(f"Failed to delete MsMessageBill links for bill {bill_id}: {e}")
            raise ValueError(f"Cannot delete bill: failed to delete MsMessageBill links for bill {bill_id}") from e

        return self.repo.delete_by_id(existing.id)


    def _rename_invoice_blob_on_complete(
        self, public_id: str, line_items: list, all_errors: list
    ) -> None:
        """
        On Mark Complete: move all attachment blobs to the container root.
        Blobs already at root are skipped. For each attachment with a folder prefix
        (e.g. contract-labor/..., invoices/...), downloads, re-uploads at root using
        the existing filename, updates the Attachment record, and deletes the old blob.
        """
        if not line_items:
            return
        storage = AzureBlobStorage()
        processed_attachment_ids = set()
        for line_item in line_items:
            if not line_item.public_id:
                continue
            link = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
                bill_line_item_public_id=line_item.public_id
            )
            if not link or not link.attachment_id:
                continue
            if link.attachment_id in processed_attachment_ids:
                continue
            processed_attachment_ids.add(link.attachment_id)
            attachment = self.attachment_service.read_by_id(id=link.attachment_id)
            if not attachment or not attachment.blob_url:
                continue
            container, blob_name = storage._parse_blob_url(attachment.blob_url)
            if "/" not in blob_name:
                continue
            new_blob_name = blob_name.rsplit("/", 1)[-1]
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
                logger.info(f"Moved blob to root: {blob_name} -> {new_blob_name}")
            except Exception as e:
                logger.exception(f"Blob move failed for attachment {attachment.id}")
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
        
        # Step 2b: Ensure invoice blob is named {public_id}.pdf (no-op if already correct)
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
            
            # Excel workbook sync
            excel_result = self.sync_to_excel_workbook(
                bill=bill,
                line_items=project_line_items,
                project_id=project_id,
            )
            excel_sync_results[project_id] = excel_result
            if excel_result.get("errors"):
                all_errors.extend(excel_result["errors"])

            # Box Excel mirror — enqueue a DETAILS-tab update for this project's
            # mapped Box workbook, alongside the MS Excel sync. Additive +
            # failure-isolated: never affects the completion result.
            self._enqueue_box_excel(bill=bill, project_id=project_id)
        
        # Step 5: Enqueue async push to QBO via the outbox. Completion returns
        # immediately; the outbox worker picks up the row within ~5 seconds and
        # actually calls QBO. Retries and dead-lettering happen at the worker.
        qbo_sync_result = self._enqueue_qbo_sync(bill=finalized_bill)
        if qbo_sync_result.get("errors"):
            all_errors.extend(qbo_sync_result["errors"])

        # Step 6: Box mirror — enqueue line-item attachments to each mapped
        # project's Box folder. Additive + failure-isolated: never affects
        # the completion result.
        self._enqueue_box_uploads(bill=bill, line_items=line_items)

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

    def _enqueue_qbo_sync(self, bill) -> dict:
        """
        Enqueue an outbox row for async QBO push of the completed bill.

        This replaces the old inline `_sync_to_qbo()` call. Completion
        returns immediately; the outbox worker picks up the row and actually
        calls QBO in the background. Retries, backoff, and dead-lettering
        all happen at the worker.
        """
        try:
            qbo_auth = self.qbo_auth_service.ensure_valid_token()
            if not qbo_auth or not qbo_auth.realm_id:
                logger.warning(
                    f"Skipping QBO enqueue for Bill {bill.public_id}: no valid QBO auth"
                )
                return {
                    "success": False,
                    "message": "No valid QBO authentication",
                    "qbo_sync_queued": False,
                    "errors": [{"step": "qbo_enqueue", "error": "No valid QBO auth"}],
                }

            from integrations.intuit.qbo.outbox.business.service import QboOutboxService
            outbox_row = QboOutboxService().enqueue(
                kind="sync_bill_to_qbo",
                entity_type="Bill",
                entity_public_id=str(bill.public_id),
                realm_id=qbo_auth.realm_id,
            )
            logger.info(
                f"Enqueued QBO sync for Bill {bill.public_id} "
                f"(outbox {outbox_row.public_id})"
            )
            return {
                "success": True,
                "message": f"QBO push queued (outbox {outbox_row.public_id})",
                "qbo_sync_queued": True,
                "outbox_public_id": outbox_row.public_id,
                "errors": [],
            }
        except Exception as error:
            logger.exception(f"Failed to enqueue QBO sync for Bill {bill.public_id}")
            return {
                "success": False,
                "message": f"Failed to enqueue QBO sync: {error}",
                "qbo_sync_queued": False,
                "errors": [{"step": "qbo_enqueue", "error": str(error)}],
            }

    def _enqueue_box_uploads(self, bill, line_items: List) -> None:
        """
        Enqueue Box uploads for the bill's line-item attachments.

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
            mapping_cache: dict = {}  # project_id -> mapping dict or None
            enqueued_keys = set()  # (project_id, attachment_id)

            for line_item in line_items:
                try:
                    if not line_item.project_id or not line_item.public_id:
                        continue
                    project_id = line_item.project_id
                    if project_id not in mapping_cache:
                        # Vendor bill docs file to the project's "14 - Invoices"
                        # (AP/'invoices') folder.
                        mapping_cache[project_id] = folder_service.read_mapping_by_project_id_and_class(
                            project_id, DOC_CLASS_INVOICES
                        )
                        if mapping_cache[project_id] is None:
                            logger.info(f"box.enqueue.skipped_unmapped_project project_id={project_id} doc_class=invoices")
                    mapping = mapping_cache[project_id]
                    if not mapping:
                        continue
                    attachment_link = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
                        bill_line_item_public_id=line_item.public_id
                    )
                    if not attachment_link or not attachment_link.attachment_id:
                        continue
                    if (project_id, attachment_link.attachment_id) in enqueued_keys:
                        continue
                    attachment = self.attachment_service.read_by_id(id=attachment_link.attachment_id)
                    if not attachment or not attachment.blob_url:
                        continue
                    box_outbox.enqueue_box_upload(
                        entity_type="bill",
                        entity_public_id=str(bill.public_id),
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
                        f"box.enqueue.failed bill={bill.public_id} line_item={line_item.id}: {line_error}"
                    )
        except Exception as e:
            logger.warning(f"box.enqueue.failed bill={bill.public_id}: {e}")

    def _enqueue_box_excel(self, bill, project_id: int) -> None:
        """
        Enqueue a Box DETAILS-tab Excel update for this bill's project.

        Sibling of _enqueue_box_uploads, called from the same completion flow
        right where sync_to_excel_workbook (the MS Excel sync) runs. Looks up
        the project's mapped Box workbook; if mapped, enqueues a single
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
                entity_type="bill",
                entity_public_id=str(bill.public_id),
                project_id=project_id,
                box_file_id=mapping["box_file_id"],
                worksheet_name=mapping["worksheet_name"],
            )
        except Exception as e:
            logger.warning(f"box.excel.enqueue.failed bill={bill.public_id} project_id={project_id}: {e}")

    def push_to_qbo(self, bill, realm_id: str):
        """
        Push a completed Bill to QBO including its attachments.

        Called by the outbox worker during drain. Raises on failure — the
        worker's retry / dead-letter logic handles outcomes. Attachment
        sync failures are logged but do NOT raise (the bill itself landing
        in QBO is the critical outcome; individual attachment issues can
        be surfaced via reconciliation later).

        Args:
            bill: The finalized Bill record.
            realm_id: QBO realm ID.

        Returns:
            QboBill: The pushed QBO bill record.

        Raises:
            QboError (or subclass): On any QBO API failure — the worker
                retries retryable errors and dead-letters the rest.
        """
        logger.info(f"Pushing Bill {bill.public_id} to QBO realm {realm_id}")
        qbo_bill = self.qbo_bill_connector.sync_to_qbo_bill(bill=bill, realm_id=realm_id)

        if not qbo_bill:
            # Connector contract says this shouldn't happen (should raise), but
            # be defensive — treat as a retryable server error so the worker
            # tries again rather than immediately dead-lettering.
            from integrations.intuit.qbo.base.errors import QboServerError
            raise QboServerError(
                f"QBO bill sync returned None for Bill {bill.public_id}",
                request_path="/bill",
                request_method="POST",
            )

        logger.info(
            f"Pushed Bill {bill.public_id} to QBO as QboBill {qbo_bill.id} "
            f"(qbo_id={qbo_bill.qbo_id})"
        )

        # Sync attachments. Best-effort: log errors but don't fail the push.
        if qbo_bill.qbo_id:
            try:
                attachments_synced, attachment_errors = self._sync_attachments_to_qbo(
                    bill=bill,
                    qbo_bill_id=qbo_bill.qbo_id,
                    realm_id=realm_id,
                )
                if attachment_errors:
                    logger.warning(
                        f"Bill {bill.public_id} attachment sync had "
                        f"{len(attachment_errors)} errors: {attachment_errors}"
                    )
                else:
                    logger.info(
                        f"Bill {bill.public_id} synced {attachments_synced} attachments to QBO"
                    )
            except Exception:
                logger.exception(
                    f"Attachment sync failed for Bill {bill.public_id} — "
                    f"bill push succeeded; attachments can be reconciled later"
                )

        return qbo_bill

    def _sync_attachments_to_qbo(self, bill, qbo_bill_id: str, realm_id: str) -> tuple:
        """
        Sync all attachments for a Bill to QBO as Attachable objects.

        Args:
            bill: The finalized Bill record
            qbo_bill_id: The QBO Bill ID (string) to link attachments to
            realm_id: QBO realm ID

        Returns:
            Tuple of (attachments_synced: int, errors: list[str])
        """
        bill_id = int(bill.id) if isinstance(bill.id, str) else bill.id
        line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill_id)
        if not line_items:
            return 0, []

        attachments_synced = 0
        errors = []
        synced_attachment_ids = set()

        for line_item in line_items:
            if not line_item.public_id:
                continue

            # Get attachment link for this line item
            attachment_link = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
                bill_line_item_public_id=line_item.public_id
            )
            if not attachment_link or not attachment_link.attachment_id:
                continue

            # Skip if already synced (same attachment shared across line items)
            if attachment_link.attachment_id in synced_attachment_ids:
                continue

            attachment = self.attachment_service.read_by_id(id=attachment_link.attachment_id)
            if not attachment or not attachment.blob_url:
                continue

            try:
                self.attachable_attachment_connector.sync_attachment_to_qbo(
                    attachment=attachment,
                    realm_id=realm_id,
                    entity_type="Bill",
                    entity_id=qbo_bill_id,
                )
                synced_attachment_ids.add(attachment_link.attachment_id)
                attachments_synced += 1
            except Exception as e:
                error_msg = f"Attachment {attachment.id} sync failed: {str(e)}"
                logger.error(f"Failed to sync attachment {attachment.id} to QBO for Bill {bill_id}: {e}")
                errors.append(error_msg)

        if attachments_synced > 0:
            logger.info(f"Synced {attachments_synced} attachment(s) to QBO for Bill {bill_id}")

        return attachments_synced, errors

    def sync_to_excel_workbook(
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

            if bill.vendor_id:
                vendor = self.vendor_service.read_by_id(id=bill.vendor_id)
            
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

            # Idempotency check: collect existing public_ids from column Z (index 25)
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
                # First row (header) is expected to be short; warn only if data rows are also short
                logger.warning(
                    f"Worksheet has {short_rows} row(s) with fewer than 26 columns. "
                    f"Column Z (reconciliation key) may not exist. Idempotency check may not prevent duplicates."
                )

            # Filter out line items that are already in the worksheet
            new_line_items = []
            for line_item in line_items:
                pid = str(line_item.public_id).strip() if line_item.public_id else ""
                if pid and pid in existing_public_ids:
                    logger.info(f"BillLineItem {pid} already in worksheet, skipping")
                else:
                    new_line_items.append(line_item)

            if not new_line_items:
                logger.info(f"All {len(line_items)} line item(s) already in worksheet, nothing to sync")
                return {
                    "success": True,
                    "message": f"All {len(line_items)} row(s) already synced",
                    "synced_count": 0,
                    "errors": []
                }

            if len(new_line_items) < len(line_items):
                logger.info(f"{len(line_items) - len(new_line_items)} line item(s) already in worksheet, syncing {len(new_line_items)} new")

            # Group line items by SubCostCode
            line_items_by_subcostcode = defaultdict(list)
            for line_item in new_line_items:
                sub_cost_code_id = line_item.sub_cost_code_id if line_item.sub_cost_code_id else None
                line_items_by_subcostcode[sub_cost_code_id].append(line_item)
            
            errors = []
            synced_count = 0
            rows_to_append = []  # Rows that couldn't find a SubCostCode match
            
            # Build groups: resolve SubCostCode details, build rows, find insertion points
            # Row structure: A(empty), B(CostCode), C(SubCostCode), D-H(empty), I(Date), J(Vendor), K(BillNum), L(Desc), M("Bill"), N(Price), O-Y(empty), Z(public_id)
            insert_groups = []  # List of (insertion_row, group_rows, sub_cost_code_number)

            for sub_cost_code_id, subcostcode_line_items in line_items_by_subcostcode.items():
                sub_cost_code_number = ""
                cost_code_number = ""

                if sub_cost_code_id:
                    sub_cost_code = self.sub_cost_code_service.read_by_id(id=str(sub_cost_code_id))
                    if sub_cost_code:
                        sub_cost_code_number = sub_cost_code.number or ""
                        cost_code_number = sub_cost_code_number.split(".")[0] if "." in sub_cost_code_number else sub_cost_code_number

                group_rows = []
                for line_item in subcostcode_line_items:
                    try:
                        row = [
                            "",                                                          # A: Empty
                            cost_code_number,                                            # B: CostCode
                            sub_cost_code_number,                                        # C: SubCostCode
                            "", "", "", "", "",                                          # D-H: Empty
                            bill.bill_date[:10] if bill.bill_date else "",               # I: Bill Date
                            vendor.name or "",                                           # J: Vendor
                            bill.bill_number or "",                                      # K: Bill Number
                            line_item.description or "",                                 # L: Description
                            "Bill",                                                      # M: Type
                            float(line_item.price) if line_item.price is not None else 0, # N: Price (numeric for Excel formulas)
                            "", "", "", "", "", "", "", "", "", "", "",                   # O-Y: Empty
                            str(line_item.public_id) if line_item.public_id else ""      # Z: Reconciliation key
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

                # Find insertion row
                insertion_row = None
                if sub_cost_code_number and worksheet_values:
                    insertion_row = find_insertion_row_for_subcostcode(
                        worksheet_values=worksheet_values,
                        target_subcostcode=sub_cost_code_number
                    )

                if insertion_row:
                    insert_groups.append((insertion_row, group_rows, sub_cost_code_number))
                else:
                    logger.info(f"SubCostCode {sub_cost_code_number or 'None'}: no match found, will append at end")
                    rows_to_append.extend(group_rows)

            # Enqueue each insert as a separate outbox row so retries + dead-
            # letter isolation happen per group. No need for the bottom-to-top
            # ordering now — the worker drains rows one at a time, so each
            # `insert_excel_rows` call sees the state resulting from all prior
            # completed rows. Sort ascending for deterministic dispatch order.
            insert_groups.sort(key=lambda g: g[0])

            from integrations.ms.outbox.business.service import MsOutboxService
            ms_outbox = MsOutboxService()

            for insertion_row, group_rows, sub_cost_code_number in insert_groups:
                queued = ms_outbox.enqueue_excel_insert(
                    entity_type="Bill",
                    entity_public_id=str(bill.public_id),
                    drive_id=drive.drive_id,
                    item_id=driveitem.item_id,
                    worksheet_name=worksheet_name,
                    row_index=insertion_row,
                    values=group_rows,
                    session_id=None,  # outbox-driven drain: no shared session
                )
                if queued is not None:
                    synced_count += len(group_rows)
                    logger.info(
                        f"Queued Excel insert of {len(group_rows)} row(s) at row "
                        f"{insertion_row} for SubCostCode {sub_cost_code_number} "
                        f"(outbox {queued.public_id})"
                    )
                else:
                    # Queueing refused (ALLOW_MS_WRITES=false) or hard failure.
                    errors.append({
                        "sub_cost_code": sub_cost_code_number,
                        "error": "Excel insert enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)"
                    })

            # Append any rows that didn't have matching SubCostCodes
            if rows_to_append:
                logger.info(f"Queuing append of {len(rows_to_append)} row(s) to end of worksheet")
                queued = ms_outbox.enqueue_excel_append(
                    entity_type="Bill",
                    entity_public_id=str(bill.public_id),
                    drive_id=drive.drive_id,
                    item_id=driveitem.item_id,
                    worksheet_name=worksheet_name,
                    values=rows_to_append,
                    session_id=None,
                )
                if queued is not None:
                    synced_count += len(rows_to_append)
                    logger.info(f"Queued Excel append of {len(rows_to_append)} row(s) (outbox {queued.public_id})")
                else:
                    errors.append({"error": "Excel append enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)"})
            
            if synced_count == 0 and not errors:
                return {
                    "success": True,
                    "message": "No rows to sync",
                    "synced_count": 0,
                    "errors": errors
                }

            logger.info(f"Queued {synced_count} row(s) for Excel workbook sync")

            has_errors = len(errors) > 0
            return {
                "success": synced_count > 0 or not has_errors,
                "message": f"Queued {synced_count} row(s) for Excel sync",
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


    def sync_bills_batch_to_excel(self, bill_line_pairs: List[tuple], project_id: int) -> dict:
        """
        Batch sync line items from multiple bills to one project's Excel workbook.
        Single worksheet read + batched outbox insert per project, regardless of how
        many bills contribute line items. Used by the QBO pull sync script.

        The single-bill ``sync_to_excel_workbook`` remains for ``complete_bill``.

        Args:
            bill_line_pairs: list of (bill, [line_items]) tuples
            project_id: target project ID

        Returns:
            dict with success, synced_count, errors
        """
        session_id = None
        try:
            if not bill_line_pairs:
                return {"success": True, "message": "No bills to sync", "synced_count": 0, "errors": []}

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

            # --- resolve vendors for all bills (cached) ---
            vendor_cache = {}
            for bill, _ in bill_line_pairs:
                if bill.vendor_id and bill.vendor_id not in vendor_cache:
                    vendor_cache[bill.vendor_id] = self.vendor_service.read_by_id(id=bill.vendor_id)
            scc_cache = {}  # memoize SubCostCode reads across all line items in this batch

            session_id = create_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id)

            # --- read worksheet once ---
            logger.info(f"Reading worksheet '{worksheet_name}' for project {project_id} (batch: {len(bill_line_pairs)} bill(s))")
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

            # --- build rows for every (bill, line_item) pair ---
            errors = []
            all_new_rows = []  # (scc_number, row)
            for bill, line_items in bill_line_pairs:
                vendor = vendor_cache.get(bill.vendor_id)
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
                        # N: prefer Price; fall back to Amount when Price is NULL so QBO-pulled
                        # account-based lines (which often carry no Price) don't land as $0.
                        if li.price is not None:
                            n_value = float(li.price)
                        elif getattr(li, "amount", None) is not None:
                            n_value = float(li.amount)
                        else:
                            n_value = 0
                        row = [
                            "",                                                          # A
                            cc_number,                                                   # B: CostCode
                            scc_number,                                                  # C: SubCostCode
                            "", "", "", "", "",                                          # D-H
                            bill.bill_date[:10] if bill.bill_date else "",               # I: Date
                            vendor_name,                                                 # J: Vendor
                            bill.bill_number or "",                                      # K: Bill #
                            li.description or "",                                         # L: Description
                            "Bill",                                                      # M: Type
                            n_value,                                                     # N: Price (Amount fallback)
                            "", "", "", "", "", "", "", "", "", "", "",                   # O-Y
                            pid,                                                         # Z: Reconciliation key
                        ]
                        all_new_rows.append((scc_number, row))
                    except Exception as e:
                        logger.error(f"Error building Excel row for BillLineItem {li.id}: {e}")
                        errors.append({"line_item_id": li.id, "error": str(e)})

            if not all_new_rows:
                logger.info(f"Project {project_id}: all bill line items already in worksheet, nothing to sync")
                return {"success": True, "message": "All rows already synced", "synced_count": 0, "errors": []}

            logger.info(f"Project {project_id}: {len(all_new_rows)} new bill row(s) to sync")

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
                    entity_type="BillBatch",
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
                    entity_type="BillBatch",
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

            logger.info(f"Project {project_id}: synced {synced_count} bill row(s) to Excel workbook")
            has_errors = len(errors) > 0
            return {
                "success": synced_count > 0 or not has_errors,
                "message": f"Synced {synced_count} row(s) to Excel workbook",
                "synced_count": synced_count,
                "errors": errors,
            }

        except Exception as e:
            logger.exception(f"Error batch-syncing bills to Excel workbook for project {project_id}")
            return {"success": False, "message": f"Error syncing to Excel: {str(e)}", "synced_count": 0, "errors": [{"error": str(e)}]}
        finally:
            if session_id:
                close_workbook_session(drive_id=drive.drive_id, item_id=driveitem.item_id, session_id=session_id)

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
            # Get the Bills module — fail fast if not found
            module = self.module_service.read_by_name("Bills")
            if not module:
                module = self.module_service.read_by_name("Bill")

            if not module:
                return {
                    "success": False,
                    "message": "Bills module not found. Create a module named 'Bills' before syncing.",
                    "synced_count": 0,
                    "errors": [{"error": "Bills module not found"}]
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

            logger.info(f"SharePoint sync: Processing {len(line_items)} line items for project {project_id}")

            # Process each line item
            for line_item in line_items:
                try:
                    if not line_item.public_id:
                        continue

                    # Get attachment link
                    attachment_link = self.bill_line_item_attachment_service.read_by_bill_line_item_id(
                        bill_line_item_public_id=line_item.public_id
                    )

                    if not attachment_link or not attachment_link.attachment_id:
                        logger.debug(f"No attachment for line item {line_item.public_id}")
                        continue

                    logger.debug(f"Found attachment for line item {line_item.public_id}, attachment_id={attachment_link.attachment_id}")
                    
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
                            price = f"${Decimal(str(line_item.price)):,.2f}"
                        except Exception:
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
                                amount_str = f"${Decimal(str(bill.total_amount)):,.2f}"
                            except Exception:
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
                    
                    # Determine content_type (needed by the worker to set upload
                    # Content-Type header). We trust `attachment.content_type`
                    # first; the worker does not re-read blob metadata.
                    # TODO(Phase 3.x): inline compression moved to TODO. The
                    # worker currently uploads the raw blob. Compression gain
                    # was 20-30% on image-heavy PDFs; acceptable loss for the
                    # Phase 3 outbox win.
                    content_type = attachment.content_type or "application/octet-stream"

                    # Enqueue SharePoint upload. Worker fetches blob at drain
                    # time, uploads, and links the resulting DriveItem back
                    # to the Attachment record (via `attachment_id` in payload).
                    from integrations.ms.outbox.business.service import MsOutboxService
                    queued = MsOutboxService().enqueue_sharepoint_upload(
                        entity_type="Bill",
                        entity_public_id=str(bill.public_id),
                        drive_id=drive.drive_id,
                        parent_item_id=folder_item_id,
                        filename=sharepoint_filename,
                        content_type=content_type,
                        blob_path=attachment.blob_url,
                        attachment_id=attachment.id,
                    )
                    if queued is None:
                        logger.error(f"SharePoint upload enqueue refused for '{sharepoint_filename}'")
                        errors.append({
                            "line_item_id": line_item.id,
                            "line_item_public_id": line_item.public_id,
                            "error": "SharePoint upload enqueue refused (ALLOW_MS_WRITES=false or enqueue failure)"
                        })
                        continue

                    uploaded_attachments[attachment_link.attachment_id] = sharepoint_filename
                    synced_count += 1
                    logger.info(
                        f"Queued SharePoint upload: '{sharepoint_filename}' "
                        f"(outbox {queued.public_id}, attachment_id={attachment.id})"
                    )
                    
                except Exception as e:
                    logger.exception(f"Error processing line item {line_item.id}")
                    errors.append({
                        "line_item_id": line_item.id,
                        "line_item_public_id": line_item.public_id,
                        "error": f"Unexpected error: {str(e)}"
                    })
            
            success = synced_count > 0 or len(errors) == 0
            message = f"Queued {synced_count} file(s) for SharePoint upload"
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
