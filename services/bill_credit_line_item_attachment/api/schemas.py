# Python Standard Library Imports

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BillCreditLineItemAttachmentCreate(BaseModel):
    bill_credit_line_item_public_id: str = Field(
        description="The public ID of the bill credit line item."
    )
    attachment_public_id: str = Field(
        description="The public ID of the attachment."
    )
