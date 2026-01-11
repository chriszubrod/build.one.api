# Python Standard Library Imports
from typing import Any, Dict, List, Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field, field_validator

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel


class QboRef(_QboBaseModel):
    """
    Generic reference object from QBO API.
    """
    value: Optional[str] = Field(default=None, alias="value")
    name: Optional[str] = Field(default=None, alias="name")


class QboPhysicalAddress(_QboBaseModel):
    """
    Physical address from QBO API.
    """
    id: Optional[str] = Field(default=None, alias="Id")
    line1: Optional[str] = Field(default=None, alias="Line1")
    line2: Optional[str] = Field(default=None, alias="Line2")
    line3: Optional[str] = Field(default=None, alias="Line3")
    line4: Optional[str] = Field(default=None, alias="Line4")
    line5: Optional[str] = Field(default=None, alias="Line5")
    city: Optional[str] = Field(default=None, alias="City")
    country: Optional[str] = Field(default=None, alias="Country")
    country_sub_division_code: Optional[str] = Field(default=None, alias="CountrySubDivisionCode")
    postal_code: Optional[str] = Field(default=None, alias="PostalCode")
    lat: Optional[str] = Field(default=None, alias="Lat")
    long: Optional[str] = Field(default=None, alias="Long")


class QboEmailAddress(_QboBaseModel):
    """
    Email address from QBO API.
    """
    address: Optional[str] = Field(default=None, alias="Address")


class QboTelephoneNumber(_QboBaseModel):
    """
    Telephone number from QBO API.
    """
    free_form_number: Optional[str] = Field(default=None, alias="FreeFormNumber")


class QboCustomerBase(_QboBaseModel):
    """
    Base Customer fields from QBO API.
    """
    display_name: Optional[str] = Field(default=None, alias="DisplayName")
    title: Optional[str] = Field(default=None, alias="Title")
    given_name: Optional[str] = Field(default=None, alias="GivenName")
    middle_name: Optional[str] = Field(default=None, alias="MiddleName")
    family_name: Optional[str] = Field(default=None, alias="FamilyName")
    suffix: Optional[str] = Field(default=None, alias="Suffix")
    company_name: Optional[str] = Field(default=None, alias="CompanyName")
    fully_qualified_name: Optional[str] = Field(default=None, alias="FullyQualifiedName")
    level: Optional[int] = Field(default=None, alias="Level")
    parent_ref: Optional[QboRef] = Field(default=None, alias="ParentRef")
    job: Optional[bool] = Field(default=None, alias="Job")
    active: Optional[bool] = Field(default=None, alias="Active")
    primary_email_addr: Optional[QboEmailAddress] = Field(default=None, alias="PrimaryEmailAddr")
    primary_phone: Optional[QboTelephoneNumber] = Field(default=None, alias="PrimaryPhone")
    mobile: Optional[QboTelephoneNumber] = Field(default=None, alias="Mobile")
    fax: Optional[QboTelephoneNumber] = Field(default=None, alias="Fax")
    bill_addr: Optional[QboPhysicalAddress] = Field(default=None, alias="BillAddr")
    ship_addr: Optional[QboPhysicalAddress] = Field(default=None, alias="ShipAddr")
    balance: Optional[Decimal] = Field(default=None, alias="Balance")
    balance_with_jobs: Optional[Decimal] = Field(default=None, alias="BalanceWithJobs")
    taxable: Optional[bool] = Field(default=None, alias="Taxable")
    notes: Optional[str] = Field(default=None, alias="Notes")
    print_on_check_name: Optional[str] = Field(default=None, alias="PrintOnCheckName")
    domain: Optional[str] = Field(default=None, alias="domain")
    sparse: Optional[bool] = Field(default=None, alias="sparse")


class QboCustomer(QboCustomerBase):
    """
    Full Customer model with Id, SyncToken, and MetaData.
    """
    id: Optional[str] = Field(default=None, alias="Id")
    sync_token: Optional[str] = Field(default=None, alias="SyncToken")
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


class QboCustomerResponse(_QboBaseModel):
    """
    Wrapper for QBO Customer API response.
    """
    customer: QboCustomer = Field(alias="Customer")


class QboCustomerQueryResponse(_QboBaseModel):
    """
    Wrapper for QBO Customer query response.
    """
    query_response: Dict[str, Any] = Field(alias="QueryResponse")

    def get_customers(self) -> List[QboCustomer]:
        """
        Extract Customer list from query response.
        """
        customers_data = self.query_response.get("Customer", [])
        if not customers_data:
            return []
        if isinstance(customers_data, dict):
            # Single customer returned as dict
            return [QboCustomer(**customers_data)]
        # Multiple customers returned as list
        return [QboCustomer(**customer) for customer in customers_data]

    def get_max_results(self) -> int:
        """
        Get maxResults from query response.
        """
        return self.query_response.get("maxResults", 0)

    def get_start_position(self) -> int:
        """
        Get startPosition from query response.
        """
        return self.query_response.get("startPosition", 1)
