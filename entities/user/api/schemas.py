# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class UserCreate(BaseModel):
    firstname: str = Field(
        min_length=1,
        max_length=50,
        description="The firstname of the user."
    )
    lastname: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The lastname of the user."
    )


class UserUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the user (base64 encoded)."
    )
    firstname: str = Field(
        min_length=1,
        max_length=50,
        description="The firstname of the user."
    )
    lastname: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The lastname of the user."
    )


class UserWorkerLinkUpdate(BaseModel):
    """Set the User's worker (Employee XOR Vendor) linkage.

    Pass worker_type=null + worker_public_id=null to clear the link.
    """
    row_version: str = Field(
        description="The row version of the user (base64 encoded)."
    )
    worker_type: Optional[str] = Field(
        default=None,
        description="'employee' | 'vendor' | null (clears).",
    )
    worker_public_id: Optional[str] = Field(
        default=None,
        description="PublicId of the Employee or Vendor row to link. Required when worker_type is non-null.",
    )
