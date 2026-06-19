# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ExpenseCreate(BaseModel):
    vendor_public_id: str = Field(
        description="The vendor public ID of the expense."
    )
    expense_date: str = Field(
        description="The expense date."
    )
    reference_number: str = Field(
        max_length=50,
        description="The reference number (receipt or transaction reference)."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the expense."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the expense."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the expense is a draft."
    )
    is_credit: Optional[bool] = Field(
        default=False,
        description="Whether the expense is a credit card credit (refund)."
    )
    attachment_public_id: str = Field(
        description=(
            "REQUIRED. UUID of an Attachment row (must be a PDF) that the "
            "client uploaded via POST /api/v1/upload/attachment. Server "
            "creates a placeholder ExpenseLineItem and links the receipt "
            "to it. Mirrors the universal Bill rule. (The internal QBO "
            "Purchase pull bypasses this boundary at the service layer.)"
        )
    )
    source_email_message_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional UUID of the EmailMessage that produced this expense. "
            "Set by the receipt-intake email pipeline so a draft can be "
            "traced back to its source email. Manual creators leave blank."
        )
    )
    # ───── Inline summary line item ──────────────────────────────────────
    # When provided, the server populates the placeholder ExpenseLineItem
    # (the one that always carries the receipt) with these values instead
    # of leaving it blank. Avoids a follow-up add_expense_line_items call.
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


class ExpenseUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the expense (base64 encoded)."
    )
    vendor_public_id: str = Field(
        description="The vendor public ID of the expense."
    )
    expense_date: str = Field(
        description="The expense date."
    )
    reference_number: str = Field(
        max_length=50,
        description="The reference number (receipt or transaction reference)."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the expense."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the expense."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the expense is a draft."
    )
    is_credit: Optional[bool] = Field(
        default=None,
        description="Whether the expense is a credit card credit (refund)."
    )
