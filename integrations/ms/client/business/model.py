# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports

# Local Imports


@dataclass
class MsClient:
    app: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]
    tenant_id: Optional[str]
    redirect_uri: Optional[str]

    def to_dict(self) -> dict:
        """
        Convert the MsClient dataclass to a dictionary.
        """
        return asdict(self)
