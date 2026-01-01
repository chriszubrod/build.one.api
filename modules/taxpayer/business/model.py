# Python Standard Library Imports
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional
import base64

# Third-party Imports

# Local Imports


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
        return result
