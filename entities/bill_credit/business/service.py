# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit.persistence.repo import BillCreditRepository
from entities.vendor.business.service import VendorService
from shared.access import assert_can_access_bill_credit
from shared.authz import current_user_id, current_is_system_admin

logger = logging.getLogger(__name__)


def _clear_legacy_vendorcredit_billcredit_mapping(bill_credit_id: int) -> None:
    """U-353 deploy-gap bridge for BillCreditService.delete_by_public_id — see its
    call site for the full rationale. Raw SQL, not a repo/model (both retired):
    deletes any row in the (soon-to-be-dropped) qbo.VendorCreditBillCredit table
    that still points at this BillCredit, so its NO_ACTION FK never blocks the
    header delete below.

    The `IF OBJECT_ID(...) IS NOT NULL` guard (code-review Angle G/round-2,
    matching the same idiom the base .sql file's own `CREATE TABLE` guard
    already uses) makes the table-already-dropped case — the normal state once
    /em applies the DROP — a plain no-op evaluated by SQL Server itself, NOT a
    caught Python exception: no substring-matching on driver error text, which
    this codebase has already been burned by once (see
    shared/database.py::map_database_error's own comment on why SQL 547's
    error text can't be substring-matched safely, and shared/db_constraints.py's
    `_has_error_number`). Once /em applies the DROP, this whole function
    becomes a permanent no-op and should be deleted."""
    from shared.database import get_connection

    try:
        with get_connection() as conn:
            conn.cursor().execute(
                "IF OBJECT_ID('qbo.VendorCreditBillCredit', 'U') IS NOT NULL "
                "DELETE FROM [qbo].[VendorCreditBillCredit] WHERE [BillCreditId] = ?",
                (bill_credit_id,),
            )
    except Exception as e:
        # Only reachable now for a genuine unexpected failure (connection reset,
        # deadlock, permissions) — table-missing no longer raises at all. Logged,
        # not raised: best-effort only. The real safety net is the FK itself — if
        # a mapping row really does still exist and this failed to clear it, the
        # header delete below 547s anyway (fail-safe, not fail-silent-corruption).
        logger.warning(
            f"Could not clear legacy qbo.VendorCreditBillCredit mapping for "
            f"BillCredit {bill_credit_id}: {e}"
        )


