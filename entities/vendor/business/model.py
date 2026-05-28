# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class Vendor:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    name: Optional[str]
    abbreviation: Optional[str]
    taxpayer_id: Optional[int]
    vendor_type_id: Optional[int]
    is_draft: Optional[bool]
    is_deleted: Optional[bool] = False
    is_contract_labor: Optional[bool] = False
    # Free-text per-vendor notes — surfaced in the React Vendor edit
    # page and read by bill_specialist via FindVendorForInvoice when
    # creating bills from invoice emails.
    notes: Optional[str] = None
    # Phase 2 — default contract-labor rate + markup. Per-project overrides
    # live in dbo.VendorProjectRate; the aggregation sproc COALESCEs the
    # override over these defaults.
    hourly_rate: Optional[Decimal] = None
    markup: Optional[Decimal] = None

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
        """
        Convert the vendor dataclass to a dictionary.

        Decimal fields are stringified so JSON transport doesn't silently
        truncate precision on the React side.
        """
        d = asdict(self)
        if self.hourly_rate is not None:
            d["hourly_rate"] = str(self.hourly_rate)
        if self.markup is not None:
            d["markup"] = str(self.markup)
        return d
