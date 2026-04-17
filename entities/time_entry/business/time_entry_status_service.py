# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.time_entry.business.model import TimeEntryStatus
from entities.time_entry.persistence.repo import TimeEntryRepository
from entities.time_entry.persistence.time_entry_status_repo import TimeEntryStatusRepository

logger = logging.getLogger(__name__)


class TimeEntryStatusService:
    """
    Service for TimeEntryStatus entity business operations.
    Lightweight child entity — read-only via API (status changes happen through
    TimeEntryService.submit/approve/reject).
    """

    def __init__(self, repo: Optional[TimeEntryStatusRepository] = None):
        """Initialize the TimeEntryStatusService."""
        self.repo = repo or TimeEntryStatusRepository()

    def read_by_time_entry_public_id(self, time_entry_public_id: str) -> list[TimeEntryStatus]:
        """
        Read the full status history for a time entry by the parent's public ID.
        """
        time_entry = TimeEntryRepository().read_by_public_id(public_id=time_entry_public_id)
        if not time_entry:
            raise ValueError(f"TimeEntry with public_id '{time_entry_public_id}' not found.")
        return self.repo.read_by_time_entry_id(time_entry_id=time_entry.id)

    def read_current(self, time_entry_public_id: str) -> Optional[TimeEntryStatus]:
        """
        Read the current (most recent) status for a time entry.
        """
        time_entry = TimeEntryRepository().read_by_public_id(public_id=time_entry_public_id)
        if not time_entry:
            raise ValueError(f"TimeEntry with public_id '{time_entry_public_id}' not found.")
        return self.repo.read_current(time_entry_id=time_entry.id)
