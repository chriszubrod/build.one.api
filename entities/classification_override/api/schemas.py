# Python Standard Library Imports
from typing import Optional

# Third-Party Imports
from pydantic import BaseModel, Field


class ClassificationOverrideCreate(BaseModel):
    """Request schema for creating a classification override."""
    match_type: str = Field(
        ...,
        description="Match type: 'email' for exact address, 'domain' for all addresses at a domain",
        pattern=r"^(email|domain)$",
    )
    match_value: str = Field(
        ...,
        description="Email address or domain to match (e.g., 'ar@acme.com' or 'acme.com')",
        min_length=1,
        max_length=320,
    )
    classification_type: str = Field(
        ...,
        description="Classification type to assign: BILL_DOCUMENT, BILL_CREDIT_DOCUMENT, EXPENSE_DOCUMENT, EXPENSE_REFUND_DOCUMENT, or UNKNOWN",
        pattern=r"^(BILL_DOCUMENT|BILL_CREDIT_DOCUMENT|EXPENSE_DOCUMENT|EXPENSE_REFUND_DOCUMENT|UNKNOWN)$",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional notes about why this override exists",
        max_length=500,
    )
    is_active: bool = Field(
        default=True,
        description="Whether this override is active",
    )


class ClassificationOverrideUpdate(BaseModel):
    """Request schema for updating a classification override."""
    row_version: str = Field(
        ...,
        description="Base64-encoded row version for optimistic concurrency",
    )
    match_type: str = Field(
        ...,
        pattern=r"^(email|domain)$",
    )
    match_value: str = Field(
        ...,
        min_length=1,
        max_length=320,
    )
    classification_type: str = Field(
        ...,
        pattern=r"^(BILL_DOCUMENT|BILL_CREDIT_DOCUMENT|EXPENSE_DOCUMENT|EXPENSE_REFUND_DOCUMENT|UNKNOWN)$",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=500,
    )
    is_active: bool = Field(default=True)
