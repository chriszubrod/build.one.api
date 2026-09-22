# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class AssetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    asset_type: str = Field(description="vehicle | machinery | equipment")
    make: Optional[str] = Field(default=None, max_length=100)
    model: Optional[str] = Field(default=None, max_length=100)
    model_year: Optional[int] = Field(default=None)
    serial_number: Optional[str] = Field(default=None, max_length=100)
    status: Optional[str] = Field(default="active")
    acquisition_date: Optional[str] = None
    disposal_date: Optional[str] = None
    qbo_fixed_asset_account_id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="qbo.Account.QboId for the fixed-asset GL account (not staging Id).",
    )
    qbo_accum_dep_account_id: Optional[str] = Field(
        default=None,
        max_length=50,
        description="qbo.Account.QboId for accumulated depreciation (not staging Id).",
    )


class AssetUpdate(BaseModel):
    row_version: str = Field(description="Base64-encoded ROWVERSION")
    name: Optional[str] = Field(default=None, max_length=200)
    asset_type: Optional[str] = None
    make: Optional[str] = Field(default=None, max_length=100)
    model: Optional[str] = Field(default=None, max_length=100)
    model_year: Optional[int] = None
    serial_number: Optional[str] = Field(default=None, max_length=100)
    status: Optional[str] = None
    acquisition_date: Optional[str] = None
    disposal_date: Optional[str] = None
    qbo_fixed_asset_account_id: Optional[str] = Field(default=None, max_length=50)
    qbo_accum_dep_account_id: Optional[str] = Field(default=None, max_length=50)


class AssetFinancingNoteCreate(BaseModel):
    asset_public_id: str = Field(min_length=1)
    qbo_liability_account_id: str = Field(
        min_length=1,
        max_length=50,
        description="qbo.Account.QboId for the liability account (not staging Id).",
    )


class AssetAccountExclusionCreate(BaseModel):
    qbo_account_id: str = Field(
        min_length=1,
        max_length=50,
        description="qbo.Account.QboId for the excluded fixed-asset account (not staging Id).",
    )
    reason: str = Field(description="leasehold-improvement | parent-rollup-account")