class BillCreditService:
    """
    Service for BillCredit entity business operations.
    """

    def __init__(self, repo: Optional[BillCreditRepository] = None):
        """Initialize the BillCreditService."""
        self.repo = repo or BillCreditRepository()

    def create(self, *, tenant_id: int = 1, vendor_public_id: str, credit_date: str, credit_number: str, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> BillCredit:
        """
        Create a new bill credit.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            vendor_public_id: Vendor public ID (required)
            credit_date: Credit date
            credit_number: Credit number
            total_amount: Total amount (optional)
            memo: Memo (optional)
            is_draft: Whether bill credit is in draft state
        """
        if not vendor_public_id:
            raise ValueError("Vendor is required.")
        if not credit_date:
            raise ValueError("Credit date is required.")
        if not credit_number:
            raise ValueError("Credit number is required.")
        
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
        vendor_id = vendor.id
        
        # Check if a bill credit with the same CreditNumber and VendorId already exists
        existing = self.repo.read_by_credit_number_and_vendor_id(credit_number=credit_number, vendor_id=vendor_id)
        if existing:
            raise ValueError(f"A bill credit with CreditNumber '{credit_number}' already exists for this vendor. Please update the existing bill credit instead of creating a new one.")
        
        return self.repo.create(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            credit_date=credit_date,
            credit_number=credit_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=is_draft,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[BillCredit]:
        """Read bill credits, scoped by UserProject for non-admin actors."""
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
        sort_by: str = "CreditDate",
        sort_direction: str = "DESC",
    ) -> list[BillCredit]:
        """Read bill credits with pagination + filters, scoped by UserProject."""
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
    ) -> int:
        """Count bill credits matching filter criteria, scoped by UserProject."""
        return self.repo.count(
            search_term=search_term,
            vendor_id=vendor_id,
            start_date=start_date,
            end_date=end_date,
            is_draft=is_draft,
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def read_by_id(self, id: int) -> Optional[BillCredit]:
        """
        Read a bill credit by ID.
        """
        bill_credit = self.repo.read_by_id(id)
        if bill_credit is None:
            return None
        assert_can_access_bill_credit(bill_credit.id)
        return bill_credit

    def read_by_public_id(self, public_id: str) -> Optional[BillCredit]:
        """
        Read a bill credit by public ID.
        """
        bill_credit = self.repo.read_by_public_id(public_id)
        if bill_credit is None:
            return None
        assert_can_access_bill_credit(bill_credit.id)
        return bill_credit

    def read_by_credit_number_and_vendor_public_id(self, credit_number: str, vendor_public_id: str) -> Optional[BillCredit]:
        """
        Read a bill credit by credit number and vendor public ID.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            return None
        bill_credit = self.repo.read_by_credit_number_and_vendor_id(credit_number=credit_number, vendor_id=vendor.id)
        if bill_credit is None:
            return None
        assert_can_access_bill_credit(bill_credit.id)
        return bill_credit

    def read_by_qbo_identity(self, qbo_id: str, realm_id: Optional[str] = None) -> Optional[BillCredit]:
        """
        Read a bill credit directly by its dbo-native QBO identity (U-278) — the
        Phase-4 repoint seam, bypassing the qbo.VendorCredit staging table.
        """
        bill_credit = self.repo.read_by_qbo_identity(qbo_id, realm_id)
        if bill_credit is None:
            return None
        assert_can_access_bill_credit(bill_credit.id)
        return bill_credit

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        vendor_public_id: str = None,
        credit_date: str = None,
        credit_number: str = None,
        total_amount: float = None,
        memo: str = None,
        is_draft: bool = None,
    ) -> Optional[BillCredit]:
        """
        Update a bill credit by public ID.
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
        
        if credit_date is not None:
            existing.credit_date = credit_date
        if credit_number is not None:
            existing.credit_number = credit_number
        if total_amount is not None:
            existing.total_amount = Decimal(str(total_amount))
        if memo is not None:
            existing.memo = memo
        if is_draft is not None:
            existing.is_draft = is_draft
        
        updated_bill_credit = self.repo.update_by_id(existing)
        
        return updated_bill_credit

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[BillCredit]:
        """
        Delete a bill credit by public ID with cascading deletes.
        
        TODO: In Phase 10, validate tenant_id matches record's tenant
        
        Process:
        1. Get the bill credit by public_id
        2. Get all BillCreditLineItems for this bill credit
        3. For each BillCreditLineItem:
           a. Get its BillCreditLineItemAttachment (1-1 relationship)
           b. If attachment exists:
              - Get the Attachment record
              - Delete the file from Azure Blob Storage (if blob_url exists)
              - Delete the Attachment record from database
              - Delete the BillCreditLineItemAttachment record
           c. Delete the BillCreditLineItem record
        4. Delete the BillCredit record
        """
        # Import here to avoid circular import
        from entities.bill_credit_line_item.business.service import BillCreditLineItemService
        from entities.bill_credit_line_item_attachment.business.service import BillCreditLineItemAttachmentService
        from entities.bill_credit_line_item_attachment.persistence.repo import BillCreditLineItemAttachmentRepository
        from entities.attachment.business.service import AttachmentService
        from shared.storage import AzureBlobStorage, AzureBlobStorageError
        
        # Step 1: Get the bill credit
        existing = self.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None
        
        bill_credit_id = existing.id
        
        # Step 2: Get all BillCreditLineItems for this bill credit
        bill_credit_line_item_service = BillCreditLineItemService()
        bill_credit_line_items = bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id=bill_credit_id)
        
        # Step 3: Delete each BillCreditLineItem and its associated attachments
        bill_credit_line_item_attachment_service = BillCreditLineItemAttachmentService()
        bill_credit_line_item_attachment_repo = BillCreditLineItemAttachmentRepository()
        attachment_service = AttachmentService()
        
        # Initialize storage once (may fail if config is missing, handle gracefully)
        storage = None
        try:
            storage = AzureBlobStorage()
        except Exception as e:
            logger.warning(f"Could not initialize Azure Blob Storage for file deletion: {e}")
        
        for line_item in bill_credit_line_items:
            try:
                # Step 3a: Get the BillCreditLineItemAttachment for this line item (1-1 relationship)
                if line_item.public_id:
                    attachment_link = bill_credit_line_item_attachment_service.read_by_bill_credit_line_item_id(
                        bill_credit_line_item_public_id=line_item.public_id
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
                            
                            # Delete the BillCreditLineItemAttachment record
                            if attachment_link.id:
                                try:
                                    bill_credit_line_item_attachment_repo.delete_by_id(id=attachment_link.id)
                                    logger.info(f"Deleted bill credit line item attachment {attachment_link.id}")
                                except Exception as e:
                                    logger.warning(f"Error deleting bill credit line item attachment {attachment_link.id}: {e}")
                        except Exception as e:
                            logger.warning(f"Error processing attachment for line item {line_item.id}: {e}")
                
                # Step 3c: Delete the BillCreditLineItem record
                if line_item.id and line_item.public_id:
                    try:
                        bill_credit_line_item_service.delete_by_public_id(public_id=line_item.public_id)
                        logger.info(f"Deleted bill credit line item {line_item.id}")
                    except Exception as e:
                        logger.warning(f"Error deleting bill credit line item {line_item.id}: {e}")
                elif line_item.id:
                    # Fallback: delete directly by ID if public_id is missing
                    try:
                        from entities.bill_credit_line_item.persistence.repo import BillCreditLineItemRepository
                        bill_credit_line_item_repo = BillCreditLineItemRepository()
                        bill_credit_line_item_repo.delete_by_id(id=line_item.id)
                        logger.info(f"Deleted bill credit line item {line_item.id} (by ID, no public_id)")
                    except Exception as e:
                        logger.warning(f"Error deleting bill credit line item {line_item.id} by ID: {e}")
            except Exception as e:
                logger.warning(f"Error processing bill credit line item {line_item.id if line_item.id else 'unknown'}: {e}")
        
        # U-353: dbo.BillCredit.QboId/RealmId (U-278) is the sole QBO identity store
        # going forward — the connector no longer creates qbo.VendorCreditBillCredit
        # rows. But builders never apply prod DDL (see repo CLAUDE.md); the DROP TABLE
        # for this retired mapping table is handed to /em and lands AFTER this code
        # deploys, not atomically with it. Until that DROP is applied, a historical
        # BillCredit synced before this unit may still have a legacy mapping row
        # pointing at it via a NO_ACTION FK (U-226) — deleting the header without
        # clearing it first would 547. Best-effort bridge: no repo/model (those are
        # retired), just enough to unblock the header delete during the deploy gap;
        # delete this call once /em confirms the DROP has landed. Deliberately does
        # NOT restore the mapping row on a header-delete failure (unlike the old
        # delete_own_qbo_mapping_before_header this replaced) — dbo.BillCredit.QboId
        # is the sole identity store now, so a lost legacy mapping row no longer
        # orphans identity resolution the way losing it once could.
        _clear_legacy_vendorcredit_billcredit_mapping(existing.id)
        return self.repo.delete_by_id(existing.id)
