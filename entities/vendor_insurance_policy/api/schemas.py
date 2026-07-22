# Python Standard Library Imports
from typing import Literal, Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


CoverageTypeLiteral = Literal["GL", "WC", "OTHER"]


class VendorInsurancePolicyCreate(BaseModel):
    certificate_of_insurance_public_id: str = Field(
        min_length=1,
        description="The public ID of the parent Certificate of Insurance.",
    )
    coverage_type: CoverageTypeLiteral = Field(
        description="The insurance coverage type.",
    )
    carrier: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The insurance carrier.",
    )
    policy_number: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The policy number.",
    )
    each_occurrence: Optional[str] = Field(
        default=None,
        description="The each-occurrence limit (decimal string).",
    )
    aggregate: Optional[str] = Field(
        default=None,
        description="The aggregate limit (decimal string).",
    )
    effective_date: Optional[str] = Field(
        default=None,
        description="The effective date (ISO YYYY-MM-DD).",
    )
    expiry_date: Optional[str] = Field(
        default=None,
        description="The expiry date (ISO YYYY-MM-DD).",
    )


class VendorInsurancePolicyUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the vendor insurance policy (base64 encoded).",
    )
    coverage_type: Optional[CoverageTypeLiteral] = Field(
        default=None,
        description="The insurance coverage type.",
    )
    carrier: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The insurance carrier.",
    )
    policy_number: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The policy number.",
    )
    each_occurrence: Optional[str] = Field(
        default=None,
        description="The each-occurrence limit (decimal string).",
    )
    aggregate: Optional[str] = Field(
        default=None,
        description="The aggregate limit (decimal string).",
    )
    effective_date: Optional[str] = Field(
        default=None,
        description="The effective date (ISO YYYY-MM-DD).",
    )
    expiry_date: Optional[str] = Field(
        default=None,
        description="The expiry date (ISO YYYY-MM-DD).",
    )
