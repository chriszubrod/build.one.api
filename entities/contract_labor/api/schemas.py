# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ContractLaborCreate(BaseModel):
    """Schema for creating a new contract labor entry."""
    vendor_public_id: str = Field(
        description="The public ID of the vendor (contractor)."
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description="The public ID of the project (from Job column)."
    )
    employee_name: str = Field(
        max_length=255,
        description="The name of the worker/vendor from the Name column."
    )
    work_date: str = Field(
        description="The date of work (YYYY-MM-DD format)."
    )
    time_in: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Time In value from Excel."
    )
    time_out: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Time Out value from Excel."
    )
    break_time: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Break Time value from Excel."
    )
    regular_hours: Optional[Decimal] = Field(
        default=None,
        description="Regular hours worked."
    )
    overtime_hours: Optional[Decimal] = Field(
        default=None,
        description="Overtime hours worked."
    )
    total_hours: Decimal = Field(
        description="Total hours worked."
    )
    hourly_rate: Optional[Decimal] = Field(
        default=None,
        description="Hourly rate for billing."
    )
    markup: Optional[Decimal] = Field(
        default=None,
        description="Markup percentage (e.g., 0.10 for 10%)."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The SubCostCode ID for billing categorization."
    )
    description: Optional[str] = Field(
        default=None,
        description="Notes/description for the work."
    )
    status: Optional[str] = Field(
        default="pending_review",
        description="Status: pending_review, ready, or billed."
    )
    import_batch_id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Batch ID for grouping imported entries."
    )
    source_file: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Original Excel filename."
    )
    source_row: Optional[int] = Field(
        default=None,
        description="Row number in the source Excel file."
    )


class ContractLaborUpdate(BaseModel):
    """Schema for updating a contract labor entry."""
    row_version: str = Field(
        description="The row version for optimistic concurrency (base64 encoded)."
    )
    vendor_public_id: Optional[str] = Field(
        default=None,
        description="The public ID of the vendor."
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description="The public ID of the project."
    )
    employee_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The name of the worker/vendor."
    )
    work_date: Optional[str] = Field(
        default=None,
        description="The date of work (YYYY-MM-DD format)."
    )
    time_in: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Time In value."
    )
    time_out: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Time Out value."
    )
    break_time: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Break Time value."
    )
    regular_hours: Optional[Decimal] = Field(
        default=None,
        description="Regular hours worked."
    )
    overtime_hours: Optional[Decimal] = Field(
        default=None,
        description="Overtime hours worked."
    )
    total_hours: Optional[Decimal] = Field(
        default=None,
        description="Total hours worked."
    )
    hourly_rate: Optional[Decimal] = Field(
        default=None,
        description="Hourly rate for billing."
    )
    markup: Optional[Decimal] = Field(
        default=None,
        description="Markup percentage (e.g., 0.10 for 10%)."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The SubCostCode ID for billing categorization."
    )
    description: Optional[str] = Field(
        default=None,
        description="Notes/description for the work."
    )
    status: Optional[str] = Field(
        default=None,
        description="Status: pending_review, ready, or billed."
    )


class ContractLaborResponse(BaseModel):
    """Schema for contract labor response."""
    id: int
    public_id: str
    row_version: str
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    vendor_id: Optional[int]
    project_id: Optional[int]
    employee_name: Optional[str]
    work_date: Optional[str]
    time_in: Optional[str]
    time_out: Optional[str]
    break_time: Optional[str]
    regular_hours: Optional[Decimal]
    overtime_hours: Optional[Decimal]
    total_hours: Optional[Decimal]
    hourly_rate: Optional[Decimal]
    markup: Optional[Decimal]
    total_amount: Optional[Decimal]
    sub_cost_code_id: Optional[int]
    description: Optional[str]
    billing_period_start: Optional[str]
    status: Optional[str]
    bill_line_item_id: Optional[int]
    import_batch_id: Optional[str]
    source_file: Optional[str]
    source_row: Optional[int]

    class Config:
        from_attributes = True


