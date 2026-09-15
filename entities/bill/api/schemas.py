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
    # is_draft REMOVED (U-458). Design §4.2: create-as-completed is accepted
    # "ONLY under system_authz" (the QBO pull connectors and CLI sync, which
    # call the SERVICE layer and keep their parameter). Exposing it here let
    # a can_create caller mint an already-completed document that never went
    # through completion — so its AP never reached QBO/SharePoint/Excel/Box,
    # and no lifecycle gate ever saw it. Same class as the U-446d P0 on
    # create_bill. Pydantic ignores unknown fields, so a client still
    # sending it gets a 200 and the value is ignored.
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
    submit_for_review: Optional[bool] = Field(
        default=None,
        description=(
            "Manual UI's per-button auto-Submit override. True/None → use "
            "the standard gate (auto-Submit fires when a draft has a "
            "populated line item with project_public_id). False → suppress "
            "auto-Submit even when the gate would otherwise fire (used by "
            "the Save For Later button so the user can save a coded draft "
            "without notifying reviewers yet). Email pipeline + scripts "
            "leave this unset → existing behavior preserved."
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
    # is_draft REMOVED (U-458). Completing is POST /complete/bill only.
    # Bill was NOT immune via its computed column: UpdateBillById translates
    # @IsDraft = 0 into Status = 'completed'. Pydantic ignores unknown fields,
    # so a client still sending it gets a 200 and the value is ignored.