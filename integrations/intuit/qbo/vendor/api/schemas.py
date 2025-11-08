# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class QboVendorCreate(BaseModel):
    id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The ID of the QBO vendor.",
    )
    sync_token: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The sync token of the QBO vendor.",
    )
    display_name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The display name of the QBO vendor.",
    )
    vendor_1099: Optional[int] = Field(
        default=None,
        description="The 1099 status of the QBO vendor.",
    )
    company_name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The company name of the QBO vendor.",
    )
    tax_identifier: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The tax identifier of the QBO vendor.",
    )
    print_on_check_name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The print on check name of the QBO vendor.",
    )
    bill_addr_id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The bill address ID of the QBO vendor.",
    )


class QboVendorUpdate(BaseModel):
    id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The ID of the QBO vendor.",
    )
    sync_token: Optional[str] = Field(
        default=None,
        max_length=255,
        description="The sync token of the QBO vendor.",
    )
    display_name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The display name of the QBO vendor.",
    )
    vendor_1099: Optional[int] = Field(
        default=None,
        description="The 1099 status of the QBO vendor.",
    )
    company_name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The company name of the QBO vendor.",
    )
    tax_identifier: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The tax identifier of the QBO vendor.",
    )
    print_on_check_name: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The print on check name of the QBO vendor.",
    )
    bill_addr_id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="The bill address ID of the QBO vendor.",
    )
