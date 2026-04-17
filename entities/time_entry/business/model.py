# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
from decimal import Decimal
import base64

# Third-party Imports

# Local Imports


@dataclass
class TimeEntry:
    """
    Represents a time tracking entry for a worker on a project.
    One record per worker per day per project.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]

    # Worker and assignment
    user_id: Optional[int]             # FK to User (the worker)
    project_id: Optional[int]          # FK to Project
    work_date: Optional[str]           # Date worked (YYYY-MM-DD)
    note: Optional[str]                # Worker's note, important for reviewer

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        """Decode base64 row version to bytes."""
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        """Get row version as hex string."""
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    def to_dict(self) -> dict:
        """Convert the time entry dataclass to a dictionary."""
        return asdict(self)


@dataclass
class TimeLog:
    """
    Represents a raw clock in/out timestamp for a time entry.
    Multiple logs per TimeEntry. The audit trail a reviewer examines.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]

    # Parent reference
    time_entry_id: Optional[int]       # FK to TimeEntry

    # Timestamp data
    clock_in: Optional[str]            # Clock in timestamp
    clock_out: Optional[str]           # Clock out timestamp (NULL = still clocked in)
    log_type: Optional[str]            # 'work' or 'break'
    duration: Optional[Decimal]        # Calculated from timestamps (hours)

    # Location
    latitude: Optional[Decimal]        # GPS latitude at clock in/out
    longitude: Optional[Decimal]       # GPS longitude at clock in/out

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        """Decode base64 row version to bytes."""
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        """Get row version as hex string."""
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    def to_dict(self) -> dict:
        """Convert the time log dataclass to a dictionary."""
        return asdict(self)


@dataclass
class TimeEntryStatus:
    """
    Represents a status transition in the time entry workflow.
    Full history of transitions with who/when/why.
    Current status = most recent row per TimeEntryId.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]

    # Status transition
    time_entry_id: Optional[int]       # FK to TimeEntry
    status: Optional[str]              # draft, submitted, approved, rejected, billed
    user_id: Optional[int]             # FK to User (who made the change)
    note: Optional[str]                # Rejection reason, approval notes, etc.

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        """Decode base64 row version to bytes."""
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        """Get row version as hex string."""
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    def to_dict(self) -> dict:
        """Convert the time entry status dataclass to a dictionary."""
        return asdict(self)
