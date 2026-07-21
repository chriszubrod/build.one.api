# Python Standard Library Imports
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional
import base64

# Third-party Imports

# Local Imports


def _mask_tin(tin):
    if not tin:
        return None
    digits = "".join(c for c in str(tin) if c.isdigit())
    if len(digits) < 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


class TaxpayerClassification(str, Enum):
    """
    Enumeration of taxpayer classifications.
    """
    INDIVIDUAL_SOLE_PROPRIETOR = "INDIVIDUAL_SOLE_PROPRIETOR"
    C_CORPORATION = "C_CORPORATION"
    S_CORPORATION = "S_CORPORATION"
    PARTNERSHIP = "PARTNERSHIP"
    TRUST_ESTATE = "TRUST_ESTATE"
    LLC = "LLC"


@dataclass
class Taxpayer:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    entity_name: Optional[str]
    business_name: Optional[str]
    classification: Optional[TaxpayerClassification]
    taxpayer_id_number: Optional[str]
    is_signed: Optional[int] = None
    signature_date: Optional[str] = None
    taxpayer_id_number_hash: Optional[str] = None
    is_deleted: Optional[bool] = None

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
        Convert the taxpayer dataclass to a dictionary.
        """
        result = asdict(self)
        # Convert enum to string value for JSON serialization
        if result.get("classification") is not None:
            result["classification"] = result["classification"].value if isinstance(result["classification"], TaxpayerClassification) else result["classification"]
        digits = "".join(c for c in str(self.taxpayer_id_number) if c.isdigit()) if self.taxpayer_id_number else ""
        result["taxpayer_id_number"] = _mask_tin(self.taxpayer_id_number)
        result["taxpayer_id_last4"] = digits[-4:] if len(digits) >= 4 else None
        result.pop("taxpayer_id_number_hash", None)
        return result
