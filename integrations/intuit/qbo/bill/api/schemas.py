# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboBillSync(BaseModel):
    realm_id: str = Field(
        description="QBO company realm ID.",
    )
    last_updated_time: Optional[str] = Field(
        default=None,
        description="Optional ISO format datetime. If provided, only sync bills updated after this time.",
    )
    sync_to_modules: bool = Field(
        default=False,
        description="If True, also sync to Bill/BillLineItem modules.",
    )
