# Python Standard Library Imports
from typing import Any, Dict, List, Optional
from decimal import Decimal

# Third-party Imports
from pydantic import BaseModel, Field, field_validator

# Local Imports
from integrations.intuit.qbo.base.schemas import _QboBaseModel
from integrations.intuit.qbo.base.id_validation import require_non_blank_qbo_id


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

    # U-507 fix: ONE shared validator, not a seventh hand-copy of the int->str
    # coercion. `id: str` REQUIRED rejects an ABSENT or NULL Id -- it does NOT
    # reject an EMPTY STRING, and `str_strip_whitespace` quietly turns " " into
    # one. Such a row staged with QBO id "" and ADVANCED the watermark past
    # itself. `require_non_blank_qbo_id` raises instead; see base/id_validation.py
    # for why this lives on the schema (the page must abort before anything
    # stages) and why `sync_token` is deliberately left alone.
    _coerce_id = field_validator("id", mode="before")(require_non_blank_qbo_id)


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
