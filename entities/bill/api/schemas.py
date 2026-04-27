# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BillCreate(BaseModel):
    vendor_public_id: str = Field(
        description="The vendor public ID of the bill."
    )
    payment_term_public_id: Optional[str] = Field(
        default=None,
        description="The payment term public ID of the bill."
    )
    bill_date: str = Field(
        description="The bill date of the bill."
    )
    due_date: str = Field(
        description="The due date of the bill."
    )
    bill_number: str = Field(
        max_length=50,
        description="The bill number of the bill."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the bill."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the bill."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the bill is a draft."
    )
    attachment_public_id: str = Field(
        description=(
            "REQUIRED. UUID of an Attachment row (must be a PDF) that the "
            "client uploaded via POST /api/v1/upload/attachment. Server "
            "creates a placeholder BillLineItem and links the attachment "
            "to it. Universal rule — agents and scripts must satisfy too."
        )
    )
    source_email_message_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional UUID of the EmailMessage that produced this bill. "
            "Set by the email-agent pipeline so we can trace any draft "
            "back to its source email. Manual creators leave this blank."
        )
    )


class BillUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the bill (base64 encoded)."
    )
    vendor_public_id: str = Field(
        description="The vendor public ID of the bill."
    )
    payment_term_public_id: Optional[str] = Field(
        default=None,
        description="The payment term public ID of the bill."
    )
    bill_date: str = Field(
        description="The bill date of the bill."
    )
    due_date: str = Field(
        description="The due date of the bill."
    )
    bill_number: str = Field(
        max_length=50,
        description="The bill number of the bill."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the bill."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the bill."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the bill is a draft."
    )