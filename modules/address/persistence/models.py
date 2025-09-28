"""
Address persistence models.

Contains data classes that represent address entities in the database.
These models define the structure for address data in the persistence layer.
"""

# Standard Library Imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Third-party Imports


# Local Imports



@dataclass
class Address:
    """
    Represents a physical address in the system.

    This dataclass maps directly to the address database table and contains
    all standard address components. All fields are optional to support
    partial address data during creation and updates.

    Attributes:
        id: Primary key identifier
        guid: Globally unique identifier
        created_datetime: Timestamp when record was created
        modified_datetime: Timestamp when record was last modified
        street_one: Primary street address line
        street_two: Secondary street address line (apartment, suite, etc.)
        city: City name
        state: State or province code
        zip_code: Postal/ZIP code
        transaction_id: Associated transaction identifier
    """
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    street_one: Optional[str] = None
    street_two: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    transaction_id: Optional[int] = None


    @classmethod
    def from_db_row(cls, row) -> Optional['Address']:
        """
        Creates an Address instance from a database row.

        Args:
            row: Database row object with address data
            
        Returns:
            Address instance populated with data from the row, or None if row is None
            
        Note:
            Uses getattr() with default None to handle missing columns gracefully
        """
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            street_one=getattr(row, 'StreetOne', None),
            street_two=getattr(row, 'StreetTwo', None),
            city=getattr(row, 'City', None),
            state=getattr(row, 'State', None),
            zip_code=getattr(row, 'ZipCode', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )

