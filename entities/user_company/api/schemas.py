# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class UserCompanyCreate(BaseModel):
    user_id: int = Field(description="The ID of the user.")
    company_id: int = Field(description="The ID of the company.")


class UserCompanyUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the user company (base64 encoded)."
    )
    user_id: int = Field(description="The ID of the user.")
    company_id: int = Field(description="The ID of the company.")
