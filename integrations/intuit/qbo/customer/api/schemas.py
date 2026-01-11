# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboCustomerSync(BaseModel):
    realm_id: str = Field(
        description="QBO company realm ID.",
    )
    last_updated_time: Optional[str] = Field(
        default=None,
        description="Optional ISO format datetime. If provided, only sync customers updated after this time.",
    )
    sync_to_modules: bool = Field(
        default=False,
        description="If True, also sync to Customer/Project modules.",
    )
