"""
Module for payment term.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from persistence import pers_database
from persistence.pers_response import PersistenceResponse


@dataclass
class PaymentTerm:
    """Represents a payment term in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    value: Optional[int] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['PaymentTerm']:
        """Creates a PaymentTerm instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            value=getattr(row, 'Value', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_payment_term(payment_term: PaymentTerm) -> PersistenceResponse:
    """
    Creates a new payment term in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreatePaymentTerm(?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    payment_term.created_datetime,
                    payment_term.modified_datetime,
                    payment_term.name,
                    payment_term.value
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Payment term created successfully",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Payment term not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create payment term: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_payment_terms() -> PersistenceResponse:
    """
    Retrieves all payment terms from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadPaymentTerms()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[PaymentTerm.from_db_row(row) for row in rows],
                        message="Payment terms read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No payment terms found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read payment terms: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_payment_term_by_name(name: str) -> PersistenceResponse:
    """
    Retrieves a payment term from the database by name.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadPaymentTermByName(?)}"
                row = cursor.execute(sql, name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=PaymentTerm.from_db_row(row),
                        message="Payment term read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Payment term by name not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read payment term by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_payment_term_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a payment term from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadPaymentTermByGUID(?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=PaymentTerm.from_db_row(row),
                        message="Payment term read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Payment term by GUID not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read payment term by GUID: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )

