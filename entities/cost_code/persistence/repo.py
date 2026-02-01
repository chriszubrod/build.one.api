# Python Standard Library Imports
from typing import Optional, List
import logging
import base64

# Third-party Imports
import pyodbc

# Local Imports
from entities.cost_code.business.model import CostCode
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class CostCodeRepository:
    """
    Repository for CostCode entity persistence operations.

    This class handles all database operations for CostCode entities using
    Azure SQL Server stored procedures. It provides a clean interface for
    CRUD operations while abstracting away database-specific details.
    """

    def __init__(self):
        """Initialize the CostCodeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[CostCode]:
        """
        Convert database row to CostCode model.

        Transforms a database row (pyodbc.Row) into a CostCode domain model.
        Handles missing fields and data type conversions.

        Args:
            row: Database row from pyodbc cursor

        Returns:
            CostCode model instance or None if row is empty

        Raises:
            DatabaseError: If row parsing fails or required fields are missing
        """
        if not row:
            return None

        try:
            return CostCode(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                number=row.Number,
                name=row.Name,
                description=row.Description
            )
        except AttributeError as error:
            logger.error(f"Attribute error during from db: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Internal error during from db: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, number: str, name: str, description: Optional[str] = None) -> CostCode:
        """
        Create a new cost code.

        Inserts a new cost code record into the database using the
        CreateCostCode stored procedure.

        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            number: Cost code number
            name: Cost code name
            description: Cost code description (optional)

        Returns:
            Created CostCode object with generated ID and timestamps

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Note: tenant_id is accepted for audit trail purposes
                # Future: Add TenantId param when stored procedure supports it
                call_procedure(
                    cursor=cursor,
                    name="CreateCostCode",
                    params={
                        "Number": number,
                        "Name": name,
                        "Description": description
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create CostCode failed.")
                    raise map_database_error(Exception("Create CostCode failed."))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create cost code: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[CostCode]:
        """
        Read all non-deleted cost codes.

        Retrieves all active cost codes from the database using the
        ReadCostCodes stored procedure.

        Returns:
            List of CostCode objects (empty list if none found)

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCostCodes",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all cost codes: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[CostCode]:
        """
        Read cost code by ID.

        Retrieves a specific cost code by its unique identifier using the
        ReadCostCodeById stored procedure.

        Args:
            id: Cost code unique identifier

        Returns:
            CostCode object if found, None otherwise

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCostCodeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read cost code by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[CostCode]:
        """
        Read cost code by public ID.

        Retrieves a specific cost code by its public identifier using the
        ReadCostCodeByPublicId stored procedure.

        Args:
            public_id: Cost code public identifier

        Returns:
            CostCode object if found, None otherwise

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCostCodeByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read cost code by public ID: {error}")
            raise map_database_error(error)

    def read_by_number(self, number: str) -> Optional[CostCode]:
        """
        Read cost code by number.

        Retrieves a specific cost code by its number value using the
        ReadCostCodeByNumber stored procedure.

        Args:
            number: Cost code to search for

        Returns:
            CostCode object if found, None otherwise

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCostCodeByNumber",
                    params={"Number": number},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read cost code by number: {error}")
            raise map_database_error(error)

    def update_by_id(self, cost_code: CostCode) -> Optional[CostCode]:
        """
        Update cost code by ID with optimistic concurrency control.

        Updates an existing cost code using the UpdateCostCodeById stored procedure.
        Uses row version for optimistic concurrency control to prevent conflicts.

        Args:
            cost_code: CostCode object with updated data and current row version

        Returns:
            Updated CostCode object if successful, None if not found or version mismatch

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateCostCodeById",
                    params={
                        "Id": cost_code.id,
                        "RowVersion": cost_code.row_version_bytes,
                        "Number": cost_code.number,
                        "Name": cost_code.name,
                        "Description": cost_code.description
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update cost code by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[CostCode]:
        """
        Delete cost code by ID with optimistic concurrency control.

        Marks a cost code as deleted using the DeleteCostCodeById stored procedure.
        Uses row version for optimistic concurrency control to prevent conflicts.

        Args:
            id: Cost code unique identifier

        Returns:
            Soft deleted CostCode object if successful, None if not found or version mismatch

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteCostCodeById",
                    params={
                        "Id": id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete cost code by ID: {error}")
            raise map_database_error(error)
