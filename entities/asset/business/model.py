# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Any, Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class Asset:
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    name: Optional[str] = None
    asset_type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[int] = None
    serial_number: Optional[str] = None
    status: Optional[str] = None
    acquisition_date: Optional[str] = None
    disposal_date: Optional[str] = None
    qbo_fixed_asset_account_id: Optional[str] = None
    qbo_accum_dep_account_id: Optional[str] = None
    company_id: Optional[int] = None
    created_by_user_id: Optional[int] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AssetWithQbo(Asset):
    fixed_asset_account_name: Optional[str] = None
    fixed_asset_account_balance: Optional[Any] = None
    accum_dep_account_name: Optional[str] = None
    accum_dep_account_balance: Optional[Any] = None


@dataclass
class AssetFinancingNote:
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    asset_id: Optional[int] = None
    qbo_liability_account_id: Optional[str] = None
    created_by_user_id: Optional[int] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AssetAccountExclusion:
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    qbo_account_id: Optional[str] = None
    reason: Optional[str] = None
    company_id: Optional[int] = None
    created_by_user_id: Optional[int] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)
