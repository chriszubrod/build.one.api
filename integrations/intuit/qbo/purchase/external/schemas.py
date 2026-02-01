# Python Standard Library Imports
from typing import Any, Dict, List, Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel


class QboReferenceType(BaseModel):
    """Reference type used in QBO API for linking entities."""
    model_config = ConfigDict(populate_by_name=True)
    
    value: Optional[str] = Field(default=None, alias="value")
    name: Optional[str] = Field(default=None, alias="name")


class QboItemBasedExpenseLineDetail(BaseModel):
    """Item-based expense line detail for Purchase line items."""
    model_config = ConfigDict(populate_by_name=True)
    
    item_ref: Optional[QboReferenceType] = Field(default=None, alias="ItemRef")
    customer_ref: Optional[QboReferenceType] = Field(default=None, alias="CustomerRef")
    class_ref: Optional[QboReferenceType] = Field(default=None, alias="ClassRef")
    price_level_ref: Optional[QboReferenceType] = Field(default=None, alias="PriceLevelRef")
    billable_status: Optional[str] = Field(default=None, alias="BillableStatus")
    tax_code_ref: Optional[QboReferenceType] = Field(default=None, alias="TaxCodeRef")
    markup_info: Optional[Dict[str, Any]] = Field(default=None, alias="MarkupInfo")
    qty: Optional[Decimal] = Field(default=None, alias="Qty")
    unit_price: Optional[Decimal] = Field(default=None, alias="UnitPrice")


class QboAccountBasedExpenseLineDetail(BaseModel):
    """Account-based expense line detail for Purchase line items."""
    model_config = ConfigDict(populate_by_name=True)
    
    account_ref: Optional[QboReferenceType] = Field(default=None, alias="AccountRef")
    customer_ref: Optional[QboReferenceType] = Field(default=None, alias="CustomerRef")
    class_ref: Optional[QboReferenceType] = Field(default=None, alias="ClassRef")
    billable_status: Optional[str] = Field(default=None, alias="BillableStatus")
    tax_code_ref: Optional[QboReferenceType] = Field(default=None, alias="TaxCodeRef")
    markup_info: Optional[Dict[str, Any]] = Field(default=None, alias="MarkupInfo")


class QboPurchaseLine(BaseModel):
    """
    Purchase Line item from QBO API.
    Can be ItemBasedExpenseLine or AccountBasedExpenseLine.
    """
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
        if v is None:
            return None
        return str(v)


class QboPurchaseBase(_QboBaseModel):
    """
    Base Purchase fields from QBO API.
    """
    # Purchase-specific fields
    payment_type: Optional[str] = Field(default=None, alias="PaymentType")  # Cash, Check, CreditCard
    account_ref: Optional[QboReferenceType] = Field(default=None, alias="AccountRef")  # Bank/CC account
    entity_ref: Optional[QboReferenceType] = Field(default=None, alias="EntityRef")  # Vendor/payee
    credit: Optional[bool] = Field(default=None, alias="Credit")  # True if credit memo
    
    # Shared fields
    line: Optional[List[QboPurchaseLine]] = Field(default_factory=list, alias="Line")
    currency_ref: Optional[QboReferenceType] = Field(default=None, alias="CurrencyRef")
    txn_date: Optional[str] = Field(default=None, alias="TxnDate")
    doc_number: Optional[str] = Field(default=None, alias="DocNumber")
    private_note: Optional[str] = Field(default=None, alias="PrivateNote")
    total_amt: Optional[Decimal] = Field(default=None, alias="TotalAmt")
    exchange_rate: Optional[Decimal] = Field(default=None, alias="ExchangeRate")
    department_ref: Optional[QboReferenceType] = Field(default=None, alias="DepartmentRef")
    global_tax_calculation: Optional[str] = Field(default=None, alias="GlobalTaxCalculation")
    txn_tax_detail: Optional[Dict[str, Any]] = Field(default=None, alias="TxnTaxDetail")
    domain: Optional[str] = Field(default=None, alias="domain")
    sparse: Optional[bool] = Field(default=None, alias="sparse")


class QboPurchaseCreate(QboPurchaseBase):
    """Schema for creating a Purchase in QBO."""
    pass


class QboPurchaseUpdate(QboPurchaseBase):
    """Schema for updating a Purchase in QBO."""
    id: str = Field(alias="Id")
    sync_token: str = Field(alias="SyncToken")


class QboPurchase(QboPurchaseUpdate):
    """
    Full Purchase model with Id, SyncToken, and MetaData.
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


class QboPurchaseResponse(_QboBaseModel):
    """
    Wrapper for QBO Purchase API response.
    """
    purchase: QboPurchase = Field(alias="Purchase")


class QboPurchaseQueryResponse(_QboBaseModel):
    """
    Wrapper for QBO Purchase query response.
    """
    purchases: List[QboPurchase] = Field(default_factory=list, alias="Purchase")
    start_position: Optional[int] = Field(default=None, alias="startPosition")
    max_results: Optional[int] = Field(default=None, alias="maxResults")
