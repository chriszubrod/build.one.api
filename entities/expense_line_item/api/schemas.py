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
    quantity: Optional[int] = Field(
        default=None,
        description="The quantity of the expense line item."
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
    quantity: Optional[int] = Field(
        default=None,
        description="The quantity of the expense line item."
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
