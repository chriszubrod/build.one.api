# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.project_address.business.model import ProjectAddress
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ProjectAddressRepository:
    """
    Repository for ProjectAddress persistence operations.
    """

    def __init__(self):
        """Initialize the ProjectAddressRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ProjectAddress]:
        """
        Convert a database row into a ProjectAddress dataclass.
        """
        if not row:
            return None

        try:
            return ProjectAddress(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                project_id=row.ProjectId,
                address_id=row.AddressId,
                address_type_id=row.AddressTypeId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during project address mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during project address mapping: {error}")
            raise map_database_error(error)

    def create(self, *, project_id: int, address_id: int, address_type_id: int) -> ProjectAddress:
        """
        Create a new project address.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateProjectAddress",
                    params={
                        "ProjectId": project_id,
                        "AddressId": address_id,
                        "AddressTypeId": address_type_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateProjectAddress did not return a row.")
                    raise map_database_error(Exception("CreateProjectAddress failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create project address: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[ProjectAddress]:
        """
        Read all project addresses.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectAddresses",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all project addresses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ProjectAddress]:
        """
        Read a project address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectAddressById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read project address by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ProjectAddress]:
        """
        Read a project address by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectAddressByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read project address by public ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> list[ProjectAddress]:
        """
        Read project addresses by project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectAddressByProjectId",
                    params={"ProjectId": project_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read project addresses by project ID: {error}")
            raise map_database_error(error)
    
    def read_by_address_id(self, address_id: int) -> list[ProjectAddress]:
        """
        Read project addresses by address ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectAddressByAddressId",
                    params={"AddressId": address_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read project addresses by address ID: {error}")
            raise map_database_error(error)
    
    def read_by_address_type_id(self, address_type_id: int) -> list[ProjectAddress]:
        """
        Read project addresses by address type ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectAddressByAddressTypeId",
                    params={"AddressTypeId": address_type_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read project addresses by address type ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, project_address: ProjectAddress) -> Optional[ProjectAddress]:
        """
        Update a project address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateProjectAddressById",
                    params={
                        "Id": project_address.id,
                        "RowVersion": project_address.row_version_bytes,
                        "ProjectId": project_address.project_id,
                        "AddressId": project_address.address_id,
                        "AddressTypeId": project_address.address_type_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update project address by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ProjectAddress]:
        """
        Delete a project address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteProjectAddressById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete project address by ID: {error}")
            raise map_database_error(error)
    
    def delete_by_project_id(self, project_id: int) -> None:
        """
        Delete all project addresses for a project by project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteProjectAddressByProjectId",
                    params={"ProjectId": project_id},
                )
        except Exception as error:
            logger.error(f"Error during delete project addresses by project ID: {error}")
            raise map_database_error(error)
