# Python Standard Library Imports
from typing import Any, Dict, Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel


class QboPhysicalAddressBase(_QboBaseModel):
    line1: Optional[str] = Field(default=None, alias="Line1")
    line2: Optional[str] = Field(default=None, alias="Line2")
    city: Optional[str] = Field(default=None, alias="City")
    country: Optional[str] = Field(default=None, alias="Country")
    country_sub_division_code: Optional[str] = Field(default=None, alias="CountrySubDivisionCode")
    postal_code: Optional[str] = Field(default=None, alias="PostalCode")


class QboPhysicalAddress(QboPhysicalAddressBase):
    pass


class QboCompanyInfoResponse(_QboBaseModel):
    company_info: Dict[str, Any] = Field(alias="CompanyInfo")
    
    @property
    def physical_address(self) -> Optional[QboPhysicalAddress]:
        """
        Extract PhysicalAddress from CompanyInfo response.
        CompanyInfo contains CompanyAddr field which is the PhysicalAddress.
        """
        company_info = self.company_info
        if not company_info:
            return None
        
        # CompanyAddr is the field name in CompanyInfo that contains PhysicalAddress
        physical_address_data = company_info.get("CompanyAddr")
        if not physical_address_data:
            return None
        
        # Handle case where CompanyAddr might be None or empty dict
        if isinstance(physical_address_data, dict) and any(physical_address_data.values()):
            return QboPhysicalAddress(**physical_address_data)
        
        return None

