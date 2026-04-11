# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ExpenseCreate(BaseModel):
    vendor_public_id: str = Field(
        description="The vendor public ID of the expense."
    )
    expense_date: str = Field(
        description="The expense date."
    )
    reference_number: str = Field(
        max_length=50,
        description="The reference number (receipt or transaction reference)."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the expense."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the expense."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the expense is a draft."
    )
    is_credit: Optional[bool] = Field(
        default=False,
        description="Whether the expense is a credit card credit (refund)."
    )


class ExpenseUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the expense (base64 encoded)."
    )
    vendor_public_id: str = Field(
        description="The vendor public ID of the expense."
    )
    expense_date: str = Field(
        description="The expense date."
    )
    reference_number: str = Field(
        max_length=50,
        description="The reference number (receipt or transaction reference)."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the expense."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the expense."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the expense is a draft."
    )
    is_credit: Optional[bool] = Field(
        default=None,
        description="Whether the expense is a credit card credit (refund)."
    )
