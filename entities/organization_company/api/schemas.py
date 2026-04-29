# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class OrganizationCompanyCreate(BaseModel):
    organization_id: int = Field(description="The ID of the organization.")
    company_id: int = Field(description="The ID of the company.")


class OrganizationCompanyUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the organization company (base64 encoded)."
    )
    organization_id: int = Field(description="The ID of the organization.")
    company_id: int = Field(description="The ID of the company.")
