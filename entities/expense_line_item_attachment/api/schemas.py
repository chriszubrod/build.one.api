# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ExpenseLineItemAttachmentCreate(BaseModel):
    expense_line_item_public_id: str = Field(
        min_length=1,
        description="The public ID of the expense line item."
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment."
    )


class ExpenseLineItemAttachmentUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the expense line item attachment (base64 encoded)."
    )
    expense_line_item_public_id: str = Field(
        min_length=1,
        description="The public ID of the expense line item."
    )
    attachment_public_id: str = Field(
        min_length=1,
        description="The public ID of the attachment."
    )
