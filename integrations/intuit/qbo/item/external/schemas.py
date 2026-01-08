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


class QboItemBase(_QboBaseModel):
    """
    Base Item fields from QBO API.
    """
    name: Optional[str] = Field(default=None, alias="Name")
    description: Optional[str] = Field(default=None, alias="Description")
    active: Optional[bool] = Field(default=None, alias="Active")
    type: Optional[str] = Field(default=None, alias="Type")
    parent_ref: Optional[QboRef] = Field(default=None, alias="ParentRef")
    level: Optional[int] = Field(default=None, alias="Level")
    fully_qualified_name: Optional[str] = Field(default=None, alias="FullyQualifiedName")
    sku: Optional[str] = Field(default=None, alias="Sku")
    unit_price: Optional[Decimal] = Field(default=None, alias="UnitPrice")
    purchase_cost: Optional[Decimal] = Field(default=None, alias="PurchaseCost")
    taxable: Optional[bool] = Field(default=None, alias="Taxable")
    income_account_ref: Optional[QboRef] = Field(default=None, alias="IncomeAccountRef")
    expense_account_ref: Optional[QboRef] = Field(default=None, alias="ExpenseAccountRef")
    asset_account_ref: Optional[QboRef] = Field(default=None, alias="AssetAccountRef")
    track_qty_on_hand: Optional[bool] = Field(default=None, alias="TrackQtyOnHand")
    qty_on_hand: Optional[Decimal] = Field(default=None, alias="QtyOnHand")
    inv_start_date: Optional[str] = Field(default=None, alias="InvStartDate")
    domain: Optional[str] = Field(default=None, alias="domain")
    sparse: Optional[bool] = Field(default=None, alias="sparse")


class QboItem(QboItemBase):
    """
    Full Item model with Id, SyncToken, and MetaData.
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


class QboItemResponse(_QboBaseModel):
    """
    Wrapper for QBO Item API response.
    """
    item: QboItem = Field(alias="Item")


class QboItemQueryResponse(_QboBaseModel):
    """
    Wrapper for QBO Item query response.
    """
    query_response: Dict[str, Any] = Field(alias="QueryResponse")

    def get_items(self) -> List[QboItem]:
        """
        Extract Item list from query response.
        """
        items_data = self.query_response.get("Item", [])
        return [QboItem(**item) for item in items_data]

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

