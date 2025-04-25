"""
Module for entry type.
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
class EntryType:
    """Represents an entry type in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    description: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['EntryType']:
        """Creates an EntryType instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            description=getattr(row, 'Description', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_entry_type(entry_type: EntryType) -> PersistenceResponse:
    """
    Creates a new entry type in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateEntryType(?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    entry_type.created_datetime,
                    entry_type.modified_datetime,
                    entry_type.name,
                    entry_type.description
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Entry type created",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Entry type not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create entry type: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_entry_types() -> PersistenceResponse:
    """
    Retrieves all entry types from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadEntryTypes()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[EntryType.from_db_row(row) for row in rows],
                        message="Entry types retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No entry types found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read entry types: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_entry_type_by_id(entry_type_id: int) -> PersistenceResponse:
    """Retrieves an EntryType instance by its ID."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadEntryTypeById(?)}"
                row = cursor.execute(sql, entry_type_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=EntryType.from_db_row(row),
                        message="Entry type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Entry type by id not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read entry type by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_entry_type_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves an entry type from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadEntryTypeByGUID(?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=EntryType.from_db_row(row),
                        message="Entry type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Entry type by guid not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read entry type by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_entry_type_by_name(name: str) -> PersistenceResponse:
    """
    Retrieves an entry type from the database by name.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadEntryTypeByName(?)}"
                row = cursor.execute(sql, name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=EntryType.from_db_row(row),
                        message="Entry type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Entry type by name not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read entry type by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
