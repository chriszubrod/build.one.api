# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboPurchaseSync(BaseModel):
    realm_id: str = Field(
        description="QBO company realm ID.",
    )
    last_updated_time: Optional[str] = Field(
        default=None,
        description="Optional ISO format datetime. If provided, only sync purchases updated after this time.",
    )
    start_date: Optional[str] = Field(
        default=None,
        description="Optional date string (YYYY-MM-DD). If provided, only sync purchases on or after this date.",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="Optional date string (YYYY-MM-DD). If provided, only sync purchases on or before this date.",
    )
    sync_to_modules: bool = Field(
        default=False,
        description="If True, also sync to Expense/ExpenseLineItem modules.",
    )


class QboPurchasePush(BaseModel):
    """Request schema for pushing a local Expense to QBO as a Purchase."""
    realm_id: str = Field(
        description="QBO company realm ID.",
    )
    sync_attachments: bool = Field(
        default=True,
        description="If True, also sync attachments for the expense to QBO.",
    )
