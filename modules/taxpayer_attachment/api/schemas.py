# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class TaxpayerAttachmentCreate(BaseModel):
    taxpayer_public_id: str = Field(
        min_length=1,
        description="The public ID of the taxpayer."
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment."
    )


class TaxpayerAttachmentUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the taxpayer attachment (base64 encoded)."
    )
    taxpayer_public_id: str = Field(
        min_length=1,
        description="The public ID of the taxpayer."
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment."
    )

