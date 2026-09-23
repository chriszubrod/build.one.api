# Python Standard Library Imports
from typing import Any, Dict, List, Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field, field_validator

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel
from integrations.intuit.qbo.base.id_validation import require_non_blank_qbo_id


class QboReferenceType(_QboBaseModel):
    """
    QBO Reference type for ParentRef, CurrencyRef, etc.
    """
    value: Optional[str] = Field(default=None, alias="value")
    name: Optional[str] = Field(default=None, alias="name")


class QboAccountBase(_QboBaseModel):
    """
    Base Account fields from QBO API.
    """
    name: Optional[str] = Field(default=None, alias="Name")
    acct_num: Optional[str] = Field(default=None, alias="AcctNum")
    description: Optional[str] = Field(default=None, alias="Description")
    active: Optional[bool] = Field(default=None, alias="Active")
    classification: Optional[str] = Field(default=None, alias="Classification")
    account_type: Optional[str] = Field(default=None, alias="AccountType")
    account_sub_type: Optional[str] = Field(default=None, alias="AccountSubType")
    fully_qualified_name: Optional[str] = Field(default=None, alias="FullyQualifiedName")
    sub_account: Optional[bool] = Field(default=None, alias="SubAccount")
    parent_ref: Optional[QboReferenceType] = Field(default=None, alias="ParentRef")
    current_balance: Optional[Decimal] = Field(default=None, alias="CurrentBalance")
    current_balance_with_sub_accounts: Optional[Decimal] = Field(default=None, alias="CurrentBalanceWithSubAccounts")
    currency_ref: Optional[QboReferenceType] = Field(default=None, alias="CurrencyRef")
    domain: Optional[str] = Field(default=None, alias="domain")
    sparse: Optional[bool] = Field(default=None, alias="sparse")


class QboAccountCreate(QboAccountBase):
    """
    Account creation model - Name and AccountType are required.
    """
    pass


class QboAccountUpdate(QboAccountBase):
    """
    Account update model - requires Id and SyncToken.
    """
    id: str = Field(alias="Id")
    sync_token: str = Field(alias="SyncToken")


class QboAccount(QboAccountUpdate):
    """
    Full Account model with Id, SyncToken, and MetaData.
    """
    metadata: Optional[Dict[str, Any]] = Field(default=None, alias="MetaData")

    # U-507 fix: ONE shared validator, not a seventh hand-copy of the int->str
    # coercion. `id: str` REQUIRED rejects an ABSENT or NULL Id -- it does NOT
    # reject an EMPTY STRING, and `str_strip_whitespace` quietly turns " " into
    # one. Such a row staged with QBO id "" and ADVANCED the watermark past
    # itself. `require_non_blank_qbo_id` raises instead; see base/id_validation.py
    # for why this lives on the schema (the page must abort before anything
    # stages) and why `sync_token` is deliberately left alone.
    _coerce_id = field_validator("id", mode="before")(require_non_blank_qbo_id)


class QboAccountResponse(_QboBaseModel):
    """
    Wrapper for QBO Account API response.
    """
    account: QboAccount = Field(alias="Account")


class QboAccountQueryResponse(_QboBaseModel):
    """
    Wrapper for QBO Account query response.
    """
    accounts: List[QboAccount] = Field(default_factory=list, alias="Account")
    start_position: Optional[int] = Field(default=None, alias="startPosition")
    max_results: Optional[int] = Field(default=None, alias="maxResults")
