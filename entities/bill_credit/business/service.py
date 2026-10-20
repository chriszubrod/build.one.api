"""Entity contract — BillCredit.   (maintained with the code)

Owns
  dbo.BillCredit vendor credit header; line items live in bill_credit_line_item.
Invariants
  RBAC: Modules.BILL_CREDITS; by-id paths assert_can_access_bill_credit.
  Delete: draft-only (IsDraft=1), no admin override; refuses QBO identity and
  invoice line citations (U-684a).
External writes
  Box/SharePoint on complete (outbox); none on delete (links only, no blobs).
Sprocs
  CountBillCredits
  CreateBillCredit
  DeleteBillCreditById
  DeleteBillCreditCascadeById
  ReadBillCreditByCreditNumberAndVendorId
  ReadBillCreditById
  ReadBillCreditByPublicId
  ReadBillCreditByQboIdAndRealmId
  ReadBillCredits
  ReadBillCreditsPaginated
  SetBillCreditQboIdentity
  UpdateBillCreditById
"""
# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit.persistence.repo import BillCreditRepository
from entities.vendor.business.service import VendorService
from shared.access import assert_can_access_bill_credit, can_access_bill_credit
from shared.authz import current_user_id, current_is_system_admin

from shared.lifecycle.terminal_lock import assert_editable

QBO_DELETE_REFUSAL = (
    "This bill credit came from QuickBooks; delete it in QuickBooks instead."
)
DELETE_CITATION_REFUSAL_PREFIX = "Cannot delete this bill credit:"

logger = logging.getLogger(__name__)


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
            if can_access_bill_credit(existing.id):
                raise ValueError(
                    f"A bill credit with CreditNumber '{credit_number}' already exists for this vendor. "
                    f"Please update the existing bill credit instead of creating a new one."
                )
            raise ValueError(
                "A bill credit with this number already exists for this vendor."
            )
        
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
        Delete a draft bill credit and every row that FKs to it, in ONE transaction.

        Cascades inside `DeleteBillCreditCascadeById` (header locked throughout):
          BillCreditLineItemAttachment links -> BillCreditLineItem rows ->
          Review -> the BillCredit itself.

        Non-draft credits and QBO-sourced credits are refused for everyone,
        including system admins. A credit cited by an InvoiceLineItem is refused
        with the citing invoice number.

        Attachment ROWS and Azure blobs are deliberately LEFT ALONE — only link
        rows go, so the delete path makes no external calls.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None

        assert_editable(
            status=getattr(existing, "status", None),
            is_draft=getattr(existing, "is_draft", None),
            what="it cannot be deleted",
            exempt=False,
        )

        if getattr(existing, "qbo_id", None):
            raise ValueError(QBO_DELETE_REFUSAL)

        deleted = self.repo.delete_cascade_by_id(existing.id)
        if deleted is not None:
            logger.info(f"Deleted bill credit {existing.id} and its dependent rows")
        return deleted
