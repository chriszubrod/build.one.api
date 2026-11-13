# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, ConfigDict, Field


class DrawPacketSettingUpdate(BaseModel):
    """Full replace: a switch left out (or null) resets to the house default."""

    model_config = ConfigDict(extra="forbid")

    row_version: Optional[str] = Field(
        default=None,
        max_length=16,
        description="Base64 ROWVERSION from the last read. Omit only for the project's first save.",
    )
    show_contractor_statement: Optional[bool] = None
    show_owner_approval: Optional[bool] = None
    show_architect_certificate: Optional[bool] = None
    show_notary: Optional[bool] = None
