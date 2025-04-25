"""
Module for sub cost code.
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
class SubCostCode:
    """Represents a sub cost code in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    number: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    cost_code_id: Optional[int] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['SubCostCode']:
        """
        Creates a SubCostCode instance from a database row.
        """
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            number=getattr(row, 'Number', None),
            name=getattr(row, 'Name', None),
            description=getattr(row, 'Description', None),
            cost_code_id=getattr(row, 'CostCodeId', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_sub_cost_code(sub_cost_code: SubCostCode) -> PersistenceResponse:
    """Creates a new sub cost code in the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateSubCostCode(?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    sub_cost_code.created_datetime,
                    sub_cost_code.modified_datetime,
                    sub_cost_code.number,
                    sub_cost_code.name,
                    sub_cost_code.description,
                    sub_cost_code.cost_code_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Sub cost code created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Sub cost code not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create sub cost code: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sub_cost_codes() -> PersistenceResponse:
    """Retrieves all sub cost codes from the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadSubCostCodes}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[SubCostCode.from_db_row(row) for row in rows],
                        message="Sub cost codes read",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Sub cost codes not read",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read sub cost codes: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sub_cost_code_by_name(name: str) -> PersistenceResponse:
    """Retrieves a sub cost code from the database by name."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadSubCostCodeByName(?)}"
                row = cursor.execute(sql, name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=SubCostCode.from_db_row(row),
                        message="Sub cost code read",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Sub cost code not read",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read sub cost code: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_sub_cost_code_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a sub cost code from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadSubCostCodeByGUID(?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=SubCostCode.from_db_row(row),
                        message="Sub cost code read",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Sub cost code not read",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read sub cost code: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