class ContractLaborBulkMarkReady(BaseModel):
    """Schema for bulk marking entries as ready."""
    public_ids: list[str] = Field(
        description="List of public IDs to mark as ready."
    )


class ContractLaborBulkMarkReadyResponse(BaseModel):
    """Response schema for bulk mark ready operation."""
    success_count: int
    error_count: int
    errors: list[dict]


class ContractLaborBulkDelete(BaseModel):
    """Schema for bulk deleting entries."""
    public_ids: list[str] = Field(
        description="List of public IDs to delete."
    )


class ContractLaborBulkDeleteResponse(BaseModel):
    """Response schema for bulk delete operation."""
    success_count: int
    error_count: int
    errors: list[dict]


class ContractLaborLastRateResponse(BaseModel):
    """Response schema for getting last rate for a vendor."""
    vendor_public_id: str
    hourly_rate: Optional[Decimal]
    markup: Optional[Decimal]


class ContractLaborImportRequest(BaseModel):
    """Schema for Excel import request."""
    import_batch_id: Optional[str] = Field(
        default=None,
        description="Optional batch ID. If not provided, one will be generated."
    )
    default_hourly_rate: Optional[Decimal] = Field(
        default=None,
        description="Default hourly rate to apply to all entries."
    )
    default_markup: Optional[Decimal] = Field(
        default=None,
        description="Default markup to apply to all entries."
    )
    carry_forward_rates: bool = Field(
        default=True,
        description="Whether to carry forward rates from previous entries for each vendor."
    )


class ContractLaborImportResponse(BaseModel):
    """Response schema for Excel import operation."""
    import_batch_id: str
    total_rows: int
    imported_count: int
    skipped_count: int
    error_count: int
    errors: list[dict]
    unmatched_vendors: list[str]
    unmatched_projects: list[str]


class ContractLaborBillingSummaryGroup(BaseModel):
    """Schema for a group in the billing summary."""
    vendor_id: int
    vendor_name: str
    billing_period_start: str
    entry_count: int
    total_hours: float
    total_amount: float


class ContractLaborBillingSummaryResponse(BaseModel):
    """Response schema for billing summary."""
    total_entries: int
    total_hours: float
    total_amount: float
    groups: list[ContractLaborBillingSummaryGroup]


class ContractLaborCreateBillsResponse(BaseModel):
    """Response schema for creating bills from contract labor."""
    success: bool
    message: str
    bills_created: int
    line_items_created: int
    entries_billed: int
    errors: list[str]
    bills: list[str]


class ContractLaborGeneratePDFsResponse(BaseModel):
    """Response schema for PDF generation."""
    success: bool
    message: str
    pdfs_generated: int
    errors: list[str]


class ContractLaborLineItemUpdate(BaseModel):
    """Schema for creating/updating a line item."""
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    line_date: Optional[str] = None
    project_id: Optional[int] = None
    sub_cost_code_id: Optional[int] = None
    description: Optional[str] = None
    hours: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    markup: Optional[Decimal] = None
    price: Optional[Decimal] = None
    is_billable: bool = True
    is_overhead: bool = False


class ContractLaborBillUpdate(BaseModel):
    """Schema for updating bill info and line items."""
    row_version: str = Field(description="Row version for optimistic locking.")
    bill_vendor_id: Optional[int] = None
    bill_date: Optional[str] = None
    due_date: Optional[str] = None
    bill_number: Optional[str] = None
    status: Optional[str] = None
    line_items: list[ContractLaborLineItemUpdate] = []


class ContractLaborBillUpdateResponse(BaseModel):
    """Response schema for bill update."""
    public_id: str
    row_version: str
    bill_vendor_id: Optional[int]
    bill_date: Optional[str]
    due_date: Optional[str]
    bill_number: Optional[str]
    status: Optional[str]
    line_items_created: int
    line_items_updated: int
    line_items_deleted: int
