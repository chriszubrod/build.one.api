# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal
from datetime import datetime

# Third-party Imports

# Local Imports
from entities.time_entry.business.model import TimeLog
from entities.time_entry.persistence.repo import TimeEntryRepository
from entities.time_entry.persistence.time_log_repo import TimeLogRepository
from entities.time_entry.persistence.time_entry_status_repo import TimeEntryStatusRepository

logger = logging.getLogger(__name__)


class TimeLogService:
    """
    Service for TimeLog entity business operations.
    Lightweight child entity — direct CRUD, no ProcessEngine routing.
    """

    def __init__(self, repo: Optional[TimeLogRepository] = None):
        """Initialize the TimeLogService."""
        self.repo = repo or TimeLogRepository()

    def create(
        self,
        *,
        time_entry_public_id: str,
        clock_in: str,
        clock_out: Optional[str] = None,
        log_type: str = "work",
        latitude: Optional[Decimal] = None,
        longitude: Optional[Decimal] = None,
        project_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> TimeLog:
        """
        Create a new time log for a time entry.
        Only allowed when the parent time entry is in 'draft' status.
        """
        # Validate parent exists
        time_entry = TimeEntryRepository().read_by_public_id(public_id=time_entry_public_id)
        if not time_entry:
            raise ValueError(f"TimeEntry with public_id '{time_entry_public_id}' not found.")

        # Check parent status allows editing
        self._validate_parent_is_draft(time_entry.id)

        # Validate log_type
        if log_type not in ("work", "break"):
            raise ValueError(f"Invalid log_type '{log_type}'. Must be 'work' or 'break'.")

        # Calculate duration from timestamps
        duration = self._calculate_duration(clock_in, clock_out)

        return self.repo.create(
            time_entry_id=time_entry.id,
            clock_in=clock_in,
            clock_out=clock_out,
            log_type=log_type,
            duration=duration,
            latitude=latitude,
            longitude=longitude,
            project_id=project_id,
            note=note,
        )

    def read_by_time_entry_public_id(self, time_entry_public_id: str) -> list[TimeLog]:
        """
        Read all time logs for a time entry by the parent's public ID.
        """
        time_entry = TimeEntryRepository().read_by_public_id(public_id=time_entry_public_id)
        if not time_entry:
            raise ValueError(f"TimeEntry with public_id '{time_entry_public_id}' not found.")
        return self.repo.read_by_time_entry_id(time_entry_id=time_entry.id)

    def read_by_public_id(self, public_id: str) -> Optional[TimeLog]:
        """
        Read a time log by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        row_version: str,
        clock_in: Optional[str] = None,
        clock_out: Optional[str] = None,
        log_type: Optional[str] = None,
        latitude: Optional[Decimal] = None,
        longitude: Optional[Decimal] = None,
        project_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> Optional[TimeLog]:
        """
        Update a time log by public ID.
        Only allowed when the parent time entry is in 'draft' status.
        """
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing:
            raise ValueError(f"TimeLog with public_id '{public_id}' not found.")

        # Check parent status allows editing
        self._validate_parent_is_draft(existing.time_entry_id)

        # Validate log_type if provided
        if log_type is not None and log_type not in ("work", "break"):
            raise ValueError(f"Invalid log_type '{log_type}'. Must be 'work' or 'break'.")

        # Apply updates
        if clock_in is not None:
            existing.clock_in = clock_in
        if clock_out is not None:
            existing.clock_out = clock_out
        if log_type is not None:
            existing.log_type = log_type
        if latitude is not None:
            existing.latitude = latitude
        if longitude is not None:
            existing.longitude = longitude
        if project_id is not None:
            existing.project_id = project_id
        if note is not None:
            existing.note = note

        # Recalculate duration
        existing.duration = self._calculate_duration(existing.clock_in, existing.clock_out)

        # Use provided row_version for optimistic locking
        existing.row_version = row_version

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[TimeLog]:
        """
        Delete a time log by public ID.
        Only allowed when the parent time entry is in 'draft' status.
        """
        existing = self.repo.read_by_public_id(public_id=public_id)
        if not existing:
            raise ValueError(f"TimeLog with public_id '{public_id}' not found.")

        # Check parent status allows editing
        self._validate_parent_is_draft(existing.time_entry_id)

        return self.repo.delete_by_id(id=existing.id)

    def _validate_parent_is_draft(self, time_entry_id: int) -> None:
        """
        Validate that the parent time entry is in 'draft' status.
        """
        current_status = TimeEntryStatusRepository().read_current(time_entry_id=time_entry_id)
        if current_status and current_status.status != "draft":
            raise ValueError(
                f"Cannot modify time logs when time entry is in '{current_status.status}' status. "
                "Only 'draft' entries can have their time logs modified."
            )

    @staticmethod
    def _calculate_duration(clock_in: str, clock_out: Optional[str]) -> Optional[Decimal]:
        """
        Calculate duration in hours from clock_in and clock_out timestamps.
        Returns None if clock_out is not set.
        """
        if not clock_out:
            return None

        try:
            # Try parsing with milliseconds first, then without
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt_in = datetime.strptime(clock_in, fmt)
                    break
                except ValueError:
                    continue
            else:
                logger.warning(f"Could not parse clock_in: {clock_in}")
                return None

            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt_out = datetime.strptime(clock_out, fmt)
                    break
                except ValueError:
                    continue
            else:
                logger.warning(f"Could not parse clock_out: {clock_out}")
                return None

            delta = dt_out - dt_in
            hours = Decimal(str(delta.total_seconds())) / Decimal("3600")
            # Round to 2 decimal places
            return hours.quantize(Decimal("0.01"))
        except Exception as e:
            logger.warning(f"Error calculating duration: {e}")
            return None
