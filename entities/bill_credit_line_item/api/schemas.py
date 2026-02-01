# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BillCreditLineItemCreate(BaseModel):
    bill_credit_public_id: str = Field(
        description="The bill credit public ID."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The sub cost code ID."
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description="The project public ID."
    )
    description: Optional[str] = Field(
        default=None,
        description="The description of the line item."
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        description="The quantity."
    )
    unit_price: Optional[Decimal] = Field(
        default=None,
        description="The unit price."
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="The amount."
    )
    is_billable: Optional[bool] = Field(
        default=None,
        description="Whether this line item is billable."
    )
    billable_amount: Optional[Decimal] = Field(
        default=None,
        description="The billable amount."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the line item is a draft."
    )


class BillCreditLineItemUpdate(BaseModel):
    row_version: str = Field(
        description="The row version (base64 encoded)."
    )
    bill_credit_public_id: str = Field(
        description="The bill credit public ID."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The sub cost code ID."
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description="The project public ID."
    )
    description: Optional[str] = Field(
        default=None,
        description="The description of the line item."
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        description="The quantity."
    )
    unit_price: Optional[Decimal] = Field(
        default=None,
        description="The unit price."
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="The amount."
    )
    is_billable: Optional[bool] = Field(
        default=None,
        description="Whether this line item is billable."
    )
    billable_amount: Optional[Decimal] = Field(
        default=None,
        description="The billable amount."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the line item is a draft."
    )
