# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class SyncCreate(BaseModel):
    provider: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The integration provider name.",
    )
    env: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The provider environment (e.g., sandbox, production).",
    )
    entity: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The entity type or resource being synchronized.",
    )
    last_sync_datetime: Optional[str] = Field(
        default=None,
        description="The datetime of the last sync.",
    )

class SyncUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the sync (base64 encoded).",
    )
    provider: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The integration provider name.",
    )
    env: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The provider environment (e.g., sandbox, production).",
    )
    entity: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The entity type or resource being synchronized.",
    )
    last_sync_datetime: Optional[str] = Field(
        default=None,
        description="The datetime of the last sync.",
    )
