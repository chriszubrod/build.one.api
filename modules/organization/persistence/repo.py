# Python Standard Library Imports
from typing import Optional, List
from uuid import UUID
import logging
import base64

# Third-party Imports
import pyodbc

# Local Imports
from modules.organization.business.model import Organization
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class OrganizationRepository:
    """
    Repository for Organization entity persistence operations.
    
    This class handles all database operations for Organization entities using
    Azure SQL Server stored procedures. It provides a clean interface for
    CRUD operations while abstracting away database-specific details.
    """

    def __init__(self):
        """Initialize the OrganizationRepository."""
        pass


    def _from_db(self, row: dict) -> Optional[Organization]: # Organization or None
        """
        Convert database row to Organization model.
        
        Transforms a database row (pyodbc.Row) into an Organization domain model.
        Handles missing fields and data type conversions.
        
        Args:
            row: Database row from pyodbc cursor
            
        Returns:
            Organization model instance or None if row is empty
            
        Raises:
            DatabaseError: If row parsing fails or required fields are missing
        """
        if not row:
            return None

        try:
            return Organization(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode('ascii'),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                website=row.Website,
            )
        except AttributeError as e:
            logger.error(f"Attribute error during from db: {e}")
            raise map_database_error(e)
        except Exception as e:
            logger.error(f"Internal error during from db: {e}")
            raise map_database_error(e)


    def create(self, *, name: str, website: Optional[str] = None) -> Organization:
        """
        Create a new organization.
        
        Inserts a new organization record into the database using the
        CreateOrganization stored procedure.
        
        Args:
            name: Organization name
            website: Organization website URL (optional)
            
        Returns:
            Created Organization object with generated ID and timestamps
            
        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateOrganization",
                    params={
                        "Name": name,
                        "Website": website,
                    }
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create Organization failed.")
                    raise map_database_error(Exception("Create Organization failed."))
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error during create organization: {e}")
            raise map_database_error(e)


    def read_all(self) -> list[Organization]:
        """
        Read all non-deleted organizations.
        
        Retrieves all active organizations from the database using the
        ReadOrganizations stored procedure.
        
        Returns:
            List of Organization objects (empty list if none found)
            
        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOrganizations",
                    params={}
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as e:
            logger.error(f"Error during read all organizations: {e}")
            raise map_database_error(e)


    def read_by_id(self, id: str) -> Optional[Organization]:
        """
        Read organization by ID.
        
        Retrieves a specific organization by its unique identifier using the
        ReadOrganizationById stored procedure.
        
        Args:
            id: Organization unique identifier
            
        Returns:
            Organization object if found, None otherwise
            
        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOrganizationById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error during read organization by ID: {e}")
            raise map_database_error(e)


    def read_by_public_id(self, public_id: str) -> Optional[Organization]:
        """
        Read organization by public ID.
        
        Retrieves a specific organization by its public identifier using the
        ReadOrganizationByPublicId stored procedure.
        
        Args:
            public_id: Organization public identifier
            
        Returns:
            Organization object if found, None otherwise
            
        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOrganizationByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error during read organization by public ID: {e}")
            raise map_database_error(e)


    def read_by_name(self, name: str) -> Optional[Organization]:
        """
        Read organization by name.
        
        Retrieves a specific organization by its name using the
        ReadOrganizationByName stored procedure.
        
        Args:
            name: Organization name to search for
            
        Returns:
            Organization object if found, None otherwise
            
        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOrganizationByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error during read organization by name: {e}")
            raise map_database_error(e)


    def update_by_id(self, org: Organization) -> Optional[Organization]:
        """
        Update organization by ID with optimistic concurrency control.
        
        Updates an existing organization using the UpdateOrganizationById stored procedure.
        Uses row version for optimistic concurrency control to prevent conflicts.
        
        Args:
            org: Organization object with updated data and current row version
            
        Returns:
            Updated Organization object if successful, None if not found or version mismatch
            
        Raises:
            DatabaseError: If database operation fails
        """
        try:
            print('data type of row_version', type(org.row_version))
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateOrganizationById",
                    params={
                        "Id": org.id,
                        "RowVersion": org.row_version_bytes,
                        "Name": org.name,
                        "Website": org.website,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as e:
            logger.error(f"Error during update organization by ID: {e}")
            raise map_database_error(e)

    def delete_by_id(self, id: str) -> Optional[Organization]:
        """
        Delete organization by ID with optimistic concurrency control.
        
        Marks an organization as deleted using the DeleteOrganizationById stored procedure.
        Uses row version for optimistic concurrency control to prevent conflicts.
        
        Args:
            id: Organization unique identifier
            row_version: Current row version for concurrency control (base64 encoded)
            
        Returns:
            Soft deleted Organization object if successful, None if not found or version mismatch
            
        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteOrganizationById",
                    params={
                        "Id": id
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as e:
            logger.error(f"Error during delete organization by ID: {e}")
            raise map_database_error(e)
