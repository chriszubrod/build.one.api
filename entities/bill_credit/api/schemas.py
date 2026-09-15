# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class BillCreditCreate(BaseModel):
    vendor_public_id: str = Field(
        description="The vendor public ID of the bill credit."
    )
    credit_date: str = Field(
        description="The credit date."
    )
    credit_number: str = Field(
        max_length=50,
        description="The credit number (vendor credit reference)."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the bill credit."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the bill credit."
    )
    # is_draft REMOVED (U-458). Design §4.2: create-as-completed is accepted
    # "ONLY under system_authz" (the QBO pull connectors and CLI sync, which
    # call the SERVICE layer and keep their parameter). Exposing it here let
    # a can_create caller mint an already-completed document that never went
    # through completion — so its AP never reached QBO/SharePoint/Excel/Box,
    # and no lifecycle gate ever saw it. Same class as the U-446d P0 on
    # create_bill. Pydantic ignores unknown fields, so a client still
    # sending it gets a 200 and the value is ignored.


class BillCreditUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the bill credit (base64 encoded)."
    )
    vendor_public_id: str = Field(
        description="The vendor public ID of the bill credit."
    )
    credit_date: str = Field(
        description="The credit date."
    )
    credit_number: str = Field(
        max_length=50,
        description="The credit number (vendor credit reference)."
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description="The total amount of the bill credit."
    )
    memo: Optional[str] = Field(
        default=None,
        description="The memo of the bill credit."
    )
    # is_draft REMOVED (U-458). Completing is POST /complete/* only, which is
    # where the lifecycle gate lives. Pydantic ignores unknown fields, so a
    # client that still sends it (build.one.web echoes the stored value on
    # every save) gets a 200 and the field is ignored — not a 422.
