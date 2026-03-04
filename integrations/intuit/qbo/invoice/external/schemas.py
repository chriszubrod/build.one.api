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


class QboSalesItemLineDetail(BaseModel):
    """Sales item line detail for Invoice line items."""
    model_config = ConfigDict(populate_by_name=True)
    
    item_ref: Optional[QboReferenceType] = Field(default=None, alias="ItemRef")
    class_ref: Optional[QboReferenceType] = Field(default=None, alias="ClassRef")
    unit_price: Optional[Decimal] = Field(default=None, alias="UnitPrice")
    qty: Optional[Decimal] = Field(default=None, alias="Qty")
    tax_code_ref: Optional[QboReferenceType] = Field(default=None, alias="TaxCodeRef")
    service_date: Optional[str] = Field(default=None, alias="ServiceDate")
    discount_rate: Optional[Decimal] = Field(default=None, alias="DiscountRate")
    discount_amt: Optional[Decimal] = Field(default=None, alias="DiscountAmt")
    item_account_ref: Optional[QboReferenceType] = Field(default=None, alias="ItemAccountRef")


class QboDiscountLineDetail(BaseModel):
    """Discount line detail for Invoice line items."""
    model_config = ConfigDict(populate_by_name=True)
    
    percent_based: Optional[bool] = Field(default=None, alias="PercentBased")
    discount_percent: Optional[Decimal] = Field(default=None, alias="DiscountPercent")
    discount_account_ref: Optional[QboReferenceType] = Field(default=None, alias="DiscountAccountRef")


class QboSubTotalLineDetail(BaseModel):
    """SubTotal line detail for Invoice line items."""
    model_config = ConfigDict(populate_by_name=True)


class QboDescriptionOnlyLineDetail(BaseModel):
    """DescriptionOnly line detail for Invoice line items (also used for inline subtotals)."""
    model_config = ConfigDict(populate_by_name=True)
    
    service_date: Optional[str] = Field(default=None, alias="ServiceDate")
    tax_code_ref: Optional[QboReferenceType] = Field(default=None, alias="TaxCodeRef")


class QboGroupLineDetail(BaseModel):
    """Group line detail for Invoice line items."""
    model_config = ConfigDict(populate_by_name=True)
    
    group_item_ref: Optional[QboReferenceType] = Field(default=None, alias="GroupItemRef")
    quantity: Optional[Decimal] = Field(default=None, alias="Quantity")
    line: Optional[List[Any]] = Field(default_factory=list, alias="Line")


class QboInvoiceLine(BaseModel):
    """
    Invoice Line item from QBO API.
    Can be SalesItemLine, GroupLine, DescriptionOnlyLine, DiscountLine, or SubTotalLine.
    """
    model_config = ConfigDict(populate_by_name=True)
    
    id: Optional[str] = Field(default=None, alias="Id")
    line_num: Optional[int] = Field(default=None, alias="LineNum")
    description: Optional[str] = Field(default=None, alias="Description")
    amount: Optional[Decimal] = Field(default=None, alias="Amount")
    detail_type: Optional[str] = Field(default=None, alias="DetailType")
    sales_item_line_detail: Optional[QboSalesItemLineDetail] = Field(
        default=None, alias="SalesItemLineDetail"
    )
    discount_line_detail: Optional[QboDiscountLineDetail] = Field(
        default=None, alias="DiscountLineDetail"
    )
    sub_total_line_detail: Optional[QboSubTotalLineDetail] = Field(
        default=None, alias="SubTotalLineDetail"
    )
    description_line_detail: Optional[QboDescriptionOnlyLineDetail] = Field(
        default=None, alias="DescriptionLineDetail"
    )
    group_line_detail: Optional[QboGroupLineDetail] = Field(
        default=None, alias="GroupLineDetail"
    )

    @field_validator('id', mode='before')
    @classmethod
    def convert_id_to_string(cls, v):
        if v is None:
            return None
        return str(v)


class QboLinkedTxn(BaseModel):
    """Linked transaction reference."""
    model_config = ConfigDict(populate_by_name=True)
    
    txn_id: Optional[str] = Field(default=None, alias="TxnId")
    txn_type: Optional[str] = Field(default=None, alias="TxnType")


class QboEmailAddress(BaseModel):
    """Email address type."""
    model_config = ConfigDict(populate_by_name=True)
    
    address: Optional[str] = Field(default=None, alias="Address")


class QboPhysicalAddress(BaseModel):
    """Physical address type."""
    model_config = ConfigDict(populate_by_name=True)
    
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


class QboMemoRef(BaseModel):
    """Memo reference type."""
    model_config = ConfigDict(populate_by_name=True)
    
    value: Optional[str] = Field(default=None, alias="value")


