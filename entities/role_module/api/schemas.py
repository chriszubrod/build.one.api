# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class RoleModuleCreate(BaseModel):
    role_id: int = Field(
        description="The ID of the role."
    )
    module_id: int = Field(
        description="The ID of the module."
    )
    can_create: bool = Field(default=False, description="Permission to create.")
    can_read: bool = Field(default=False, description="Permission to read.")
    can_update: bool = Field(default=False, description="Permission to update.")
    can_delete: bool = Field(default=False, description="Permission to delete.")
    can_submit: bool = Field(default=False, description="Permission to submit.")
    can_approve: bool = Field(default=False, description="Permission to approve.")
    can_complete: bool = Field(default=False, description="Permission to complete.")


class RoleModuleUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the role module (base64 encoded)."
    )
    role_id: int = Field(
        description="The ID of the role."
    )
    module_id: int = Field(
        description="The ID of the module."
    )
    can_create: bool = Field(default=False, description="Permission to create.")
    can_read: bool = Field(default=False, description="Permission to read.")
    can_update: bool = Field(default=False, description="Permission to update.")
    can_delete: bool = Field(default=False, description="Permission to delete.")
    can_submit: bool = Field(default=False, description="Permission to submit.")
    can_approve: bool = Field(default=False, description="Permission to approve.")
    can_complete: bool = Field(default=False, description="Permission to complete.")
