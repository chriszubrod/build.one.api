# Python Standard Library Imports
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional
import base64

# Third-party Imports

# Local Imports


class IntegrationStatus(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"

@dataclass
class Integration:
    id: Optional[str]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    name: Optional[str]
    status: Optional[IntegrationStatus]
    endpoint: Optional[str]


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
        Convert the integration dataclass to a dictionary.
        """
        d = asdict(self)
        if self.status is not None:
            d["status"] = self.status.value
        return d
