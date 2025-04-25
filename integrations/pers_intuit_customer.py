from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
import pyodbc

import persistence.pers_database as pers_database
from persistence.pers_response import (
    DatabaseError,
    ValidationError,
    NotFoundError,
    exception_handler,
    SuccessResponse,
    PersistenceResponse
)


@dataclass
class IntuitCustomer:
    """Represents an Intuit customer in the system."""
    guid: Optional[str] = None
    realm_id: Optional[str] = None
    id: Optional[str] = None  # Intuit's customer ID
    display_name: Optional[str] = None
    fully_qualified_name: Optional[str] = None
    is_job: Optional[int] = None
    parent_ref_value: Optional[str] = None
    level: Optional[int] = None
    is_project: Optional[int] = None
    client_entity_id: Optional[str] = None
    is_active: Optional[int] = None
    sync_token: Optional[str] = None
    v4id_pseudonym: Optional[str] = None
    created_datetime: Optional[datetime] = None
    last_updated_datetime: Optional[datetime] = None

    def to_db_params(self) -> tuple:
        """Converts customer object to database parameters."""
        return (
            self.realm_id,
            self.id,
            self.display_name,
            self.fully_qualified_name,
            self.is_job,
            self.parent_ref_value,
            self.level,
            self.is_project,
            self.client_entity_id,
            self.is_active,
            self.sync_token,
            self.v4id_pseudonym,
            self.created_datetime,
            self.last_updated_datetime
        )

    @classmethod
    def from_db_row(cls, row) -> 'IntuitCustomer':
        """Creates an IntuitCustomer instance from a database row."""
        return cls(
            guid=getattr(row, 'GUID', None),
            realm_id=getattr(row, 'RealmId', None),
            id=getattr(row, 'Id', None),
            display_name=getattr(row, 'DisplayName', None),
            fully_qualified_name=getattr(row, 'FullyQualifiedName', None),
            is_job=getattr(row, 'IsJob', None),
            parent_ref_value=getattr(row, 'ParentRefValue', None),
            level=getattr(row, 'Level', None),
            is_project=getattr(row, 'IsProject', None),
            client_entity_id=getattr(row, 'ClientEntityId', None),
            is_active=getattr(row, 'IsActive', None),
            sync_token=getattr(row, 'SyncToken', None),
            v4id_pseudonym=getattr(row, 'V4IDPseudonym', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            last_updated_datetime=getattr(row, 'LastUpdatedDatetime', None)
        )


def create_intuit_customer(customer: IntuitCustomer) -> Dict[str, Any]:
    """Creates a new Intuit customer in the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateIntuitCustomer(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(sql, customer.to_db_params()).rowcount
                cnxn.commit()

                if rowcount == 1:
                    return {
                        "message": "Intuit Customer has been successfully created.",
                        "rowcount": rowcount,
                        "status_code": 201
                    }
                raise ValidationError("Failed to create Intuit Customer")

        except pyodbc.Error as e:
            cnxn.rollback()
            exception_handler(e)  # This will raise appropriate exception


def read_intuit_customer_by_id(customer_id: str) -> Dict[str, Any]:
    """Retrieves an Intuit customer from the database by ID."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadIntuitCustomerById(?)}"
                row = cursor.execute(sql, customer_id).fetchone()

                if row:
                    return {
                        "message": row,
                        "rowcount": 1,
                        "status_code": 201
                    }
                raise NotFoundError(f"Intuit Customer with ID {customer_id} not found")

        except pyodbc.Error as e:
            exception_handler(e)  # This will raise appropriate exception


def update_intuit_customer_by_realm_id_and_customer_id(customer: IntuitCustomer) -> Dict[str, Any]:
    """Updates an existing Intuit customer in the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = (
                    "{CALL UpdateIntuitCustomerByRealmIdAndCustomerId"
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                )
                rowcount = cursor.execute(sql, customer.to_db_params()).rowcount
                cnxn.commit()

                if rowcount == 1:
                    return {
                        "message": "Intuit Customer has been successfully updated.",
                        "rowcount": rowcount,
                        "status_code": 201
                    }
                raise NotFoundError(
                    f"Intuit Customer with ID {customer.id} and "
                    f"Realm {customer.realm_id} not found"
                )

        except pyodbc.Error as e:
            cnxn.rollback()
            exception_handler(e)  # This will raise appropriate exception


def read_intuit_projects():
    """
    Retrieves all intuit projects from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadIntuitProjects()}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return SuccessResponse(
                        message="Intuit Projects found",
                        data=[IntuitCustomer.from_db_row(row) for row in rows],
                        status_code=200
                    )

                return PersistenceResponse(
                    message="No Intuit Projects found",
                    status_code=404
                )

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to read intuit projects: {str(e)}") from e
