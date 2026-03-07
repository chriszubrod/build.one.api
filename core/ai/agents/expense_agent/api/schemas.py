# Python Standard Library Imports
from typing import Optional, List

# Third-party Imports
from pydantic import BaseModel, Field


class ExpenseAgentRunRequest(BaseModel):
    """Request model for triggering an expense agent run."""
    company_id: int = Field(
        description="The database ID of the company"
    )
    trigger_source: str = Field(
        default="manual",
        description="Source of the trigger: 'manual' or 'scheduler'"
    )


class ExpenseAgentRunResponse(BaseModel):
    """Response model for an expense agent run."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    run_public_id: Optional[str] = Field(
        default=None,
        description="Public ID of the run"
    )
    status: Optional[str] = Field(
        default=None,
        description="Run status: running, completed, failed"
    )
    files_found: Optional[int] = Field(
        default=None,
        description="Number of files found in source folder"
    )
    files_processed: Optional[int] = Field(
        default=None,
        description="Number of files successfully processed"
    )
    files_skipped: Optional[int] = Field(
        default=None,
        description="Number of files skipped due to errors"
    )
    expenses_created: Optional[int] = Field(
        default=None,
        description="Number of expense drafts created"
    )
    error_count: Optional[int] = Field(
        default=None,
        description="Number of errors"
    )
    errors: Optional[List[str]] = Field(
        default=None,
        description="List of error messages"
    )


class ExpenseAgentFolderStatusResponse(BaseModel):
    """Response model for folder status check."""
    message: str = Field(
        description="Status message"
    )
    status_code: int = Field(
        description="HTTP status code"
    )
    is_linked: bool = Field(
        default=False,
        description="Whether a source folder is linked"
    )
    folder_name: Optional[str] = Field(
        default=None,
        description="Name of the source folder"
    )
    folder_web_url: Optional[str] = Field(
        default=None,
        description="Web URL of the source folder"
    )
    file_count: Optional[int] = Field(
        default=None,
        description="Number of PDF files in the source folder"
    )
    last_run_datetime: Optional[str] = Field(
        default=None,
        description="When the last run completed"
    )
