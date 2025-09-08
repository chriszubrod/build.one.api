from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
import pyodbc

from shared.database import get_db_connection
from shared.response import (
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
    with get_db_connection() as cnxn:
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


def read_intuit_customer_by_id(customer_id: str) -> PersistenceResponse:
    """Retrieves an Intuit customer from the database by ID."""
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadIntuitCustomerById(?)}"
                row = cursor.execute(sql, customer_id).fetchone()

                if row:
                    return PersistenceResponse(
                        data=IntuitCustomer.from_db_row(row),
                        message="Intuit Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="Intuit Customer not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Intuit Customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_intuit_customer_by_realm_id_and_customer_id(customer: IntuitCustomer) -> Dict[str, Any]:
    """Updates an existing Intuit customer in the database."""
    with get_db_connection() as cnxn:
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
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadIntuitProjects()}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[IntuitCustomer.from_db_row(row) for row in rows],
                        success=True,
                        timestamp=datetime.now(),
                        status_code=200,
                        message="Intuit Projects found"
                    )

                return PersistenceResponse(
                    data=None,
                    message="No Intuit Projects found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to read intuit projects: {str(e)}") from e


def read_intuit_customer_by_guid(guid: str):
    """Retrieves an Intuit customer by GUID."""
    print(f"Read Intuit Customer By GUID: {guid}")
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{ CALL ReadIntuitCustomerByIdGUID (?) }"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=IntuitCustomer.from_db_row(row),
                        message="Intuit Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                return PersistenceResponse(
                    data=None,
                    message="Intuit Customer not found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )
        except pyodbc.Error as e:
            raise PersistenceResponse(
                data=None,
                message=f"Failed to read intuit customer by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            ) from e


def read_intuit_customers_available():
    """
    Retrieves Intuit customers that are not yet in dbo.Customer and are not jobs/projects
    (IsJob is NULL or 0) and (IsProject is NULL or 0).
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = (
                    "SELECT c.[GUID], c.[RealmId], c.[Id], c.[DisplayName], c.[FullyQualifiedName], "
                    "c.[IsJob], c.[ParentRefValue], c.[Level], c.[IsProject], c.[ClientEntityId], c.[IsActive], "
                    "c.[SyncToken], c.[V4IDPseudonym], CAST(c.[CreatedDatetime] AS NVARCHAR(MAX)) AS CreatedDatetime, "
                    "CAST(c.[LastUpdatedDatetime] AS NVARCHAR(MAX)) AS LastUpdatedDatetime "
                    "FROM intuit.Customer c "
                    "LEFT JOIN dbo.Customer dc ON dc.IntuitCustomerId = c.Id "
                    "WHERE dc.Id IS NULL AND (c.IsJob IS NULL OR c.IsJob = 0) AND (c.IsProject IS NULL OR c.IsProject = 0) "
                    "ORDER BY c.DisplayName"
                )
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return SuccessResponse(
                        message="Available Intuit Customers found",
                        data=[IntuitCustomer.from_db_row(row) for row in rows],
                        status_code=200
                    )
                return PersistenceResponse(
                    message="No available Intuit Customers found",
                    status_code=404
                )
        except pyodbc.Error as e:
            raise DatabaseError(f"Failed to read available Intuit customers: {str(e)}") from e
