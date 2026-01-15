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
    quantity: Optional[int] = Field(
        default=None,
        description="The quantity of the bill line item."
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
    quantity: Optional[int] = Field(
        default=None,
        description="The quantity of the bill line item."
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