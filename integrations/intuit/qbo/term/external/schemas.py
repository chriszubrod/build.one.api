# Python Standard Library Imports
from typing import Any, Dict, List, Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field, field_validator

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel


class QboTermBase(_QboBaseModel):
    """
    Base Term fields from QBO API.
    """
    name: Optional[str] = Field(default=None, alias="Name")
    discount_percent: Optional[Decimal] = Field(default=None, alias="DiscountPercent")
    discount_days: Optional[int] = Field(default=None, alias="DiscountDays")
    active: Optional[bool] = Field(default=None, alias="Active")
    type: Optional[str] = Field(default=None, alias="Type")
    day_of_month_due: Optional[int] = Field(default=None, alias="DayOfMonthDue")
    discount_day_of_month: Optional[int] = Field(default=None, alias="DiscountDayOfMonth")
    due_next_month_days: Optional[int] = Field(default=None, alias="DueNextMonthDays")
    due_days: Optional[int] = Field(default=None, alias="DueDays")
    domain: Optional[str] = Field(default=None, alias="domain")
    sparse: Optional[bool] = Field(default=None, alias="sparse")


class QboTermCreate(QboTermBase):
    pass


class QboTermUpdate(QboTermBase):
    id: str = Field(alias="Id")
    sync_token: str = Field(alias="SyncToken")


class QboTerm(QboTermUpdate):
    """
    Full Term model with Id, SyncToken, and MetaData.
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


class QboTermResponse(_QboBaseModel):
    """
    Wrapper for QBO Term API response.
    """
    term: QboTerm = Field(alias="Term")


class QboTermQueryResponse(_QboBaseModel):
    """
    Wrapper for QBO Term query response.
    """
    terms: List[QboTerm] = Field(default_factory=list, alias="Term")
    start_position: Optional[int] = Field(default=None, alias="startPosition")
    max_results: Optional[int] = Field(default=None, alias="maxResults")
