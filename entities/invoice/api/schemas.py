# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class InvoiceCreate(BaseModel):
    project_public_id: str = Field(
        description="The project public ID for this invoice."
    )
    payment_term_public_id: Optional[str] = Field(
        default=None,
        description="The payment term public ID (e.g. Due On Receipt)."
    )
    invoice_date: str = Field(
        description="The invoice date."
    )
    due_date: str = Field(
        description="The due date."
    )
    invoice_number: str = Field(
        max_length=50,
        description="The invoice number."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the invoice."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo for the invoice."
    )
    # is_draft REMOVED (U-458). Design §4.2: create-as-completed is accepted
    # "ONLY under system_authz" (the QBO pull connectors and CLI sync, which
    # call the SERVICE layer and keep their parameter). Exposing it here let
    # a can_create caller mint an already-completed document that never went
    # through completion — so its AP never reached QBO/SharePoint/Excel/Box,
    # and no lifecycle gate ever saw it. Same class as the U-446d P0 on
    # create_bill. Pydantic ignores unknown fields, so a client still
    # sending it gets a 200 and the value is ignored.


class InvoiceUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the invoice (base64 encoded)."
    )
    project_public_id: str = Field(
        description="The project public ID for this invoice."
    )
    payment_term_public_id: Optional[str] = Field(
        default=None,
        description="The payment term public ID."
    )
    invoice_date: str = Field(
        description="The invoice date."
    )
    due_date: str = Field(
        description="The due date."
    )
    invoice_number: str = Field(
        max_length=50,
        description="The invoice number."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the invoice."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo for the invoice."
    )
    # is_draft REMOVED (U-458). Completing is POST /complete/* only, which is
    # where the lifecycle gate lives. Pydantic ignores unknown fields, so a
    # client that still sends it (build.one.web echoes the stored value on
    # every save) gets a 200 and the field is ignored — not a 422.
