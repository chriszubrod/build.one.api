# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboAuth:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    code: Optional[str]
    realm_id: Optional[str]
    state: Optional[str]
    token_type: Optional[str]
    id_token: Optional[str]
    access_token: Optional[str]
    expires_in: Optional[int]
    refresh_token: Optional[str]
    x_refresh_token_expires_in: Optional[int]

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
        Convert the QboAuth dataclass to a dictionary.
        """
        return asdict(self)
