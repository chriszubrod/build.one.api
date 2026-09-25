# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BillLineItemCreate(BaseModel):
    bill_public_id: str = Field(
        description="The bill public ID of the bill line item."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The sub cost code ID of the bill line item."
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description="The project public ID of the bill line item."
    )
    description: Optional[str] = Field(
        default=None,
        description="The description of the bill line item."
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        max_digits=18,
        # Bounded to what DECIMAL(18,4) can actually hold. `max_digits` alone is
        # NOT that bound — it counts TOTAL digits, so it accepts 123456789012345
        # (15 integer digits), which overflows the column at the DB boundary.
        # The real ceiling is 14 integer digits. `decimal_places` is deliberately
        # NOT set: it would reject 5.016667, and qbo.BillLine.Qty is DECIMAL(18,6)
        # carrying exactly that (sixths of an hour). Magnitude bounded, scale not.
        le=Decimal("99999999999999.9999"),
        ge=Decimal("-99999999999999.9999"),
        description="The quantity of the bill line item (fractional allowed, e.g. 5.25)."
    )
    rate: Optional[Decimal] = Field(
        default=None,
        description="The rate per unit of the bill line item."
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="The amount of the bill line item (Quantity * Rate)."
    )
    is_billable: Optional[bool] = Field(
        default=None,
        description="Whether the bill line item is billable."
    )
    is_billed: Optional[bool] = Field(
        default=None,
        description="Whether the bill line item has been billed."
    )
    markup: Optional[Decimal] = Field(
        default=None,
        description="The markup percentage of the bill line item (e.g., 0.10 for 10%)."
    )
    price: Optional[Decimal] = Field(
        default=None,
        description="The price of the bill line item (Amount * (1 + Markup))."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the bill line item is a draft."
    )


class BillLineItemUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the bill line item (base64 encoded)."
    )
    bill_public_id: str = Field(
        description="The bill public ID of the bill line item."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The sub cost code ID of the bill line item."
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description="The project public ID of the bill line item."
    )
    description: Optional[str] = Field(
        default=None,
        description="The description of the bill line item."
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        max_digits=18,
        # Bounded to what DECIMAL(18,4) can actually hold. `max_digits` alone is
        # NOT that bound — it counts TOTAL digits, so it accepts 123456789012345
        # (15 integer digits), which overflows the column at the DB boundary.
        # The real ceiling is 14 integer digits. `decimal_places` is deliberately
        # NOT set: it would reject 5.016667, and qbo.BillLine.Qty is DECIMAL(18,6)
        # carrying exactly that (sixths of an hour). Magnitude bounded, scale not.
        le=Decimal("99999999999999.9999"),
        ge=Decimal("-99999999999999.9999"),
        description="The quantity of the bill line item (fractional allowed, e.g. 5.25)."
    )
    rate: Optional[Decimal] = Field(
        default=None,
        description="The rate per unit of the bill line item."
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="The amount of the bill line item (Quantity * Rate)."
    )
    is_billable: Optional[bool] = Field(
        default=None,
        description="Whether the bill line item is billable."
    )
    is_billed: Optional[bool] = Field(
        default=None,
        description="Whether the bill line item has been billed."
    )
    markup: Optional[Decimal] = Field(
        default=None,
        description="The markup percentage of the bill line item (e.g., 0.10 for 10%)."
    )
    price: Optional[Decimal] = Field(
        default=None,
        description="The price of the bill line item (Amount * (1 + Markup))."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the bill line item is a draft."
    )