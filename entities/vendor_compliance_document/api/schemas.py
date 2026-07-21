# Python Standard Library Imports
from typing import Literal, Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


DocumentTypeLiteral = Literal[
    "BUSINESS_LICENSE",
    "CONTRACTORS_LICENSE",
    "CERTIFICATE_OF_INSURANCE",
]

VerificationStatusLiteral = Literal["Received", "Verified", "Rejected"]


class VendorComplianceDocumentCreate(BaseModel):
    vendor_public_id: str = Field(
        min_length=1,
        description="The public ID of the vendor.",
    )
    document_type: DocumentTypeLiteral = Field(
        description="The compliance document type.",
    )
    issuing_authority: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The issuing authority for the document.",
    )
    document_number: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The document number.",
    )
    classification: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The document classification.",
    )
    issue_date: Optional[str] = Field(
        default=None,
        description="The issue date (ISO YYYY-MM-DD).",
    )
    expiry_date: Optional[str] = Field(
        default=None,
        description="The expiry date (ISO YYYY-MM-DD).",
    )
    attachment_public_id: Optional[str] = Field(
        default=None,
        description="The public ID of the uploaded attachment PDF.",
    )
    verification_status: VerificationStatusLiteral = Field(
        default="Received",
        description="The verification status of the document.",
    )


class VendorComplianceDocumentUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the vendor compliance document (base64 encoded).",
    )
    issuing_authority: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The issuing authority for the document.",
    )
    document_number: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The document number.",
    )
    classification: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The document classification.",
    )
    issue_date: Optional[str] = Field(
        default=None,
        description="The issue date (ISO YYYY-MM-DD).",
    )
    expiry_date: Optional[str] = Field(
        default=None,
        description="The expiry date (ISO YYYY-MM-DD).",
    )
    attachment_public_id: Optional[str] = Field(
        default=None,
        description="The public ID of the uploaded attachment PDF.",
    )
    verification_status: Optional[VerificationStatusLiteral] = Field(
        default=None,
        description="The verification status of the document.",
    )


class VendorFolderLinkRequest(BaseModel):
    drive_public_id: str = Field(min_length=1)
    graph_item_id: str = Field(min_length=1)


class VendorFolderImportRequest(BaseModel):
    graph_item_id: str = Field(min_length=1)
    document_type: DocumentTypeLiteral
    issuing_authority: Optional[str] = None
    document_number: Optional[str] = None
    classification: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None


class BoxVendorFolderLinkRequest(BaseModel):
    box_folder_id: str


class BoxVendorFolderImportRequest(BaseModel):
    box_file_id: str
    document_type: DocumentTypeLiteral
    issuing_authority: Optional[str] = None
    document_number: Optional[str] = None
    classification: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
