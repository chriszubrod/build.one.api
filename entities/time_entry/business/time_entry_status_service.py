# Python Standard Library Imports
import logging
from typing import Optional, Tuple

# Third-party Imports

# Local Imports
from entities.time_entry.business.model import TimeEntryStatus
from entities.time_entry.persistence.repo import TimeEntryRepository
from entities.time_entry.persistence.time_entry_status_repo import TimeEntryStatusRepository
from shared.authz import current_user_id, current_is_system_admin

logger = logging.getLogger(__name__)


def _actor_scope() -> Tuple[Optional[int], Optional[bool]]:
    """Read the current request actor from ContextVars."""
    return current_user_id.get(), current_is_system_admin.get()


class TimeEntryStatusService:
    """
    Service for TimeEntryStatus entity business operations.
    Lightweight child entity — read-only via API (status changes happen through
    TimeEntryService.submit/approve/reject).

    Phase 3 row-scoping: forwards the actor to the repo, which scopes
    via the parent TimeEntry.UserId.
    """

    def __init__(self, repo: Optional[TimeEntryStatusRepository] = None):
        """Initialize the TimeEntryStatusService."""
        self.repo = repo or TimeEntryStatusRepository()

    def read_by_time_entry_public_id(self, time_entry_public_id: str) -> list[TimeEntryStatus]:
        """
        Read the full status history for a time entry by the parent's public ID.
        """
        actor_user_id, actor_is_system_admin = _actor_scope()
        time_entry = TimeEntryRepository().read_by_public_id(
            public_id=time_entry_public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
        if not time_entry:
            raise ValueError(f"TimeEntry with public_id '{time_entry_public_id}' not found.")
        return self.repo.read_by_time_entry_id(
            time_entry_id=time_entry.id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def read_current(self, time_entry_public_id: str) -> Optional[TimeEntryStatus]:
        """
        Read the current (most recent) status for a time entry.
        """
        actor_user_id, actor_is_system_admin = _actor_scope()
        time_entry = TimeEntryRepository().read_by_public_id(
            public_id=time_entry_public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
        if not time_entry:
            raise ValueError(f"TimeEntry with public_id '{time_entry_public_id}' not found.")
        return self.repo.read_current(
            time_entry_id=time_entry.id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
