# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class Employee:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    firstname: Optional[str]
    lastname: Optional[str]
    email: Optional[str] = None
    hourly_rate: Optional[Decimal] = None
    markup: Optional[Decimal] = None
    is_active: Optional[bool] = True
    is_deleted: Optional[bool] = False
    notes: Optional[str] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    def to_dict(self) -> dict:
        """Convert the employee dataclass to a dictionary.

        Decimals serialize as strings so JSON transport doesn't silently lose
        precision on the React side (per memory: never round-trip currency
        through float)."""
        d = asdict(self)
        if self.hourly_rate is not None:
            d["hourly_rate"] = str(self.hourly_rate)
        if self.markup is not None:
            d["markup"] = str(self.markup)
        return d
