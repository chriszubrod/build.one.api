# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional

# Third-party Imports

# Local Imports


@dataclass
class QboClient:
    app: Optional[str]
    client_id: Optional[str]
    client_secret: Optional[str]

    def to_dict(self) -> dict:
        """
        Serialize for API responses. client_secret is deliberately excluded;
        internal callers needing the secret must read the client_secret attribute.
        """
        return {
            "app": self.app,
            "client_id": self.client_id,
            "client_secret_set": bool(self.client_secret),
        }
