# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class VendorInsurancePolicy:
    id: Optional[str]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    certificate_of_insurance_id: Optional[str]
    coverage_type: Optional[str]
    carrier: Optional[str]
    policy_number: Optional[str]
    each_occurrence: Optional[str]
    aggregate: Optional[str]
    effective_date: Optional[str]
    expiry_date: Optional[str]
    created_by_user_id: Optional[str]

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
        d = asdict(self)
        if self.each_occurrence is not None:
            d["each_occurrence"] = str(self.each_occurrence)
        if self.aggregate is not None:
            d["aggregate"] = str(self.aggregate)
        return d
