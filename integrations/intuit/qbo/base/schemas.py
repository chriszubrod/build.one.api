# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Local Imports
from integrations.intuit.qbo.base.charge_link import strict_qbo_txn_id


class _QboBaseModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="ignore",
    )


class QboLinkedTxn(BaseModel):
    """LinkedTxn on Bill / Purchase / VendorCredit lines and the Bill header. One definition (U-778a) so the strict
    id rule has one edit site. NOT the invoice LinkedTxn, which carries TxnLineId and is written to QBO.
    """
    model_config = ConfigDict(populate_by_name=True)

    txn_id: Optional[str] = Field(default=None, alias="TxnId")
    txn_type: Optional[str] = Field(default=None, alias="TxnType")

    @field_validator('txn_id', mode='before')
    @classmethod
    def normalize_txn_id(cls, v):
        # U-778a: only a str or non-bool int is an id; anything malformed is "no link", never a stringified value.
        return strict_qbo_txn_id(v)

    @field_validator('txn_type', mode='before')
    @classmethod
    def normalize_txn_type(cls, v):
        return v if isinstance(v, str) else None
