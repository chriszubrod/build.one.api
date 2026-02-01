# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BillCreditCreate(BaseModel):
    vendor_public_id: str = Field(
        description="The vendor public ID of the bill credit."
    )
    credit_date: str = Field(
        description="The credit date."
    )
    credit_number: str = Field(
        max_length=50,
        description="The credit number (vendor credit reference)."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the bill credit."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the bill credit."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the bill credit is a draft."
    )


class BillCreditUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the bill credit (base64 encoded)."
    )
    vendor_public_id: str = Field(
        description="The vendor public ID of the bill credit."
    )
    credit_date: str = Field(
        description="The credit date."
    )
    credit_number: str = Field(
        max_length=50,
        description="The credit number (vendor credit reference)."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the bill credit."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the bill credit."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the bill credit is a draft."
    )
