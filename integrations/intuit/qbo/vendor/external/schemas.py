# Python Standard Library Imports
from typing import Any, Dict, List, Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field, field_validator

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel
from integrations.intuit.qbo.customer.external.schemas import (
    QboPhysicalAddress,
    QboEmailAddress,
    QboTelephoneNumber,
)


class QboVendorBase(_QboBaseModel):
    """
    Base Vendor fields from QBO API.
    """
    display_name: Optional[str] = Field(default=None, alias="DisplayName")
    title: Optional[str] = Field(default=None, alias="Title")
    given_name: Optional[str] = Field(default=None, alias="GivenName")
    middle_name: Optional[str] = Field(default=None, alias="MiddleName")
    family_name: Optional[str] = Field(default=None, alias="FamilyName")
    suffix: Optional[str] = Field(default=None, alias="Suffix")
    company_name: Optional[str] = Field(default=None, alias="CompanyName")
    print_on_check_name: Optional[str] = Field(default=None, alias="PrintOnCheckName")
    tax_identifier: Optional[str] = Field(default=None, alias="TaxIdentifier")
    vendor_1099: Optional[bool] = Field(default=None, alias="Vendor1099")
    active: Optional[bool] = Field(default=None, alias="Active")
    primary_email_addr: Optional[QboEmailAddress] = Field(default=None, alias="PrimaryEmailAddr")
    primary_phone: Optional[QboTelephoneNumber] = Field(default=None, alias="PrimaryPhone")
    mobile: Optional[QboTelephoneNumber] = Field(default=None, alias="Mobile")
    fax: Optional[QboTelephoneNumber] = Field(default=None, alias="Fax")
    bill_addr: Optional[QboPhysicalAddress] = Field(default=None, alias="BillAddr")
    balance: Optional[Decimal] = Field(default=None, alias="Balance")
    acct_num: Optional[str] = Field(default=None, alias="AcctNum")
    web_addr: Optional[Dict[str, Any]] = Field(default=None, alias="WebAddr")
    domain: Optional[str] = Field(default=None, alias="domain")
    sparse: Optional[bool] = Field(default=None, alias="sparse")


class QboVendorCreate(QboVendorBase):
    pass


class QboVendorUpdate(QboVendorBase):
    id: str = Field(alias="Id")
    sync_token: str = Field(alias="SyncToken")


class QboVendor(QboVendorUpdate):
    """
    Full Vendor model with Id, SyncToken, and MetaData.
    """
    metadata: Optional[Dict[str, Any]] = Field(default=None, alias="MetaData")

    @field_validator('id', mode='before')
    @classmethod
    def convert_id_to_string(cls, v):
        """
        Convert QBO Id to string if it comes as an integer.
        QBO may return Id as either integer or string, but we store it as string.
        """
        if v is None:
            return None
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            return v
        return str(v)


class QboVendorResponse(_QboBaseModel):
    """
    Wrapper for QBO Vendor API response.
    """
    vendor: QboVendor = Field(alias="Vendor")


class QboVendorQueryResponse(_QboBaseModel):
    """
    Wrapper for QBO Vendor query response.
    """
    vendors: List[QboVendor] = Field(default_factory=list, alias="Vendor")
    start_position: Optional[int] = Field(default=None, alias="startPosition")
    max_results: Optional[int] = Field(default=None, alias="maxResults")
