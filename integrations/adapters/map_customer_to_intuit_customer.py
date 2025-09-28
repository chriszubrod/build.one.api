"""
Persistence layer for mapping Build One Customer to Intuit Customer.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pyodbc

from integrations.adapters import register_adapter
from integrations.intuit.persistence.pers_intuit_customer import IntuitCustomer
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@register_adapter
@dataclass
class MapCustomerToIntuitCustomer:
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    customer_id: Optional[int] = None
    intuit_customer_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'MapCustomerToIntuitCustomer':
        if not row:
            return None
        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            customer_id=getattr(row, 'CustomerId'),
            intuit_customer_id=getattr(row, 'IntuitCustomerId'),
        )


def create_map_customer_to_intuit_customer(customer_id: int, intuit_customer_id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMapCustomerIntuitCustomer (?, ?)}"
                rowcount = cursor.execute(sql, int(customer_id), int(intuit_customer_id)).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Customer Intuit Customer created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Map Customer Intuit Customer not created",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Map Customer Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_customer_to_intuit_customers() -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapCustomerIntuitCustomers}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[MapCustomerToIntuitCustomer.from_db_row(row) for row in rows],
                        message="Map Customer Intuit Customers found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=[],
                    message="No Map Customer Intuit Customers found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Customer Intuit Customers: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_customer_to_intuit_customer_by_customer_id(customer_id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapCustomerIntuitCustomerByCustomerId (?)}"
                row = cursor.execute(sql, int(customer_id)).fetchone()
                if row:
                    return PersistenceResponse(
                        data=MapCustomerToIntuitCustomer.from_db_row(row),
                        message="Map Customer Intuit Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="No Map Customer Intuit Customer found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Customer Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_customer_to_intuit_customer_by_intuit_customer_id(intuit_customer_id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapCustomerIntuitCustomerByIntuitCustomerId (?)}"
                row = cursor.execute(sql, int(intuit_customer_id)).fetchone()
                if row:
                    return PersistenceResponse(
                        data=MapCustomerToIntuitCustomer.from_db_row(row),
                        message="Map Customer Intuit Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="No Map Customer Intuit Customer found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Customer Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_map_customer_to_intuit_customer(mapping: MapCustomerToIntuitCustomer) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMapCustomerIntuitCustomerById (?, ?, ?)}"
                rowcount = cursor.execute(sql, int(mapping.id), int(mapping.customer_id), int(mapping.intuit_customer_id)).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Customer Intuit Customer updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Map Customer Intuit Customer not updated",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update Map Customer Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_map_customer_to_intuit_customer_by_id(id: int) -> PersistenceResponse:
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteMapCustomerIntuitCustomerById (?)}"
                rowcount = cursor.execute(sql, int(id)).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Customer Intuit Customer deleted",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Map Customer Intuit Customer not deleted",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to delete Map Customer Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_available_intuit_customers_for_customer_map() -> PersistenceResponse:
    """Reads Intuit customers not mapped to any Build One customer (and not jobs/projects)."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAvailableIntuitCustomersForCustomerMap}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[IntuitCustomer.from_db_row(r) for r in rows],
                        message="Available Intuit Customers for mapping found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=[],
                    message="No available Intuit Customers for mapping found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read available Intuit Customers for mapping: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
