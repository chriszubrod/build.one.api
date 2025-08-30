"""
Module for transaction.
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
class Transaction:
    """Represents a transaction in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['Transaction']:
        """Creates a Transaction object from a database row."""
        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime')
        )


def read_transaction_by_id(transaction_id: int) -> PersistenceResponse:
    """
    Retrieves a transaction from the database by ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadTransactionById(?)}"
                row = cursor.execute(sql, transaction_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Transaction.from_db_row(row),
                        message="Transaction found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Transaction not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read transaction by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
