"""
Persistence layer for mapping Build One Project to Intuit Customer.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pyodbc

from integrations.adapters import register_adapter
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@register_adapter
@dataclass
class MapProjectToIntuitCustomer:
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    project_id: Optional[int] = None
    intuit_customer_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['MapProjectToIntuitCustomer']:
        if not row:
            return None
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            project_id=getattr(row, 'ProjectId', None),
            intuit_customer_id=getattr(row, 'IntuitCustomerId', None),
        )


def create_map_project_to_intuit_customer(project_id: int, intuit_customer_id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMapProjectIntuitCustomer (?, ?)}"
                rowcount = cursor.execute(sql, int(project_id), int(intuit_customer_id)).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Project Intuit Customer created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Map Project Intuit Customer not created",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Map Project Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_project_to_intuit_customers() -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapProjectIntuitCustomers}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[MapProjectToIntuitCustomer.from_db_row(r) for r in rows],
                        message="Map Project Intuit Customers found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=[],
                    message="No Map Project Intuit Customers found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Project Intuit Customers: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_project_to_intuit_customer_by_guid(guid: str) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapProjectToIntuitCustomerByGUID (?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=MapProjectToIntuitCustomer.from_db_row(row),
                        message="Map Project Intuit Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="No Map Project Intuit Customer found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Project Intuit Customer by GUID: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_project_to_intuit_customer_by_project_id(project_id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapProjectIntuitCustomerByProjectId (?)}"
                row = cursor.execute(sql, int(project_id)).fetchone()
                if row:
                    return PersistenceResponse(
                        data=MapProjectToIntuitCustomer.from_db_row(row),
                        message="Map Project Intuit Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="No Map Project Intuit Customer found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Project Intuit Customer by Project Id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_map_project_to_intuit_customer(mapping: MapProjectToIntuitCustomer) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMapProjectIntuitCustomerById (?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    int(mapping.id),
                    int(mapping.project_id),
                    int(mapping.intuit_customer_id)
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Project Intuit Customer updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Map Project Intuit Customer not updated",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update Map Project Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_map_project_to_intuit_customer_by_id(id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteMapProjectIntuitCustomerById (?)}"
                rowcount = cursor.execute(sql, int(id)).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Project Intuit Customer deleted",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Map Project Intuit Customer not deleted",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to delete Map Project Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )

