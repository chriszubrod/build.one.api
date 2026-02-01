# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from services.module.business.model import Module
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ModuleRepository:
    """
    Repository for Module persistence operations.
    """

    def __init__(self):
        """Initialize the ModuleRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Module]:
        """
        Convert a database row into a Module dataclass.
        """
        if not row:
            return None

        try:
            return Module(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                route=row.Route,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during module mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during module mapping: {error}")
            raise map_database_error(error)

    def create(self, *, name: str, route: str) -> Module:
        """
        Create a new module.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateModule",
                    params={
                        "Name": name,
                        "Route": route,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateModule did not return a row.")
                    raise map_database_error(Exception("CreateModule failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create module: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Module]:
        """
        Read all modules.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadModules",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all modules: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[Module]:
        """
        Read a module by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadModuleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read module by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Module]:
        """
        Read a module by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadModuleByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read module by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Module]:
        """
        Read a module by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadModuleByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read module by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, module: Module) -> Optional[Module]:
        """
        Update a module by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateModuleById",
                    params={
                        "Id": module.id,
                        "RowVersion": module.row_version_bytes,
                        "Name": module.name,
                        "Route": module.route,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update module by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[Module]:
        """
        Delete a module by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteModuleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete module by ID: {error}")
            raise map_database_error(error)
