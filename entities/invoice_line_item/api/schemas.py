# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class InvoiceLineItemCreate(BaseModel):
    invoice_public_id: str = Field(
        description="The invoice public ID."
    )
    source_type: str = Field(
        description="Source type: BillLineItem, ExpenseLineItem, BillCreditLineItem, ExpenseRefundLineItem, or Manual."
    )
    bill_line_item_id: Optional[int] = Field(
        default=None,
        description="The source BillLineItem ID (if source_type is BillLineItem)."
    )
    expense_line_item_id: Optional[int] = Field(
        default=None,
        description="The source ExpenseLineItem ID (if source_type is ExpenseLineItem)."
    )
    bill_credit_line_item_id: Optional[int] = Field(
        default=None,
        description="The source BillCreditLineItem ID (if source_type is BillCreditLineItem)."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The SubCostCode ID (required for Manual source type)."
    )
    description: Optional[str] = Field(
        default=None,
        description="Snapshot of the line item description."
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        description="Quantity (used for Manual source type)."
    )
    rate: Optional[Decimal] = Field(
        default=None,
        description="Unit rate (used for Manual source type)."
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="Snapshot of the cost amount."
    )
    markup: Optional[Decimal] = Field(
        default=None,
        description="Snapshot of the markup percentage."
    )
    price: Optional[Decimal] = Field(
        default=None,
        description="Snapshot of the billable price."
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description="Whether the line item is a draft."
    )


class InvoiceLineItemUpdate(BaseModel):
    row_version: str = Field(
        description="The row version (base64 encoded)."
    )
    invoice_public_id: str = Field(
        description="The invoice public ID."
    )
    source_type: Optional[str] = Field(
        default=None,
        description="Source type discriminator."
    )
    bill_line_item_id: Optional[int] = Field(
        default=None,
        description="The source BillLineItem ID."
    )
    expense_line_item_id: Optional[int] = Field(
        default=None,
        description="The source ExpenseLineItem ID."
    )
    bill_credit_line_item_id: Optional[int] = Field(
        default=None,
        description="The source BillCreditLineItem ID."
    )
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description="The SubCostCode ID (required for Manual source type)."
    )
    description: Optional[str] = Field(
        default=None,
        description="Snapshot of the line item description."
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        description="Quantity (used for Manual source type)."
    )
    rate: Optional[Decimal] = Field(
        default=None,
        description="Unit rate (used for Manual source type)."
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description="Snapshot of the cost amount."
    )
    markup: Optional[Decimal] = Field(
        default=None,
        description="Snapshot of the markup percentage."
    )
    price: Optional[Decimal] = Field(
        default=None,
        description="Snapshot of the billable price."
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description="Whether the line item is a draft."
    )
