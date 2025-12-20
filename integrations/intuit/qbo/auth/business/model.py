# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports

# Local Imports


@dataclass
class QboAuth:
    code: Optional[str]
    realm_id: Optional[str]
    state: Optional[str]
    token_type: Optional[str]
    id_token: Optional[str]
    access_token: Optional[str]
    expires_in: Optional[int]
    refresh_token: Optional[str]
    x_refresh_token_expires_in: Optional[int]

    def to_dict(self) -> dict:
        """
        Convert the QboAuth dataclass to a dictionary.
        """
        return asdict(self)
