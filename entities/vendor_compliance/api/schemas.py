# Python Standard Library Imports
from typing import Literal, Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


DocumentTypeLiteral = Literal["CERTIFICATE_OF_INSURANCE"]

VerificationStatusLiteral = Literal["Received", "Verified", "Rejected"]


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
