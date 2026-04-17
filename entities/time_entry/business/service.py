# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.time_entry.business.model import TimeEntry
from entities.time_entry.persistence.repo import TimeEntryRepository
from entities.time_entry.persistence.time_entry_status_repo import TimeEntryStatusRepository
from entities.time_entry.persistence.time_log_repo import TimeLogRepository
from entities.user.business.service import UserService
from entities.project.business.service import ProjectService

logger = logging.getLogger(__name__)

# Valid status transitions
VALID_TRANSITIONS = {
    "draft": ["submitted"],
    "submitted": ["approved", "rejected"],
    "approved": ["billed"],
    "rejected": ["draft"],
    "billed": [],
}


class TimeEntryService:
    """
    Service for TimeEntry entity business operations.
    """

    def __init__(self, repo: Optional[TimeEntryRepository] = None):
        """Initialize the TimeEntryService."""
        self.repo = repo or TimeEntryRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        user_public_id: str,
        project_public_id: str,
        work_date: str,
        note: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> TimeEntry:
        """
        Create a new time entry and set initial status to 'draft'.
        """
        # Validate and resolve user
        user = UserService().read_by_public_id(public_id=user_public_id)
        if not user:
            raise ValueError(f"User with public_id '{user_public_id}' not found.")
        user_id = user.id

        # Validate and resolve project
        project = ProjectService().read_by_public_id(public_id=project_public_id)
        if not project:
            raise ValueError(f"Project with public_id '{project_public_id}' not found.")
        project_id = project.id

        # Create the time entry
        time_entry = self.repo.create(
            user_id=user_id,
            project_id=project_id,
            work_date=work_date,
            note=note,
        )

        # Create initial 'draft' status
        status_user_id = created_by_user_id or user_id
        TimeEntryStatusRepository().create(
            time_entry_id=time_entry.id,
            status="draft",
            user_id=status_user_id,
        )

        return time_entry

    def read_all(self) -> list[TimeEntry]:
        """
        Read all time entries.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[TimeEntry]:
        """
        Read a time entry by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[TimeEntry]:
        """
        Read a time entry by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> list[TimeEntry]:
        """
        Read all time entries for a specific user.
        """
        return self.repo.read_by_user_id(user_id)

    def read_by_project_id(self, project_id: int) -> list[TimeEntry]:
        """
        Read all time entries for a specific project.
        """
        return self.repo.read_by_project_id(project_id)

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sort_by: str = "WorkDate",
        sort_direction: str = "DESC",
    ) -> list[TimeEntry]:
        """
        Read time entries with pagination and filtering.
        """
        return self.repo.read_paginated(
            page_number=page_number,
            page_size=page_size,
            search_term=search_term,
            user_id=user_id,
            project_id=project_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        """
        Count time entries matching the filter criteria.
        """
        return self.repo.count(
            search_term=search_term,
            user_id=user_id,
            project_id=project_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_public_id: Optional[str] = None,
        project_public_id: Optional[str] = None,
        work_date: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Optional[TimeEntry]:
        """
        Update a time entry by public ID. Only allowed when status is 'draft'.
        """
        # Read existing entry
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        # Check current status allows editing
        current_status = TimeEntryStatusRepository().read_current(time_entry_id=existing.id)
        if current_status and current_status.status != "draft":
            raise ValueError(f"Cannot update time entry in '{current_status.status}' status. Only 'draft' entries can be edited.")

        # Resolve user if provided
        if user_public_id is not None:
            user = UserService().read_by_public_id(public_id=user_public_id)
            if not user:
                raise ValueError(f"User with public_id '{user_public_id}' not found.")
            existing.user_id = user.id

        # Resolve project if provided
        if project_public_id is not None:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            existing.project_id = project.id

        # Apply updates
        if work_date is not None:
            existing.work_date = work_date
        if note is not None:
            existing.note = note

        # Use the provided row_version for optimistic locking
        existing.row_version = row_version

        return self.repo.update_by_id(existing)

    def delete_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
    ) -> Optional[TimeEntry]:
        """
        Delete a time entry by public ID. Only allowed when status is 'draft'.
        """
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        # Check current status allows deletion
        current_status = TimeEntryStatusRepository().read_current(time_entry_id=existing.id)
        if current_status and current_status.status != "draft":
            raise ValueError(f"Cannot delete time entry in '{current_status.status}' status. Only 'draft' entries can be deleted.")

        return self.repo.delete_by_id(id=existing.id)

    def submit(self, public_id: str, *, user_id: int) -> TimeEntry:
        """
        Submit a time entry for review. Transitions from 'draft' to 'submitted'.
        """
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        self._validate_transition(existing.id, "submitted")

        # Verify at least one time log exists
        logs = TimeLogRepository().read_by_time_entry_id(time_entry_id=existing.id)
        if not logs:
            raise ValueError("Cannot submit time entry without any time logs.")

        TimeEntryStatusRepository().create(
            time_entry_id=existing.id,
            status="submitted",
            user_id=user_id,
        )

        return existing

    def approve(self, public_id: str, *, user_id: int, note: Optional[str] = None) -> TimeEntry:
        """
        Approve a submitted time entry. Transitions from 'submitted' to 'approved'.
        """
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        self._validate_transition(existing.id, "approved")

        TimeEntryStatusRepository().create(
            time_entry_id=existing.id,
            status="approved",
            user_id=user_id,
            note=note,
        )

        return existing

    def reject(self, public_id: str, *, user_id: int, note: Optional[str] = None) -> TimeEntry:
        """
        Reject a submitted time entry. Transitions from 'submitted' back to 'draft'.
        """
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        self._validate_transition(existing.id, "rejected")

        status_repo = TimeEntryStatusRepository()

        # Record the rejection with the reviewer's note
        status_repo.create(
            time_entry_id=existing.id,
            status="rejected",
            user_id=user_id,
            note=note,
        )

        # Auto-transition back to draft so the worker can re-edit
        status_repo.create(
            time_entry_id=existing.id,
            status="draft",
            user_id=user_id,
        )

        return existing

    def _validate_transition(self, time_entry_id: int, target_status: str) -> None:
        """
        Validate that a status transition is allowed.
        """
        current = TimeEntryStatusRepository().read_current(time_entry_id=time_entry_id)
        current_status = current.status if current else None

        if current_status is None:
            raise ValueError("Time entry has no status history.")

        allowed = VALID_TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            raise ValueError(
                f"Cannot transition from '{current_status}' to '{target_status}'. "
                f"Allowed transitions: {allowed}"
            )
