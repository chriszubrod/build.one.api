# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BillCreate(BaseModel):
    vendor_public_id: str = Field(
        description="The vendor public ID of the bill."
    )
    terms_id: Optional[int] = Field(
        default=None,
        description="The terms ID of the bill."
    )
    bill_date: str = Field(
        description="The bill date of the bill."
    )
    due_date: str = Field(
        description="The due date of the bill."
    )
    bill_number: str = Field(
        max_length=50,
        description="The bill number of the bill."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the bill."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the bill."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the bill is a draft."
    )


class BillUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the bill (base64 encoded)."
    )
    vendor_public_id: str = Field(
        description="The vendor public ID of the bill."
    )
    terms_id: Optional[int] = Field(
        default=None,
        description="The terms ID of the bill."
    )
    bill_date: str = Field(
        description="The bill date of the bill."
    )
    due_date: str = Field(
        description="The due date of the bill."
    )
    bill_number: str = Field(
        max_length=50,
        description="The bill number of the bill."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the bill."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the bill."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the bill is a draft."
    )