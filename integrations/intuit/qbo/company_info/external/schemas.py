# Python Standard Library Imports
from typing import Any, Dict, Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel


class QboPhysicalAddressRef(_QboBaseModel):
    """
    PhysicalAddress reference from QBO API (nested in CompanyInfo).
    """
    id: Optional[str] = Field(default=None, alias="Id")
    line1: Optional[str] = Field(default=None, alias="Line1")
    line2: Optional[str] = Field(default=None, alias="Line2")
    city: Optional[str] = Field(default=None, alias="City")
    country: Optional[str] = Field(default=None, alias="Country")
    country_sub_division_code: Optional[str] = Field(default=None, alias="CountrySubDivisionCode")
    postal_code: Optional[str] = Field(default=None, alias="PostalCode")


class QboCurrencyRef(_QboBaseModel):
    """
    Currency reference from QBO API.
    """
    value: Optional[str] = Field(default=None, alias="value")
    name: Optional[str] = Field(default=None, alias="name")


class QboEmailAddr(_QboBaseModel):
    """
    Email address from QBO API.
    """
    address: Optional[str] = Field(default=None, alias="Address")


class QboWebAddr(_QboBaseModel):
    """
    Web address from QBO API.
    """
    uri: Optional[str] = Field(default=None, alias="URI")


class QboCompanyInfoBase(_QboBaseModel):
    """
    Base CompanyInfo fields from QBO API.
    """
    company_name: Optional[str] = Field(default=None, alias="CompanyName")
    legal_name: Optional[str] = Field(default=None, alias="LegalName")
    company_addr: Optional[QboPhysicalAddressRef] = Field(default=None, alias="CompanyAddr")
    legal_addr: Optional[QboPhysicalAddressRef] = Field(default=None, alias="LegalAddr")
    customer_communication_addr: Optional[QboPhysicalAddressRef] = Field(default=None, alias="CustomerCommunicationAddr")
    tax_payer_id: Optional[str] = Field(default=None, alias="TaxPayerId")
    fiscal_year_start_month: Optional[int] = Field(default=None, alias="FiscalYearStartMonth")
    country: Optional[str] = Field(default=None, alias="Country")
    email: Optional[QboEmailAddr] = Field(default=None, alias="Email")
    web_addr: Optional[QboWebAddr] = Field(default=None, alias="WebAddr")
    currency_ref: Optional[QboCurrencyRef] = Field(default=None, alias="CurrencyRef")
    domain: Optional[str] = Field(default=None, alias="domain")
    sparse: Optional[bool] = Field(default=None, alias="sparse")


class QboCompanyInfo(QboCompanyInfoBase):
    """
    Full CompanyInfo model with Id, SyncToken, and MetaData.
    """
    id: Optional[str] = Field(default=None, alias="Id")
    sync_token: Optional[str] = Field(default=None, alias="SyncToken")
    metadata: Optional[Dict[str, Any]] = Field(default=None, alias="MetaData")


class QboCompanyInfoResponse(_QboBaseModel):
    """
    Wrapper for QBO CompanyInfo API response.
    """
    company_info: QboCompanyInfo = Field(alias="CompanyInfo")

