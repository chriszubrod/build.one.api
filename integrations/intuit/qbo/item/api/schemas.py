# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboItemSync(BaseModel):
    realm_id: str = Field(
        description="QBO company realm ID.",
    )
    last_updated_time: Optional[str] = Field(
        default=None,
        description="Optional ISO format datetime. If provided, only sync items updated after this time.",
    )
    sync_to_modules: bool = Field(
        default=True,
        description="If True, also sync to CostCode/SubCostCode modules.",
    )

