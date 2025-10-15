# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
import base64


@dataclass
class Auth:
    """
    Domain representation of an authentication account persisted in SQL.
    """
    id: str
    public_id: str
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    username: str
    password_hash: str

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

    def to_dict(self):
        """Convert to dictionary with JSON-serializable values."""
        data = asdict(self)
        return data


@dataclass
class AuthToken:
    """
    Access token issued on login or signup.
    """
    access_token: str
    token_type: str
    expires_in: int

    def to_dict(self):
        """Convert to dictionary with JSON-serializable values."""
        data = asdict(self)
        return data
