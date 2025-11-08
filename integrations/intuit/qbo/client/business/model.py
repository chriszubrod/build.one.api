# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports

# Local Imports


@dataclass
class QboClient:
    client_id: Optional[str]
    client_secret: Optional[str]

    def to_dict(self) -> dict:
        """
        Convert the QboClient dataclass to a dictionary.
        """
        return asdict(self)
