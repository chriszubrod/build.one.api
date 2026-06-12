# Python Standard Library Imports
from decimal import Decimal
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field


class BudgetLineItemCreate(BaseModel):
    budget_revision_public_id: str = Field(..., description="PublicId of the parent BudgetRevision (must be draft).")
    sub_cost_code_id: Optional[int] = Field(default=None, description="Internal id of the SubCostCode.")
    description: Optional[str] = Field(default=None, max_length=500)
    quantity: Optional[Decimal] = Field(default=None, description="DECIMAL(18,4). Negative values legal (CO deltas).")
    rate: Optional[Decimal] = Field(default=None, description="DECIMAL(18,4).")
    amount: Optional[Decimal] = Field(default=None, description="DECIMAL(18,2). Pre-markup cost basis.")
    markup: Optional[Decimal] = Field(default=None, description="DECIMAL(18,4).")
    price: Optional[Decimal] = Field(default=None, description="DECIMAL(18,2). Contract value — client-computed in v1.")


class BudgetLineItemUpdate(BaseModel):
    """
    Full-row state — business fields are SET unconditionally (the
    auto-save grid sends the entire row each save; omitted/None fields
    are CLEARED, not preserved).
    """

    row_version: Optional[str] = Field(default=None, description="Base64 ROWVERSION for optimistic concurrency.")
    sub_cost_code_id: Optional[int] = Field(default=None)
    description: Optional[str] = Field(default=None, max_length=500)
    quantity: Optional[Decimal] = Field(default=None)
    rate: Optional[Decimal] = Field(default=None)
    amount: Optional[Decimal] = Field(default=None)
    markup: Optional[Decimal] = Field(default=None)
    price: Optional[Decimal] = Field(default=None)
