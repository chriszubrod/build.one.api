# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class PaymentTermCreate(BaseModel):
    name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The name of the payment term.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The description of the payment term.",
    )
    discount_percent: Optional[float] = Field(
        default=None,
        description="The discount percentage for early payment.",
    )
    discount_days: Optional[int] = Field(
        default=None,
        description="The number of days within which the discount applies.",
    )
    due_days: Optional[int] = Field(
        default=None,
        description="The number of days until payment is due.",
    )


class PaymentTermUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the payment term (base64 encoded).",
    )
    name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The name of the payment term.",
    )
    description: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The description of the payment term.",
    )
    discount_percent: Optional[float] = Field(
        default=None,
        description="The discount percentage for early payment.",
    )
    discount_days: Optional[int] = Field(
        default=None,
        description="The number of days within which the discount applies.",
    )
    due_days: Optional[int] = Field(
        default=None,
        description="The number of days until payment is due.",
    )
