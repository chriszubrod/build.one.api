# Python Standard Library Imports
from typing import List, Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Local Imports


class QboReferenceType(BaseModel):
    """QBO reference type for entity references."""
    model_config = ConfigDict(populate_by_name=True)
    
    value: str = Field(alias="value")
    name: Optional[str] = Field(default=None, alias="name")
    
    @field_validator('value', mode='before')
    @classmethod
    def convert_value_to_string(cls, v):
        if v is not None:
            return str(v)
        return v


class QboItemBasedExpenseLineDetail(BaseModel):
    """QBO ItemBasedExpenseLineDetail for VendorCredit lines."""
    model_config = ConfigDict(populate_by_name=True)
    
    item_ref: Optional[QboReferenceType] = Field(default=None, alias="ItemRef")
    class_ref: Optional[QboReferenceType] = Field(default=None, alias="ClassRef")
    unit_price: Optional[Decimal] = Field(default=None, alias="UnitPrice")
    qty: Optional[Decimal] = Field(default=None, alias="Qty")
    billable_status: Optional[str] = Field(default=None, alias="BillableStatus")
    tax_code_ref: Optional[QboReferenceType] = Field(default=None, alias="TaxCodeRef")
    customer_ref: Optional[QboReferenceType] = Field(default=None, alias="CustomerRef")
    markup_info: Optional[dict] = Field(default=None, alias="MarkupInfo")


class QboAccountBasedExpenseLineDetail(BaseModel):
    """QBO AccountBasedExpenseLineDetail for VendorCredit lines."""
    model_config = ConfigDict(populate_by_name=True)
    
    account_ref: Optional[QboReferenceType] = Field(default=None, alias="AccountRef")
    class_ref: Optional[QboReferenceType] = Field(default=None, alias="ClassRef")
    billable_status: Optional[str] = Field(default=None, alias="BillableStatus")
    tax_code_ref: Optional[QboReferenceType] = Field(default=None, alias="TaxCodeRef")
    customer_ref: Optional[QboReferenceType] = Field(default=None, alias="CustomerRef")
    markup_info: Optional[dict] = Field(default=None, alias="MarkupInfo")


class QboVendorCreditLine(BaseModel):
    """QBO VendorCredit line item."""
    model_config = ConfigDict(populate_by_name=True)
    
    id: Optional[str] = Field(default=None, alias="Id")
    line_num: Optional[int] = Field(default=None, alias="LineNum")
    description: Optional[str] = Field(default=None, alias="Description")
    amount: Optional[Decimal] = Field(default=None, alias="Amount")
    detail_type: Optional[str] = Field(default=None, alias="DetailType")
    item_based_expense_line_detail: Optional[QboItemBasedExpenseLineDetail] = Field(
        default=None, alias="ItemBasedExpenseLineDetail"
    )
    account_based_expense_line_detail: Optional[QboAccountBasedExpenseLineDetail] = Field(
        default=None, alias="AccountBasedExpenseLineDetail"
    )
    
    @field_validator('id', mode='before')
    @classmethod
    def convert_id_to_string(cls, v):
        if v is not None:
            return str(v)
        return v


class _QboBaseModel(BaseModel):
    """Base model with common config."""
    model_config = ConfigDict(populate_by_name=True)


class QboVendorCreditBase(_QboBaseModel):
    """Base model for VendorCredit."""
    vendor_ref: Optional[QboReferenceType] = Field(default=None, alias="VendorRef")
    line: Optional[List[QboVendorCreditLine]] = Field(default=None, alias="Line")
    currency_ref: Optional[QboReferenceType] = Field(default=None, alias="CurrencyRef")
    txn_date: Optional[str] = Field(default=None, alias="TxnDate")
    doc_number: Optional[str] = Field(default=None, alias="DocNumber")
    private_note: Optional[str] = Field(default=None, alias="PrivateNote")
    total_amt: Optional[Decimal] = Field(default=None, alias="TotalAmt")
    ap_account_ref: Optional[QboReferenceType] = Field(default=None, alias="APAccountRef")
    global_tax_calculation: Optional[str] = Field(default=None, alias="GlobalTaxCalculation")
    exchange_rate: Optional[Decimal] = Field(default=None, alias="ExchangeRate")


class QboVendorCreditCreate(QboVendorCreditBase):
    """Model for creating a VendorCredit in QBO."""
    pass


class QboVendorCreditUpdate(QboVendorCreditBase):
    """Model for updating a VendorCredit in QBO."""
    id: str = Field(alias="Id")
    sync_token: str = Field(alias="SyncToken")
    
    @field_validator('id', mode='before')
    @classmethod
    def convert_id_to_string(cls, v):
        if v is not None:
            return str(v)
        return v


class QboVendorCredit(QboVendorCreditBase):
    """Full VendorCredit model from QBO API response."""
    id: str = Field(alias="Id")
    sync_token: str = Field(alias="SyncToken")
    meta_data: Optional[dict] = Field(default=None, alias="MetaData")
    
    @field_validator('id', mode='before')
    @classmethod
    def convert_id_to_string(cls, v):
        if v is not None:
            return str(v)
        return v


class QboVendorCreditResponse(BaseModel):
    """API response wrapper for a single VendorCredit."""
    model_config = ConfigDict(populate_by_name=True)
    
    vendor_credit: QboVendorCredit = Field(alias="VendorCredit")


class QboVendorCreditQueryResponse(BaseModel):
    """API response wrapper for VendorCredit query."""
    model_config = ConfigDict(populate_by_name=True)
    
    query_response: dict = Field(alias="QueryResponse")
