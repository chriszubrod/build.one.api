# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.review.business.model import ParentType, Review
from entities.review.persistence.repo import ReviewRepository
from entities.review_status.business.service import ReviewStatusService


class ReviewTransitionError(Exception):
    """
    Raised when a review state transition is invalid for the parent's
    current review state. Routers map this to HTTP 409 Conflict.
    """
    pass


class ParentNotFoundError(Exception):
    """
    Raised when a referenced parent entity (Bill / Expense / BillCredit /
    Invoice) cannot be resolved. Routers map this to HTTP 404 Not Found.
    """
    pass


class ReviewService:
    """
    Service for Review entity business operations.

    Reviews are an insert-only audit trail of state transitions on
    transactional documents (Bill, Expense, BillCredit, Invoice). The
    "current" review state of a parent is the latest row by CreatedDatetime.

    Transition rules:
      - Submit:  no current row, OR current row is in a declined status.
      - Advance: current row exists, is non-final, and is non-declined.
                 Target = next ReviewStatus by SortOrder.
      - Decline: current row exists and is non-final. Target may be
                 specified explicitly via target_status_public_id, or
                 auto-resolved if exactly one declined status is configured.
    """

    def __init__(
        self,
        repo: Optional[ReviewRepository] = None,
        review_status_service: Optional[ReviewStatusService] = None,
    ):
        self.repo = repo or ReviewRepository()
        self.review_status_service = review_status_service or ReviewStatusService()

    # =========================================================================
    # Standard CRUD entry — invoked by ProcessEngine via SERVICE_REGISTRY when
    # workflow_type='review_create'. The router builds the payload using the
    # build_*_payload helpers below; ProcessEngine forwards it here.
    # =========================================================================

    def create(
        self,
        *,
        tenant_id: int = None,
        review_status_id: int,
        user_id: int,
        comments: Optional[str] = None,
        bill_id: Optional[int] = None,
        expense_id: Optional[int] = None,
        bill_credit_id: Optional[int] = None,
        invoice_id: Optional[int] = None,
    ) -> Review:
        return self.repo.create(
            review_status_id=review_status_id,
            user_id=user_id,
            comments=comments,
            bill_id=bill_id,
            expense_id=expense_id,
            bill_credit_id=bill_credit_id,
            invoice_id=invoice_id,
        )

    # =========================================================================
    # Read helpers — parent-keyed
    # =========================================================================

    def read_by_public_id(self, public_id: str) -> Optional[Review]:
        return self.repo.read_by_public_id(public_id)

    def list_for(self, parent_type: str, parent_public_id: str) -> list[Review]:
        parent_id = self._resolve_parent_id(parent_type, parent_public_id)
        if parent_type == ParentType.BILL:
            return self.repo.read_by_bill_id(parent_id)
        if parent_type == ParentType.EXPENSE:
            return self.repo.read_by_expense_id(parent_id)
        if parent_type == ParentType.BILL_CREDIT:
            return self.repo.read_by_bill_credit_id(parent_id)
        if parent_type == ParentType.INVOICE:
            return self.repo.read_by_invoice_id(parent_id)
        raise ValueError(f"Unknown parent_type: {parent_type}")

    def get_current(self, parent_type: str, parent_public_id: str) -> Optional[Review]:
        parent_id = self._resolve_parent_id(parent_type, parent_public_id)
        return self._get_current_by_id(parent_type, parent_id)

    def is_approved(self, parent_type: str, parent_public_id: str) -> bool:
        current = self.get_current(parent_type, parent_public_id)
        if current is None:
            return False
        return bool(current.status_is_final) and not bool(current.status_is_declined)

    # =========================================================================
    # Transition payload builders — return ready-to-execute create payloads
    # for the router to feed into ProcessEngine. They do NOT write to the DB.
    # =========================================================================

    def build_submit_payload(
        self,
        *,
        parent_type: str,
        parent_public_id: str,
        user_id: int,
        comments: Optional[str] = None,
    ) -> dict:
        parent_id = self._resolve_parent_id(parent_type, parent_public_id)
        current = self._get_current_by_id(parent_type, parent_id)

        if current is not None and not current.status_is_declined:
            if current.status_is_final:
                raise ReviewTransitionError(
                    "Cannot submit: review is already approved."
                )
            raise ReviewTransitionError(
                "Cannot submit: a review is already in progress."
            )

        first = self.review_status_service.get_first_status()
        if first is None:
            raise ReviewTransitionError(
                "No initial review status is configured."
            )

        return self._build_payload(
            parent_type=parent_type,
            parent_id=parent_id,
            review_status_id=first.id,
            user_id=user_id,
            comments=comments,
        )

    def build_advance_payload(
        self,
        *,
        parent_type: str,
        parent_public_id: str,
        user_id: int,
        comments: Optional[str] = None,
    ) -> dict:
        parent_id = self._resolve_parent_id(parent_type, parent_public_id)
        current = self._get_current_by_id(parent_type, parent_id)

        if current is None:
            raise ReviewTransitionError(
                "Cannot advance: no review in progress. Submit first."
            )
        if current.status_is_final:
            raise ReviewTransitionError(
                "Cannot advance: review is already at a final status."
            )
        if current.status_is_declined:
            raise ReviewTransitionError(
                "Cannot advance: review is declined. Resubmit before advancing."
            )

        nxt = self.review_status_service.get_next_status(current.status_sort_order)
        if nxt is None:
            raise ReviewTransitionError(
                "Cannot advance: no next review status is configured."
            )

        return self._build_payload(
            parent_type=parent_type,
            parent_id=parent_id,
            review_status_id=nxt.id,
            user_id=user_id,
            comments=comments,
        )

    def build_decline_payload(
        self,
        *,
        parent_type: str,
        parent_public_id: str,
        user_id: int,
        target_status_public_id: Optional[str] = None,
        comments: Optional[str] = None,
    ) -> dict:
        parent_id = self._resolve_parent_id(parent_type, parent_public_id)
        current = self._get_current_by_id(parent_type, parent_id)

        if current is None:
            raise ReviewTransitionError(
                "Cannot decline: no review in progress."
            )
        if current.status_is_final:
            raise ReviewTransitionError(
                "Cannot decline: review is already at a final status."
            )
        if current.status_is_declined:
            raise ReviewTransitionError(
                "Cannot decline: review is already declined."
            )

        target = self._resolve_decline_target(target_status_public_id)

        return self._build_payload(
            parent_type=parent_type,
            parent_id=parent_id,
            review_status_id=target.id,
            user_id=user_id,
            comments=comments,
        )

    # =========================================================================
    # Internals
    # =========================================================================

    def _resolve_parent_id(self, parent_type: str, parent_public_id: str) -> int:
        """
        Translate a parent's public_id (UNIQUEIDENTIFIER) to its internal id
        (BIGINT). Lazy imports to avoid circular dependencies.
        """
        if parent_type == ParentType.BILL:
            from entities.bill.business.service import BillService
            existing = BillService().read_by_public_id(public_id=parent_public_id)
        elif parent_type == ParentType.EXPENSE:
            from entities.expense.business.service import ExpenseService
            existing = ExpenseService().read_by_public_id(public_id=parent_public_id)
        elif parent_type == ParentType.BILL_CREDIT:
            from entities.bill_credit.business.service import BillCreditService
            existing = BillCreditService().read_by_public_id(public_id=parent_public_id)
        elif parent_type == ParentType.INVOICE:
            from entities.invoice.business.service import InvoiceService
            existing = InvoiceService().read_by_public_id(public_id=parent_public_id)
        else:
            raise ValueError(f"Unknown parent_type: {parent_type}")

        if existing is None:
            raise ParentNotFoundError(
                f"{parent_type} with public_id {parent_public_id} not found."
            )
        return existing.id

    def _get_current_by_id(self, parent_type: str, parent_id: int) -> Optional[Review]:
        if parent_type == ParentType.BILL:
            return self.repo.read_current_by_bill_id(parent_id)
        if parent_type == ParentType.EXPENSE:
            return self.repo.read_current_by_expense_id(parent_id)
        if parent_type == ParentType.BILL_CREDIT:
            return self.repo.read_current_by_bill_credit_id(parent_id)
        if parent_type == ParentType.INVOICE:
            return self.repo.read_current_by_invoice_id(parent_id)
        raise ValueError(f"Unknown parent_type: {parent_type}")

    def _build_payload(
        self,
        *,
        parent_type: str,
        parent_id: int,
        review_status_id: int,
        user_id: int,
        comments: Optional[str],
    ) -> dict:
        payload = {
            "review_status_id": review_status_id,
            "user_id": user_id,
            "comments": comments,
            "bill_id": None,
            "expense_id": None,
            "bill_credit_id": None,
            "invoice_id": None,
        }
        fk_field = {
            ParentType.BILL:        "bill_id",
            ParentType.EXPENSE:     "expense_id",
            ParentType.BILL_CREDIT: "bill_credit_id",
            ParentType.INVOICE:     "invoice_id",
        }[parent_type]
        payload[fk_field] = parent_id
        return payload

    def _resolve_decline_target(self, target_status_public_id: Optional[str]):
        """
        Resolve which ReviewStatus to record on a decline action.

        - If caller supplied target_status_public_id: validate it exists and
          is a declined status.
        - If omitted: auto-pick the only configured declined status. Error
          if zero or more than one is configured (caller must specify).
        """
        if target_status_public_id is not None:
            target = self.review_status_service.read_by_public_id(target_status_public_id)
            if target is None:
                raise ReviewTransitionError(
                    f"Target review status {target_status_public_id} not found."
                )
            if not target.is_declined:
                raise ReviewTransitionError(
                    f"Target review status '{target.name}' is not a declined status."
                )
            return target

        declined = self.review_status_service.get_declined_statuses()
        if not declined:
            raise ReviewTransitionError(
                "No declined review status is configured."
            )
        if len(declined) > 1:
            raise ReviewTransitionError(
                "Multiple declined review statuses are configured; "
                "specify target_status_public_id."
            )
        return declined[0]
