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


class QboLinkedTxn(BaseModel):
    """
    Linked transaction reference. On a ReimburseCharge this is the REVERSE
    pointer back to the source Bill/Purchase (dropped once HasBeenInvoiced=true).
    """
    model_config = ConfigDict(populate_by_name=True)

    txn_id: Optional[str] = Field(default=None, alias="TxnId")
    txn_type: Optional[str] = Field(default=None, alias="TxnType")
    txn_line_id: Optional[str] = Field(default=None, alias="TxnLineId")


class QboReimburseCharge(_QboBaseModel):
    """
    ReimburseCharge model from the QBO API.

    Documentation/typing companion to the pure `parse_reimburse_charge` — the
    sync service parses raw dicts directly (QBO RC payloads are loosely shaped),
    so this schema is not on the hot path.
    """
    id: Optional[str] = Field(default=None, alias="Id")
    customer_ref: Optional[QboReferenceType] = Field(default=None, alias="CustomerRef")
    txn_date: Optional[str] = Field(default=None, alias="TxnDate")
    amount: Optional[Decimal] = Field(default=None, alias="Amount")
    has_been_invoiced: Optional[bool] = Field(default=None, alias="HasBeenInvoiced")
    linked_txn: Optional[List[QboLinkedTxn]] = Field(default_factory=list, alias="LinkedTxn")
    line: Optional[List[Dict[str, Any]]] = Field(default_factory=list, alias="Line")

    @field_validator("id", mode="before")
    @classmethod
    def convert_id_to_string(cls, v):
        if v is None:
            return None
        return str(v)
