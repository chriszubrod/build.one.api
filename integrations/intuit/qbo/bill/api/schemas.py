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
        default=True,
        description="If True, also sync to Bill/BillLineItem modules. Defaults to True — a pull sync should always update local application data.",
    )


class QboBillPush(BaseModel):
    """Request schema for pushing a local Bill to QBO."""
    realm_id: str = Field(
        description="QBO company realm ID.",
    )
    sync_attachments: bool = Field(
        default=True,
        description="If True, also sync attachments for the bill to QBO.",
    )
