# Python Standard Library Imports
from typing import Any, Dict, List, Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel


class QboVendorBase(_QboBaseModel):
    display_name: Optional[str] = Field(default=None, alias="DisplayName")
    company_name: Optional[str] = Field(default=None, alias="CompanyName")
    tax_identifier: Optional[str] = Field(default=None, alias="TaxIdentifier")
    print_on_check_name: Optional[str] = Field(default=None, alias="PrintOnCheckName")
    bill_addr_id: Optional[str] = Field(default=None, alias="BillAddrId")
    vendor_1099: Optional[int] = Field(default=None, alias="Vendor1099")
    phone: Optional[Dict[str, Any]] = Field(default=None, alias="PrimaryPhone")
    email: Optional[Dict[str, Any]] = Field(default=None, alias="PrimaryEmailAddr")


class QboVendorCreate(QboVendorBase):
    pass


class QboVendorUpdate(QboVendorBase):
    id: str = Field(alias="Id")
    sync_token: str = Field(alias="SyncToken")


class QboVendor(QboVendorUpdate):
    metadata: Optional[Dict[str, Any]] = Field(default=None, alias="MetaData")


class QboVendorResponse(_QboBaseModel):
    vendor: QboVendor = Field(alias="Vendor")


class QboVendorQueryResponse(_QboBaseModel):
    vendors: List[QboVendor] = Field(default_factory=list, alias="Vendor")
    start_position: Optional[int] = Field(default=None, alias="startPosition")
    max_results: Optional[int] = Field(default=None, alias="maxResults")
