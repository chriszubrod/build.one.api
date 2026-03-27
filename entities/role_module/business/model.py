# Python Standard Library Imports
import base64
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports

# Local Imports


@dataclass
class RoleModule:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    role_id: Optional[int]
    module_id: Optional[int]
    can_create: Optional[bool] = False
    can_read: Optional[bool] = False
    can_update: Optional[bool] = False
    can_delete: Optional[bool] = False
    can_submit: Optional[bool] = False
    can_approve: Optional[bool] = False
    can_complete: Optional[bool] = False

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
        Convert the role module dataclass to a dictionary.
        """
        return asdict(self)
