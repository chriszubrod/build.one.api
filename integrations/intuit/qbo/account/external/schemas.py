# Python Standard Library Imports
from typing import Any, Dict, List, Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field, field_validator

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel


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
