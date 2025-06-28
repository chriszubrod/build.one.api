"""
Module for database connections.
"""

# python standard library imports
from typing import Generator
from contextlib import contextmanager

# third party imports
import pyodbc


def open_db_cnxn():
    """
    Opens a database connection.
    """
    database = pyodbc.connect(
        'DRIVER={ODBC Driver 17 for SQL Server};' +
        'SERVER=tcp:bchristopher.database.windows.net,1433;' +
        'DATABASE=buildone;' +
        'UID=zubrodcb@bchristopher;' +
        'PWD=KAPsig246!;' +
        'Encrypt=yes;'
    )
    return database


@contextmanager
def get_db_connection() -> Generator[pyodbc.Connection, None, None]:
    """
    Context manager for database connections.
    
    Yields:
        pyodbc.Connection: Database connection

    Raises:
        DatabaseError: If connection cannot be established
    """
    cnxn = open_db_cnxn()
    cnxn.autocommit = False
    try:
        yield cnxn
    finally:
        cnxn.close()


@contextmanager
def transaction() -> Generator[pyodbc.Connection, None, None]:
    """
    Context manager for database transactions.
    Handles commit and rollback automatically.
    
    Usage:
        with transaction() as trans:
            # do database operations
            trans.commit()  # commit if all is good
            # rollback happens automatically if there's an exception
    """
    with get_db_connection() as cnxn:
        try:
            yield cnxn
            # Transaction will be committed by the caller
        except Exception:
            cnxn.rollback()
            raise
        finally:
            cnxn.close()
