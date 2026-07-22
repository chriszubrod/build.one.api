# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BusinessLicenseAttachmentCreate(BaseModel):
    business_license_public_id: str = Field(
        min_length=1,
        description="The public ID of the business license.",
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment.",
    )
