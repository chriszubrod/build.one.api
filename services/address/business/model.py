# Python Standard Library Imports
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional
import base64

# Third-party Imports

# Local Imports


class Country(Enum):
    """
    Country enum with only United States available.
    """
    UNITED_STATES = {"name": "United States", "abbreviation": "USA"}

    @property
    def country_name(self) -> str:
        """Get the country name."""
        return self.value["name"]

    @property
    def abbreviation(self) -> str:
        """Get the country abbreviation."""
        return self.value["abbreviation"]

    def to_dict(self) -> dict:
        """Convert the country enum to a dictionary."""
        return {
            "name": self.country_name,
            "abbreviation": self.abbreviation
        }


@dataclass
class Address:
    id: Optional[str]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    street_one: Optional[str]
    street_two: Optional[str]
    city: Optional[str]
    state: Optional[str]
    zip: Optional[str]
    country: Optional[Country]

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
        Convert the address dataclass to a dictionary.
        """
        d = asdict(self)
        if self.country is not None:
            if isinstance(self.country, Country):
                d["country"] = self.country.to_dict()
            else:
                d["country"] = self.country
        return d
