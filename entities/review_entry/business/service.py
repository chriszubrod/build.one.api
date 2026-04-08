# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.review_entry.business.model import ReviewEntry
from entities.review_entry.persistence.repo import ReviewEntryRepository

logger = logging.getLogger(__name__)


class ReviewEntryService:
    """
    Service for ReviewEntry entity business operations.
    Handles status transition enforcement and review workflow logic.
    """

    def __init__(self, repo: Optional[ReviewEntryRepository] = None, status_service=None):
        """Initialize the ReviewEntryService."""
        self.repo = repo or ReviewEntryRepository()
        self._status_service = status_service
        self._bill_service = None

    @property
    def status_service(self):
        if self._status_service is None:
            from entities.review_status.business.service import ReviewStatusService
            self._status_service = ReviewStatusService()
        return self._status_service

    @property
    def bill_service(self):
        if self._bill_service is None:
            from entities.bill.business.service import BillService
            self._bill_service = BillService()
        return self._bill_service

    # =========================================================================
    # Standard CRUD (for workflow engine compatibility)
    # =========================================================================

    def create(
        self,
        *,
        tenant_id: int = None,
        review_status_public_id: Optional[str] = None,
        bill_public_id: Optional[str] = None,
        user_id: Optional[int] = None,
        comments: Optional[str] = None,
    ) -> ReviewEntry:
        """
        Create a review entry directly (for workflow engine / admin use).
        """
        review_status_id = None
        if review_status_public_id:
            status = self.status_service.read_by_public_id(review_status_public_id)
            if not status:
                raise ValueError(f"Review status not found: {review_status_public_id}")
            review_status_id = status.id

        bill_id = None
        if bill_public_id:
            bill = self.bill_service.read_by_public_id(bill_public_id)
            if not bill:
                raise ValueError(f"Bill not found: {bill_public_id}")
            bill_id = bill.id

        return self.repo.create(
            review_status_id=review_status_id,
            bill_id=bill_id,
            user_id=user_id,
            comments=comments,
        )

    def read_all(self) -> list[ReviewEntry]:
        """
        Read all review entries.
        """
        return self.repo.read_all()

    def read_by_public_id(self, public_id: str) -> Optional[ReviewEntry]:
        """
        Read a review entry by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_bill_id(self, bill_id: int) -> list[ReviewEntry]:
        """
        Read all review entries for a bill.
        """
        return self.repo.read_by_bill_id(bill_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        comments: str = None,
    ) -> Optional[ReviewEntry]:
        """
        Update a review entry (only Comments is editable).
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if comments is not None:
                existing.comments = comments
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[ReviewEntry]:
        """
        Delete a review entry by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None

    def delete_by_bill_id(self, bill_id: int) -> None:
        """
        Delete all review entries for a bill (cascade cleanup).
        """
        self.repo.delete_by_bill_id(bill_id)

    # =========================================================================
    # Review Workflow Actions
    # =========================================================================

    def submit_for_review(
        self,
        *,
        bill_public_id: str,
        user_id: int,
        comments: Optional[str] = None,
    ) -> ReviewEntry:
        """
        Submit a bill for review. Creates the first ReviewEntry with the
        initial status (lowest SortOrder, non-declined, active).
        """
        bill = self.bill_service.read_by_public_id(bill_public_id)
        if not bill:
            raise ValueError(f"Bill not found: {bill_public_id}")

        first_status = self.status_service.get_first_status()
        if not first_status:
            raise ValueError("No active review statuses configured. Create review statuses first.")

        return self.repo.create(
            review_status_id=first_status.id,
            bill_id=bill.id,
            user_id=user_id,
            comments=comments,
        )

    def advance_status(
        self,
        *,
        bill_public_id: str,
        user_id: int,
        comments: Optional[str] = None,
    ) -> ReviewEntry:
        """
        Advance a bill's review to the next status in sort order.
        Validates that the current status is not final or declined.
        """
        bill = self.bill_service.read_by_public_id(bill_public_id)
        if not bill:
            raise ValueError(f"Bill not found: {bill_public_id}")

        latest = self.repo.read_latest_by_bill_id(bill.id)
        if not latest:
            raise ValueError("Bill has not been submitted for review yet.")

        if latest.status_is_final:
            raise ValueError(f"Cannot advance from final status '{latest.status_name}'.")

        if latest.status_is_declined:
            raise ValueError(f"Cannot advance from declined status '{latest.status_name}'. Resubmit for review instead.")

        next_status = self.status_service.get_next_status(latest.status_sort_order)
        if not next_status:
            raise ValueError("No further status to advance to.")

        return self.repo.create(
            review_status_id=next_status.id,
            bill_id=bill.id,
            user_id=user_id,
            comments=comments,
        )

    def decline(
        self,
        *,
        bill_public_id: str,
        review_status_public_id: str,
        user_id: int,
        comments: Optional[str] = None,
    ) -> ReviewEntry:
        """
        Decline a bill's review. The target status must have is_declined=True.
        """
        bill = self.bill_service.read_by_public_id(bill_public_id)
        if not bill:
            raise ValueError(f"Bill not found: {bill_public_id}")

        latest = self.repo.read_latest_by_bill_id(bill.id)
        if not latest:
            raise ValueError("Bill has not been submitted for review yet.")

        if latest.status_is_final:
            raise ValueError(f"Cannot decline from final status '{latest.status_name}'.")

        if latest.status_is_declined:
            raise ValueError(f"Bill is already declined with status '{latest.status_name}'.")

        declined_status = self.status_service.read_by_public_id(review_status_public_id)
        if not declined_status:
            raise ValueError(f"Review status not found: {review_status_public_id}")

        if not declined_status.is_declined:
            raise ValueError(f"Status '{declined_status.name}' is not a declined status.")

        return self.repo.create(
            review_status_id=declined_status.id,
            bill_id=bill.id,
            user_id=user_id,
            comments=comments,
        )

    # =========================================================================
    # Status Query Methods
    # =========================================================================

    def get_current_status(self, *, bill_public_id: str) -> Optional[ReviewEntry]:
        """
        Get the current (latest) review entry for a bill.
        """
        bill = self.bill_service.read_by_public_id(bill_public_id)
        if not bill:
            return None
        return self.repo.read_latest_by_bill_id(bill.id)

    def get_timeline(self, *, bill_public_id: str) -> list[ReviewEntry]:
        """
        Get the full review timeline for a bill, ordered by CreatedDatetime DESC.
        """
        bill = self.bill_service.read_by_public_id(bill_public_id)
        if not bill:
            return []
        return self.repo.read_by_bill_id(bill.id)

    def is_approved(self, *, bill_id: int) -> bool:
        """
        Check if a bill's review is in an approved (final, non-declined) state.
        Returns False if no review entries exist.
        """
        latest = self.repo.read_latest_by_bill_id(bill_id)
        if not latest:
            return False
        return latest.status_is_final is True and latest.status_is_declined is not True
