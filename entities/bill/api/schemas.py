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
    # ───── Inline summary line item ──────────────────────────────────────
    # When provided, the server populates the placeholder BillLineItem
    # (the one that always carries the attachment) with these values
    # instead of leaving it blank. Avoids a follow-up update call from
    # agent-driven flows. Manual UI uploads typically leave these unset.
    line_description: Optional[str] = Field(
        default=None,
        description="Summary description for the placeholder line (~6 words)."
    )
    line_quantity: Optional[int] = Field(
        default=None,
        description="Quantity. Summary-line use typically passes 1."
    )
    line_rate: Optional[Decimal] = Field(
        default=None,
        description="Rate (often equals total_amount on a summary line)."
    )
    line_amount: Optional[Decimal] = Field(
        default=None,
        description="Amount = quantity × rate."
    )
    line_markup: Optional[Decimal] = Field(
        default=None,
        description="Markup decimal (0.10 = 10%). Null = no markup."
    )
    line_price: Optional[Decimal] = Field(
        default=None,
        description="Price = amount × (1 + markup). Equals amount when markup is null/0."
    )
    line_is_billable: Optional[bool] = Field(
        default=None,
        description="Defaults to True server-side when omitted."
    )
    line_sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="BIGINT — resolve via SubCostCode read tools first."
    )
    line_project_public_id: Optional[str] = Field(
        default=None,
        description="UUID of the Project for this line."
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