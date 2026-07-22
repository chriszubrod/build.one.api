# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class CertificateOfInsuranceCreate(BaseModel):
    vendor_public_id: str = Field(
        min_length=1,
        description="The public ID of the vendor.",
    )
    issuing_authority: Optional[str] = Field(
        default=None,
        description="The producer/agency on the ACORD cert.",
    )
    issue_date: Optional[str] = Field(
        default=None,
        description="The issue date.",
    )
    attachment_id: Optional[int] = Field(
        default=None,
        description="The internal ID of the cert PDF attachment.",
    )
    verification_status: Optional[str] = Field(
        default="Received",
        description="Verification status of the certificate.",
    )


class CertificateOfInsuranceUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the certificate of insurance (base64 encoded).",
    )
    issuing_authority: Optional[str] = Field(
        default=None,
        description="The producer/agency on the ACORD cert.",
    )
    issue_date: Optional[str] = Field(
        default=None,
        description="The issue date.",
    )
    attachment_id: Optional[int] = Field(
        default=None,
        description="The internal ID of the cert PDF attachment.",
    )
    verification_status: Optional[str] = Field(
        default=None,
        description="Verification status of the certificate.",
    )
