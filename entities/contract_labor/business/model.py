# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
from decimal import Decimal
import base64

# Third-party Imports

# Local Imports


@dataclass
class ContractLabor:
    """
    Represents a contract labor time log entry imported from Excel.
    Used for tracking hours worked and creating bills.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    
    # Source data from Excel
    vendor_id: Optional[int]           # Matched from Name column (assigned during review)
    project_id: Optional[int]          # Matched from Job column (assigned during review)
    employee_name: Optional[str]       # Original Name value from Excel (worker/vendor name)
    job_name: Optional[str]            # Original Job value from Excel
    work_date: Optional[str]           # Date column (YYYY-MM-DD)
    
    # Time data
    time_in: Optional[str]             # Time In column
    time_out: Optional[str]            # Time Out column
    break_time: Optional[str]          # Break Time column
    regular_hours: Optional[Decimal]   # Regular Time (parsed hours)
    overtime_hours: Optional[Decimal]  # OT column (parsed hours)
    total_hours: Optional[Decimal]     # Total Work Time (parsed hours)
    
    # Rates & amounts (entered during review)
    hourly_rate: Optional[Decimal]     # Rate per hour
    markup: Optional[Decimal]          # Markup percentage (e.g., 0.10 for 10%)
    total_amount: Optional[Decimal]    # Calculated: hours * rate * (1 + markup)
    
    # Assignment (manual entry during review)
    sub_cost_code_id: Optional[int]    # Assigned during review
    description: Optional[str]         # Notes column + editable
    
    # Billing period
    billing_period_start: Optional[str]  # Start of billing period (1st or 16th)
    status: Optional[str]              # pending_review, ready, billed
    bill_line_item_id: Optional[int]   # Legacy: Set when billed via old process
    
    # Bill header fields (for PDF generation)
    bill_vendor_id: Optional[int]      # Selected vendor for billing
    bill_date: Optional[str]           # Bill date (YYYY-MM-DD)
    due_date: Optional[str]            # Due date (YYYY-MM-DD)
    bill_number: Optional[str]         # Bill number
    
    # Source tracking
    import_batch_id: Optional[str]     # Groups imports together
    source_file: Optional[str]         # Original Excel filename
    source_row: Optional[int]          # Row number in Excel

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
        """Convert the contract labor dataclass to a dictionary."""
        return asdict(self)

    def calculate_total_amount(self) -> Optional[Decimal]:
        """
        Calculate total amount based on hours, rate, and markup.
        Formula: total_hours * hourly_rate * (1 + markup)
        """
        if self.total_hours is None or self.hourly_rate is None:
            return None
        
        base_amount = self.total_hours * self.hourly_rate
        
        if self.markup is not None:
            return base_amount * (Decimal("1") + self.markup)
        
        return base_amount

    @staticmethod
    def calculate_billing_period_start(work_date: str) -> Optional[str]:
        """
        Calculate the billing period date from a work date.
        Period 1: 1st - 15th → billing period is the 15th
        Period 2: 16th - end of month → billing period is the last day of month
        
        Args:
            work_date: Date in YYYY-MM-DD format
            
        Returns:
            Billing period date in YYYY-MM-DD format
        """
        if not work_date:
            return None
        
        try:
            from calendar import monthrange
            parts = work_date.split("-")
            year = int(parts[0])
            month = int(parts[1])
            day = int(parts[2])
            
            if day <= 15:
                # First half of month → 15th
                return f"{year:04d}-{month:02d}-15"
            else:
                # Second half of month → last day of month
                last_day = monthrange(year, month)[1]
                return f"{year:04d}-{month:02d}-{last_day:02d}"
        except (IndexError, ValueError):
            return None

    @property
    def is_ready_for_billing(self) -> bool:
        """Check if this entry has all required fields for billing."""
        return (
            self.bill_vendor_id is not None
            and self.bill_date is not None
            and self.bill_number is not None
            and self.status == "ready"
        )


@dataclass
class ContractLaborLineItem:
    """
    Represents a line item for contract labor billing.
    Many line items can belong to one ContractLabor entry.
    
    Price formula: (Hours / 8 * Rate) * (1 + Markup)
    This represents a percentage of an 8-hour day.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    
    # Parent reference
    contract_labor_id: Optional[int]   # FK to ContractLabor
    bill_line_item_id: Optional[int]   # FK to BillLineItem (link-back when billed)

    # Line item details
    line_date: Optional[str]           # Date for this line (YYYY-MM-DD)
    project_id: Optional[int]          # FK to Project
    sub_cost_code_id: Optional[int]    # FK to SubCostCode
    description: Optional[str]         # Line item description
    hours: Optional[Decimal]           # Hours allocated to this line
    rate: Optional[Decimal]            # Hourly rate
    markup: Optional[Decimal]          # Markup percentage (e.g., 0.05 for 5%)
    price: Optional[Decimal]           # Calculated: (Hours / 8 * Rate) * (1 + Markup)
    is_billable: Optional[bool]        # Whether this line is billable

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
        """Convert the line item dataclass to a dictionary."""
        return asdict(self)

    def calculate_price(self) -> Optional[Decimal]:
        """
        Calculate price based on hours, rate, and markup.
        Formula: (Hours / 8 * Rate) * (1 + Markup)
        
        This represents a percentage of an 8-hour day.
        Example: 6.75 hours at $230/day with 5% markup
                 = (6.75 / 8 * 230) * 1.05 = $203.77
        """
        if self.hours is None or self.rate is None:
            return None
        
        # Calculate as percentage of 8-hour day
        base_amount = (self.hours / Decimal("8")) * self.rate
        
        if self.markup is not None:
            return base_amount * (Decimal("1") + self.markup)
        
        return base_amount
