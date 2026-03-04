# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class InvoiceLineItemAttachmentCreate(BaseModel):
    invoice_line_item_public_id: str = Field(
        min_length=1,
        description="The public ID of the invoice line item."
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment."
    )


class InvoiceLineItemAttachmentUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the invoice line item attachment (base64 encoded)."
    )
    invoice_line_item_public_id: str = Field(
        min_length=1,
        description="The public ID of the invoice line item."
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment."
    )
