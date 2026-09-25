# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ExpenseLineItemCreate(BaseModel):
    expense_public_id: str = Field(
        description="The expense public ID of the expense line item."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The sub cost code ID of the expense line item."
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description="The project public ID of the expense line item."
    )
    description: Optional[str] = Field(
        default=None,
        description="The description of the expense line item."
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        max_digits=18,
        # Bounded to what DECIMAL(18,4) can actually hold. `max_digits` alone is
        # NOT that bound — it counts TOTAL digits, so it accepts 123456789012345
        # (15 integer digits), which overflows the column at the DB boundary.
        # The real ceiling is 14 integer digits. `decimal_places` is deliberately
        # NOT set. ⚠️ The reason is NOT "the QBO pull would break" — the pull calls
        # the service directly and never sees this schema, so a scale bound here
        # could not block it. The reason is edge-vs-internal consistency: the
        # system behind this API stores 6dp values (qbo.PurchaseLine.Qty is
        # DECIMAL(18,6)) and rounds them into DECIMAL(18,4) at write, so rejecting
        # 5.016667 at the edge would make the door stricter than the room behind
        # it. Magnitude is bounded; scale is left to the column.
        le=Decimal("99999999999999.9999"),
        ge=Decimal("-99999999999999.9999"),
        description="The quantity of the expense line item (fractional allowed, e.g. 5.25)."
    )
    rate: Optional[Decimal] = Field(
        default=None,
        description="The rate per unit of the expense line item."
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="The amount of the expense line item (Quantity * Rate)."
    )
    is_billable: Optional[bool] = Field(
        default=None,
        description="Whether the expense line item is billable."
    )
    is_billed: Optional[bool] = Field(
        default=None,
        description="Whether the expense line item has been billed."
    )
    markup: Optional[Decimal] = Field(
        default=None,
        description="The markup percentage of the expense line item (e.g., 0.10 for 10%)."
    )
    price: Optional[Decimal] = Field(
        default=None,
        description="The price of the expense line item (Amount * (1 + Markup))."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the expense line item is a draft."
    )


class ExpenseLineItemUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the expense line item (base64 encoded)."
    )
    expense_public_id: str = Field(
        description="The expense public ID of the expense line item."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The sub cost code ID of the expense line item."
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description="The project public ID of the expense line item."
    )
    description: Optional[str] = Field(
        default=None,
        description="The description of the expense line item."
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        max_digits=18,
        # Bounded to what DECIMAL(18,4) can actually hold. `max_digits` alone is
        # NOT that bound — it counts TOTAL digits, so it accepts 123456789012345
        # (15 integer digits), which overflows the column at the DB boundary.
        # The real ceiling is 14 integer digits. `decimal_places` is deliberately
        # NOT set. ⚠️ The reason is NOT "the QBO pull would break" — the pull calls
        # the service directly and never sees this schema, so a scale bound here
        # could not block it. The reason is edge-vs-internal consistency: the
        # system behind this API stores 6dp values (qbo.PurchaseLine.Qty is
        # DECIMAL(18,6)) and rounds them into DECIMAL(18,4) at write, so rejecting
        # 5.016667 at the edge would make the door stricter than the room behind
        # it. Magnitude is bounded; scale is left to the column.
        le=Decimal("99999999999999.9999"),
        ge=Decimal("-99999999999999.9999"),
        description="The quantity of the expense line item (fractional allowed, e.g. 5.25)."
    )
    rate: Optional[Decimal] = Field(
        default=None,
        description="The rate per unit of the expense line item."
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="The amount of the expense line item (Quantity * Rate)."
    )
    is_billable: Optional[bool] = Field(
        default=None,
        description="Whether the expense line item is billable."
    )
    is_billed: Optional[bool] = Field(
        default=None,
        description="Whether the expense line item has been billed."
    )
    markup: Optional[Decimal] = Field(
        default=None,
        description="The markup percentage of the expense line item (e.g., 0.10 for 10%)."
    )
    price: Optional[Decimal] = Field(
        default=None,
        description="The price of the expense line item (Amount * (1 + Markup))."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the expense line item is a draft."
    )
