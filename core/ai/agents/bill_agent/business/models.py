# Python Standard Library Imports
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class BillAgentRun:
    """Tracks a single execution of the bill folder processing agent."""
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    status: Optional[str] = None
    trigger_source: Optional[str] = None
    completed_datetime: Optional[str] = None
    files_found: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    bills_created: int = 0
    error_count: int = 0
    summary: Optional[str] = None
    created_by: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "public_id": self.public_id,
            "status": self.status,
            "trigger_source": self.trigger_source,
            "created_datetime": self.created_datetime,
            "completed_datetime": self.completed_datetime,
            "files_found": self.files_found,
            "files_processed": self.files_processed,
            "files_skipped": self.files_skipped,
            "bills_created": self.bills_created,
            "error_count": self.error_count,
            "summary": self.summary,
            "created_by": self.created_by,
        }


@dataclass
class ProcessingResult:
    """Result of a bill folder processing run."""
    files_found: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    bills_created: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.errors)


@dataclass
class FilenameParsedData:
    """Data extracted from a bill filename."""
    project_abbrev: Optional[str] = None
    vendor_name: Optional[str] = None
    bill_number: Optional[str] = None
    description: Optional[str] = None
    sub_cost_code_raw: Optional[str] = None
    rate: Optional[Decimal] = None
    bill_date: Optional[str] = None  # YYYY-MM-DD

    # Resolved entity IDs
    project_public_id: Optional[str] = None
    vendor_public_id: Optional[str] = None
    sub_cost_code_id: Optional[int] = None
