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
from functools import partial
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit.persistence.repo import BillCreditRepository
from entities.vendor.business.service import VendorService
from shared.access import (
    EntityNotAccessibleError,
    assert_can_access_bill_credit,
    can_access_bill_credit,
)
from shared.authz import current_user_id, current_is_system_admin

from shared.lifecycle.terminal_lock import assert_editable
from shared.authz.delegation import assert_may_act_as
from shared.database import get_connection
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.review.business.decision_errors import review_decision_apply_error
from entities.review.business.reviewer_decision import ReviewerDecisionSpec, apply_emailed

QBO_DELETE_REFUSAL = (
    "This bill credit came from QuickBooks; delete it in QuickBooks instead."
)
DELETE_CITATION_REFUSAL_PREFIX = "Cannot delete this bill credit:"

logger = logging.getLogger(__name__)

_bill_credit_review_decision_apply_error = partial(
    review_decision_apply_error,
    parent="bill credit",
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

    def _reviewer_decision_spec(self) -> ReviewerDecisionSpec:
        """BillCredit's half of the shared reviewer-decision engine (U-797c4b).

        Mirrors Bill's spec. `kind` is the ReviewNotification / binder target
        ("bill_credit"); the prose noun is "bill credit". Built per call so
        `get_connection`, `SubCostCodeService`, `assert_may_act_as` and `logger`
        are read from THIS module at call time."""

        def _line_service():
            from entities.bill_credit_line_item.business.service import BillCreditLineItemService

            return BillCreditLineItemService()

        return ReviewerDecisionSpec(
            parent="bill_credit",
            kind="bill_credit",
            noun="bill credit",
            entity_type="BillCredit",
            provenance_what="a bill credit reviewer decision",
            read_parent=lambda public_id: self.read_by_public_id(public_id=public_id),
            resolve_recipients=lambda svc, bill_credit_id: svc.resolve_for_bill_credit(bill_credit_id=bill_credit_id),
            line_service_factory=_line_service,
            read_lines=lambda svc, bill_credit_id: svc.read_by_bill_credit_id(bill_credit_id=bill_credit_id),
            count_lines_for_update=lambda svc, bill_credit_id, conn: svc.count_by_bill_credit_id_for_update(bill_credit_id, conn=conn),
            lock_current_review=lambda repo, bill_credit_id, conn: repo.read_current_by_bill_credit_id_for_update(bill_credit_id, conn=conn),
            apply_error=_bill_credit_review_decision_apply_error,
            get_connection=get_connection,
            sub_cost_code_service=SubCostCodeService,
            assert_may_act_as=assert_may_act_as,
            logger=logger,
            emailed_fresh_status_service_for_readback=False,
        )

    def apply_reviewer_decision(
        self,
        *,
        bill_credit_public_id: str,
        decision: str,
        reviewer_email: str,
        sub_cost_code_public_id: Optional[str] = None,
        description: Optional[str] = None,
        raw_reply_text: Optional[str] = None,
        reviewer_email_message_public_id: Optional[str] = None,
    ) -> dict:
        """Apply a Project Manager / Owner's emailed review decision to a BillCredit.

        Same rules as Bill (U-755/U-760/U-761): the sender must be a PM/Owner on
        the credit's recipient envelope, the reply must be provenance-bound, the
        caller may act as that reviewer (U-459), the credit must still be a draft,
        and an approval codes the credit's ONE line (single-line only, not the
        holding code). One transaction covers the line recode and the Review
        insert. A BillCredit has no Status column, so nothing is mirrored onto it.
        Engine: `entities.review.business.reviewer_decision.apply_emailed`.
        """
        return apply_emailed(
            self._reviewer_decision_spec(),
            public_id=bill_credit_public_id,
            decision=decision,
            reviewer_email=reviewer_email,
            sub_cost_code_public_id=sub_cost_code_public_id,
            description=description,
            raw_reply_text=raw_reply_text,
            reviewer_email_message_public_id=reviewer_email_message_public_id,
        )

    def find_for_reviewer_reply(self, conversation_id: Optional[str]) -> Optional[BillCredit]:
        """The BillCredit an emailed reviewer reply targets (U-797c4b).

        Resolves through the ReviewNotification record. Only exactly one matching
        notification, of Kind "bill_credit", with exactly one BillCredit target,
        resolves; anything else is None. The credit is read through the
        access-checked read, so a caller who cannot see it gets None too."""
        from entities.review.business.review_notification_service import (
            ReviewNotificationRecordService,
        )

        matches = ReviewNotificationRecordService().lookup_for_reply(conversation_id)
        if len(matches) != 1:
            return None
        notification, targets = matches[0]
        if getattr(notification, "Kind", None) != "bill_credit":
            return None
        if len(targets) != 1:
            return None
        ((kind, bill_credit_id),) = targets
        if kind != "bill_credit":
            return None
        try:
            return self.read_by_id(bill_credit_id)
        except EntityNotAccessibleError:
            # Inaccessible == not found: the same null as a miss. Logged (id only) so
            # a service identity missing a UserProject row shows up in the logs.
            logger.warning(
                "find_for_reviewer_reply: matched BillCredit %s is not accessible to the caller — answering as no match",
                bill_credit_id,
            )
            return None

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
