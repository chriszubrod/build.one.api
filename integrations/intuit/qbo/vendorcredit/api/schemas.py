# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboVendorCreditSyncRequest(BaseModel):
    """Request body for VendorCredit sync."""
    realm_id: str = Field(description="QBO realm/company ID")
    last_updated_time: Optional[str] = Field(
        default=None,
        description="Fetch only records updated after this time (ISO format)"
    )
    start_date: Optional[str] = Field(
        default=None,
        description="Filter by transaction date start (YYYY-MM-DD)"
    )
    end_date: Optional[str] = Field(
        default=None,
        description="Filter by transaction date end (YYYY-MM-DD)"
    )
    sync_to_modules: bool = Field(
        default=True,
        description="Whether to sync to BillCredit module"
    )
