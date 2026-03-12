# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ContactCreate(BaseModel):
    email: Optional[str] = Field(default=None, max_length=255)
    office_phone: Optional[str] = Field(default=None, max_length=50)
    mobile_phone: Optional[str] = Field(default=None, max_length=50)
    fax: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None)
    user_id: Optional[int] = Field(default=None)
    company_id: Optional[int] = Field(default=None)
    customer_id: Optional[int] = Field(default=None)
    project_id: Optional[int] = Field(default=None)
    vendor_id: Optional[int] = Field(default=None)


class ContactUpdate(BaseModel):
    row_version: str = Field(
        description="The row version of the contact (base64 encoded)."
    )
    email: Optional[str] = Field(default=None, max_length=255)
    office_phone: Optional[str] = Field(default=None, max_length=50)
    mobile_phone: Optional[str] = Field(default=None, max_length=50)
    fax: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None)