class QboInvoiceBase(_QboBaseModel):
    """
    Base Invoice fields from QBO API.
    """
    customer_ref: Optional[QboReferenceType] = Field(default=None, alias="CustomerRef")
    line: Optional[List[QboInvoiceLine]] = Field(default_factory=list, alias="Line")
    currency_ref: Optional[QboReferenceType] = Field(default=None, alias="CurrencyRef")
    txn_date: Optional[str] = Field(default=None, alias="TxnDate")
    due_date: Optional[str] = Field(default=None, alias="DueDate")
    ship_date: Optional[str] = Field(default=None, alias="ShipDate")
    doc_number: Optional[str] = Field(default=None, alias="DocNumber")
    private_note: Optional[str] = Field(default=None, alias="PrivateNote")
    customer_memo: Optional[QboMemoRef] = Field(default=None, alias="CustomerMemo")
    bill_email: Optional[QboEmailAddress] = Field(default=None, alias="BillEmail")
    bill_email_cc: Optional[QboEmailAddress] = Field(default=None, alias="BillEmailCc")
    bill_email_bcc: Optional[QboEmailAddress] = Field(default=None, alias="BillEmailBcc")
    sales_term_ref: Optional[QboReferenceType] = Field(default=None, alias="SalesTermRef")
    linked_txn: Optional[List[QboLinkedTxn]] = Field(default_factory=list, alias="LinkedTxn")
    total_amt: Optional[Decimal] = Field(default=None, alias="TotalAmt")
    balance: Optional[Decimal] = Field(default=None, alias="Balance")
    deposit: Optional[Decimal] = Field(default=None, alias="Deposit")
    exchange_rate: Optional[Decimal] = Field(default=None, alias="ExchangeRate")
    department_ref: Optional[QboReferenceType] = Field(default=None, alias="DepartmentRef")
    class_ref: Optional[QboReferenceType] = Field(default=None, alias="ClassRef")
    ship_method_ref: Optional[QboReferenceType] = Field(default=None, alias="ShipMethodRef")
    ship_addr: Optional[QboPhysicalAddress] = Field(default=None, alias="ShipAddr")
    bill_addr: Optional[QboPhysicalAddress] = Field(default=None, alias="BillAddr")
    tracking_num: Optional[str] = Field(default=None, alias="TrackingNum")
    print_status: Optional[str] = Field(default=None, alias="PrintStatus")
    email_status: Optional[str] = Field(default=None, alias="EmailStatus")
    allow_online_ach_payment: Optional[bool] = Field(default=None, alias="AllowOnlineACHPayment")
    allow_online_credit_card_payment: Optional[bool] = Field(default=None, alias="AllowOnlineCreditCardPayment")
    apply_tax_after_discount: Optional[bool] = Field(default=None, alias="ApplyTaxAfterDiscount")
    global_tax_calculation: Optional[str] = Field(default=None, alias="GlobalTaxCalculation")
    txn_tax_detail: Optional[Dict[str, Any]] = Field(default=None, alias="TxnTaxDetail")
    deposit_to_account_ref: Optional[QboReferenceType] = Field(default=None, alias="DepositToAccountRef")
    txn_source: Optional[str] = Field(default=None, alias="TxnSource")
    domain: Optional[str] = Field(default=None, alias="domain")
    sparse: Optional[bool] = Field(default=None, alias="sparse")


class QboInvoiceCreate(QboInvoiceBase):
    """Schema for creating an Invoice in QBO."""
    pass


class QboInvoiceUpdate(QboInvoiceBase):
    """Schema for updating an Invoice in QBO."""
    id: str = Field(alias="Id")
    sync_token: str = Field(alias="SyncToken")


class QboInvoice(QboInvoiceUpdate):
    """
    Full Invoice model with Id, SyncToken, and MetaData.
    """
    metadata: Optional[Dict[str, Any]] = Field(default=None, alias="MetaData")
    home_total_amt: Optional[Decimal] = Field(default=None, alias="HomeTotalAmt")
    home_balance: Optional[Decimal] = Field(default=None, alias="HomeBalance")
    invoice_link: Optional[str] = Field(default=None, alias="InvoiceLink")
    free_form_address: Optional[bool] = Field(default=None, alias="FreeFormAddress")

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


class QboInvoiceResponse(_QboBaseModel):
    """
    Wrapper for QBO Invoice API response.
    """
    invoice: QboInvoice = Field(alias="Invoice")


class QboInvoiceQueryResponse(_QboBaseModel):
    """
    Wrapper for QBO Invoice query response.
    """
    invoices: List[QboInvoice] = Field(default_factory=list, alias="Invoice")
    start_position: Optional[int] = Field(default=None, alias="startPosition")
    max_results: Optional[int] = Field(default=None, alias="maxResults")
