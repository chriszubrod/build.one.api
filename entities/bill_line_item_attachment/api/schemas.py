# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BillLineItemAttachmentCreate(BaseModel):
    bill_line_item_public_id: str = Field(
        min_length=1,
        description="The public ID of the bill line item."
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment."
    )


class BillLineItemAttachmentUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the bill line item attachment (base64 encoded)."
    )
    bill_line_item_public_id: str = Field(
        min_length=1,
        description="The public ID of the bill line item."
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment."
    )
