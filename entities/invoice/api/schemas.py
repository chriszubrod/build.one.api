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
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the invoice is a draft."
    )


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
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the invoice is a draft."
    )
