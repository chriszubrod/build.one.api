# Python Standard Library Imports
import logging
from typing import Optional, Tuple

# Third-party Imports

# Local Imports
from entities.time_entry.business.model import TimeEntry
from entities.time_entry.persistence.repo import TimeEntryRepository
from entities.time_entry.persistence.time_entry_status_repo import TimeEntryStatusRepository
from entities.time_entry.persistence.time_log_repo import TimeLogRepository
from entities.user.business.service import UserService
from shared.authz import current_user_id, current_is_system_admin

logger = logging.getLogger(__name__)

# Valid status transitions
VALID_TRANSITIONS = {
    "draft": ["submitted"],
    "submitted": ["approved", "rejected"],
    "approved": ["billed"],
    "rejected": ["draft"],
    "billed": [],
}


def _actor_scope() -> Tuple[Optional[int], Optional[bool]]:
    """Read the current request actor from ContextVars."""
    return current_user_id.get(), current_is_system_admin.get()


class TimeEntryService:
    """
    Service for TimeEntry entity business operations.

    Phase 3 row-scoping: read/update/delete paths read the calling
    user's id + system-admin flag from ContextVars and forward them
    to the repository. Sprocs filter to rows whose UserId matches
    (system admins bypass).

    The submit/approve/reject transition methods bypass UserId scope
    via `actor_is_system_admin=True`. The API surface gates these on
    the Time Tracking module's can_submit / can_approve permissions
    (RBAC), so the service can trust that the caller is authorized
    to act on entries they don't own.
    """

    def __init__(self, repo: Optional[TimeEntryRepository] = None):
        """Initialize the TimeEntryService."""
        self.repo = repo or TimeEntryRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        user_public_id: str,
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

        # Create the time entry
        time_entry = self.repo.create(
            user_id=user_id,
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
        Read all time entries the current actor can access.
        """
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_all(
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def read_by_id(self, id: int) -> Optional[TimeEntry]:
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_by_id(
            id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def read_by_public_id(self, public_id: str) -> Optional[TimeEntry]:
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_by_public_id(
            public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def read_by_user_id(self, user_id: int) -> list[TimeEntry]:
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_by_user_id(
            user_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def read_by_project_id(self, project_id: int) -> list[TimeEntry]:
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.read_by_project_id(
            project_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

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
        actor_user_id, actor_is_system_admin = _actor_scope()
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
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
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
        actor_user_id, actor_is_system_admin = _actor_scope()
        return self.repo.count(
            search_term=search_term,
            user_id=user_id,
            project_id=project_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_public_id: Optional[str] = None,
        work_date: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Optional[TimeEntry]:
        """
        Update a time entry by public ID. Only allowed when status is 'draft'.
        """
        actor_user_id, actor_is_system_admin = _actor_scope()

        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        # Status read uses admin bypass — scope is already enforced via
        # the read above. We just need the status itself.
        current_status = TimeEntryStatusRepository().read_current(
            time_entry_id=existing.id,
            actor_is_system_admin=True,
        )
        if current_status and current_status.status != "draft":
            raise ValueError(f"Cannot update time entry in '{current_status.status}' status. Only 'draft' entries can be edited.")

        if user_public_id is not None:
            user = UserService().read_by_public_id(public_id=user_public_id)
            if not user:
                raise ValueError(f"User with public_id '{user_public_id}' not found.")
            existing.user_id = user.id

        if work_date is not None:
            existing.work_date = work_date
        if note is not None:
            existing.note = note

        existing.row_version = row_version

        return self.repo.update_by_id(
            existing,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

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
        actor_user_id, actor_is_system_admin = _actor_scope()

        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        current_status = TimeEntryStatusRepository().read_current(
            time_entry_id=existing.id,
            actor_is_system_admin=True,
        )
        if current_status and current_status.status != "draft":
            raise ValueError(f"Cannot delete time entry in '{current_status.status}' status. Only 'draft' entries can be deleted.")

        return self.repo.delete_by_id(
            id=existing.id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def submit(self, public_id: str, *, user_id: int) -> TimeEntry:
        """
        Submit a time entry for review. Transitions from 'draft' to 'submitted'.
        Caller must be the entry owner (enforced by row-scope at read).
        """
        actor_user_id, actor_is_system_admin = _actor_scope()
        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        self._validate_transition(existing.id, "submitted")

        # Verify at least one time log exists. Bypass row-scope here —
        # the parent read already proved access.
        logs = TimeLogRepository().read_by_time_entry_id(
            time_entry_id=existing.id,
            actor_is_system_admin=True,
        )
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
        The API surface gates this on Time Tracking can_approve — reviewers act
        on entries they don't own, so this method bypasses UserId row-scope.
        """
        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_is_system_admin=True,
        )
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
        The API surface gates this on Time Tracking can_approve — reviewers act
        on entries they don't own, so this method bypasses UserId row-scope.
        """
        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_is_system_admin=True,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        self._validate_transition(existing.id, "rejected")

        status_repo = TimeEntryStatusRepository()

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
        Validate that a status transition is allowed. Status reads bypass
        row-scope — the caller already proved access (or is acting in a
        privileged transition like approve/reject).
        """
        current = TimeEntryStatusRepository().read_current(
            time_entry_id=time_entry_id,
            actor_is_system_admin=True,
        )
        current_status = current.status if current else None

        if current_status is None:
            raise ValueError("Time entry has no status history.")

        allowed = VALID_TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            raise ValueError(
                f"Cannot transition from '{current_status}' to '{target_status}'. "
                f"Allowed transitions: {allowed}"
            )
