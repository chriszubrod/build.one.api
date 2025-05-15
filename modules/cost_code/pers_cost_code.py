"""
Module for cost code.
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
class CostCode:
    """Represents a cost code in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    number: Optional[int] = None
    name: Optional[str] = None
    description: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['CostCode']:
        """Creates a CostCode instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            number=getattr(row, 'Number', None),
            name=getattr(row, 'Name', None),
            description=getattr(row, 'Desc', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_cost_code(cost_code: CostCode) -> PersistenceResponse:
    """
    Creates a new cost code in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateCostCode(?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    cost_code.created_datetime,
                    cost_code.modified_datetime,
                    cost_code.number,
                    cost_code.name,
                    cost_code.description,
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Cost code created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Cost code not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create cost code: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_cost_codes() -> PersistenceResponse:
    """
    Retrieves all cost codes from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCostCodes}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[CostCode.from_db_row(row) for row in rows],
                        message="Cost codes retrieved",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Cost codes not retrieved",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read cost codes: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_cost_code_by_number(cost_code_number: int) -> PersistenceResponse:
    """
    Retrieves a cost code from the database by number.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCostCodeByNumber(?)}"
                row = cursor.execute(sql, cost_code_number).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CostCode.from_db_row(row),
                        message="Cost code retrieved",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Cost code by number not retrieved",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read cost code by number: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_cost_code_by_name(cost_code_name: str) -> PersistenceResponse:
    """
    Retrieves a cost code from the database by name.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCostCodeByName(?)}"
                row = cursor.execute(sql, cost_code_name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CostCode.from_db_row(row),
                        message="Cost code retrieved",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Cost code by name not retrieved",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read cost code by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_cost_code_by_id(cost_code_id: int) -> PersistenceResponse:
    """
    Retrieves a cost code from the database by id.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCostCodeById(?)}"
                row = cursor.execute(sql, cost_code_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CostCode.from_db_row(row),
                        message="Cost code retrieved",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Cost code by id not retrieved",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read cost code by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_cost_code_by_guid(cost_code_guid: str) -> PersistenceResponse:
    """
    Retrieves a cost code from the database by guid.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCostCodeByGuid(?)}"
                row = cursor.execute(sql, cost_code_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=CostCode.from_db_row(row),
                        message="Cost code retrieved",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Cost code by guid not retrieved",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read cost code by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
