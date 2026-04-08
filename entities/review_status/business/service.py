# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.review_status.business.model import ReviewStatus
from entities.review_status.persistence.repo import ReviewStatusRepository


class ReviewStatusService:
    """
    Service for ReviewStatus entity business operations.
    """

    def __init__(self, repo: Optional[ReviewStatusRepository] = None):
        """Initialize the ReviewStatusService."""
        self.repo = repo or ReviewStatusRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        name: Optional[str],
        description: Optional[str] = None,
        sort_order: int = 0,
        is_final: bool = False,
        is_declined: bool = False,
        is_active: bool = True,
        color: Optional[str] = None,
    ) -> ReviewStatus:
        """
        Create a new review status.
        """
        return self.repo.create(
            name=name,
            description=description,
            sort_order=sort_order,
            is_final=is_final,
            is_declined=is_declined,
            is_active=is_active,
            color=color,
        )

    def read_all(self) -> list[ReviewStatus]:
        """
        Read all review statuses, ordered by SortOrder.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[ReviewStatus]:
        """
        Read a review status by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[ReviewStatus]:
        """
        Read a review status by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def get_next_status(self, current_sort_order: int) -> Optional[ReviewStatus]:
        """
        Get the next active, non-declined review status after the given sort order.
        """
        return self.repo.read_next(current_sort_order)

    def get_first_status(self) -> Optional[ReviewStatus]:
        """
        Get the first active, non-declined review status (initial submission status).
        """
        return self.repo.read_first()

    def get_declined_statuses(self) -> list[ReviewStatus]:
        """
        Get all active declined statuses.
        """
        all_statuses = self.repo.read_all()
        return [s for s in all_statuses if s.is_declined and s.is_active]

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        description: str = None,
        sort_order: int = None,
        is_final: bool = None,
        is_declined: bool = None,
        is_active: bool = None,
        color: str = None,
    ) -> Optional[ReviewStatus]:
        """
        Update a review status by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if description is not None:
                existing.description = description
            if sort_order is not None:
                existing.sort_order = sort_order
            if is_final is not None:
                existing.is_final = is_final
            if is_declined is not None:
                existing.is_declined = is_declined
            if is_active is not None:
                existing.is_active = is_active
            if color is not None:
                existing.color = color
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[ReviewStatus]:
        """
        Delete a review status by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
