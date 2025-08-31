"""
Persistence for Type of Insurance.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class TypeOfInsurance:
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'TypeOfInsurance':
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def read_type_of_insurances() -> PersistenceResponse:
    """Reads all TypeOfInsurance records."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadTypeOfInsurances}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[TypeOfInsurance.from_db_row(r) for r in rows],
                        message="Types of insurance found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No types of insurance found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read types of insurance: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )

