# Python Standard Library Imports
from decimal import Decimal
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# MINIMAL BY DESIGN: BuildersFeeRate is the only business field. The full contract
# model (contract value, change orders, retainage, dates, and the relationship to
# the existing Budget entity) is deferred to a formal design conversation.


class ContractCreate(BaseModel):
    project_id: int = Field(..., description="Internal id (BIGINT) of the parent Project.")
    builders_fee_rate: Optional[Decimal] = Field(
        default=None, description="DECIMAL(9,6) fraction — 0.100000 = 10%."
    )


class ContractUpdate(BaseModel):
    row_version: str = Field(
        ..., description="Base64 ROWVERSION for optimistic concurrency."
    )
    builders_fee_rate: Optional[Decimal] = Field(
        default=None, description="DECIMAL(9,6) fraction — 0.100000 = 10%."
    )
