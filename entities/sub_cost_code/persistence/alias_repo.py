# Python Standard Library Imports
from typing import List, Optional
import logging
import base64

# Third-party Imports
import pyodbc

# Local Imports
from entities.sub_cost_code.business.alias_model import SubCostCodeAlias
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error
)

logger = logging.getLogger(__name__)


class SubCostCodeAliasRepository:
    """
    Repository for SubCostCodeAlias entity persistence operations.

    Handles all database operations for SubCostCodeAlias entities using
    Azure SQL Server stored procedures.
    """

    def __init__(self):
        """Initialize the SubCostCodeAliasRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[SubCostCodeAlias]:
        """
        Convert database row to SubCostCodeAlias model.

        Args:
            row: Database row from pyodbc cursor

        Returns:
            SubCostCodeAlias model instance or None if row is empty
        """
        if not row:
            return None

        try:
            return SubCostCodeAlias(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                sub_cost_code_id=row.SubCostCodeId,
                alias=row.Alias,
                source=row.Source
            )
        except AttributeError as error:
            logger.error(f"Attribute error during from db: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Internal error during from db: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        sub_cost_code_id: int,
        alias: str,
        source: Optional[str] = None,
    ) -> SubCostCodeAlias:
        """
        Create a new sub cost code alias.

        Args:
            sub_cost_code_id: Parent sub cost code ID
            alias: The alias value
            source: Origin of the alias (e.g. 'manual', 'bill_agent')
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateSubCostCodeAlias",
                    params={
                        "SubCostCodeId": sub_cost_code_id,
                        "Alias": alias,
                        "Source": source,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create SubCostCodeAlias failed.")
                    raise map_database_error(Exception("Create SubCostCodeAlias failed."))
                return self._from_db(row)
        except AttributeError as error:
            logger.error(f"Attribute error during create sub cost code alias: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Error during create sub cost code alias: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[SubCostCodeAlias]:
        """
        Read all sub cost code aliases.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeAliases",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except AttributeError as error:
            logger.error(f"Attribute error during read all sub cost code aliases: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Error during read all sub cost code aliases: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[SubCostCodeAlias]:
        """
        Read sub cost code alias by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeAliasById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except AttributeError as error:
            logger.error(f"Attribute error during read sub cost code alias by ID: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Error during read sub cost code alias by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[SubCostCodeAlias]:
        """
        Read sub cost code alias by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeAliasByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except AttributeError as error:
            logger.error(f"Attribute error during read sub cost code alias by public ID: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Error during read sub cost code alias by public ID: {error}")
            raise map_database_error(error)

    def read_by_sub_cost_code_id(self, sub_cost_code_id: int) -> List[SubCostCodeAlias]:
        """
        Read all aliases for a given sub cost code.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeAliasBySubCostCodeId",
                    params={"SubCostCodeId": sub_cost_code_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except AttributeError as error:
            logger.error(f"Attribute error during read sub cost code aliases by parent: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Error during read sub cost code aliases by parent: {error}")
            raise map_database_error(error)

    def read_by_alias(self, alias: str) -> Optional[SubCostCodeAlias]:
        """
        Read sub cost code alias by alias value.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeAliasByAlias",
                    params={"Alias": alias},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except AttributeError as error:
            logger.error(f"Attribute error during read sub cost code alias by alias: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Error during read sub cost code alias by alias: {error}")
            raise map_database_error(error)

    def update_by_id(self, sub_cost_code_alias: SubCostCodeAlias) -> Optional[SubCostCodeAlias]:
        """
        Update sub cost code alias by ID with optimistic concurrency control.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateSubCostCodeAliasById",
                    params={
                        "Id": sub_cost_code_alias.id,
                        "RowVersion": sub_cost_code_alias.row_version_bytes,
                        "SubCostCodeId": sub_cost_code_alias.sub_cost_code_id,
                        "Alias": sub_cost_code_alias.alias,
                        "Source": sub_cost_code_alias.source,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update SubCostCodeAlias failed due to concurrency mismatch.")
                    raise map_database_error(Exception("RowVersion conflict"))
                return self._from_db(row)
        except AttributeError as error:
            logger.error(f"Attribute error during update sub cost code alias by ID: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Error during update sub cost code alias by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[SubCostCodeAlias]:
        """
        Delete sub cost code alias by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteSubCostCodeAliasById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except AttributeError as error:
            logger.error(f"Attribute error during delete sub cost code alias by ID: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Error during delete sub cost code alias by ID: {error}")
            raise map_database_error(error)
