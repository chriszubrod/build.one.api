# Python Standard Library Imports
from typing import List, Optional
import logging
import base64

# Third-party Imports
import pyodbc

# Local Imports
from modules.sub_cost_code.business.model import SubCostCode
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
    DatabaseError,
)

logger = logging.getLogger(__name__)


class SubCostCodeRepository:
    """
    Repository for SubCostCode entity persistence operations.

    Handles all database operations for SubCostCode entities using
    Azure SQL Server stored procedures. Provides a consistent
    interface for CRUD operations while isolating database-specific
    behavior.
    """

    def __init__(self):
        """Initialize the SubCostCodeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[SubCostCode]:
        """
        Convert database row to SubCostCode model.

        Args:
            row: Database row from pyodbc cursor

        Returns:
            SubCostCode model instance or None if row is empty
        """
        if not row:
            return None

        try:
            row_version_value = getattr(row, "RowVersion", None)
            encoded_row_version = None
            if row_version_value:
                encoded_row_version = base64.b64encode(row_version_value).decode("ascii")

            return SubCostCode(
                id=getattr(row, "Id", None),
                public_id=getattr(row, "PublicId", None),
                row_version=encoded_row_version,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                cost_code_id=getattr(row, "CostCodeId", None),
                cost_code_public_id=getattr(row, "CostCodePublicId", None),
                cost_code_number=getattr(row, "CostCodeNumber", None),
                cost_code_name=getattr(row, "CostCodeName", None),
                number=getattr(row, "Number", None),
                name=getattr(row, "Name", None),
                description=getattr(row, "Description", None),
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
        cost_code_public_id: str,
        number: str,
        name: str,
        description: Optional[str] = None,
    ) -> SubCostCode:
        """
        Create a new sub cost code.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateSubCostCode",
                    params={
                        "CostCodePublicId": cost_code_public_id,
                        "Number": number,
                        "Name": name,
                        "Description": description,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create SubCostCode failed.")
                    raise map_database_error(Exception("Create SubCostCode failed."))
                return self._from_db(row)
        except DatabaseError as error:
            logger.error(f"Database error during create sub cost code: {error}")
            raise error
        except Exception as error:
            logger.error(f"Error during create sub cost code: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[SubCostCode]:
        """
        Read all sub cost codes.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodes",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except DatabaseError as error:
            logger.error(f"Database error during read all sub cost codes: {error}")
            raise error
        except Exception as error:
            logger.error(f"Error during read all sub cost codes: {error}")
            raise map_database_error(error)

    def read_by_cost_code_public_id(self, cost_code_public_id: str) -> List[SubCostCode]:
        """
        Read sub cost codes by parent cost code public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodesByCostCodePublicId",
                    params={"CostCodePublicId": cost_code_public_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except DatabaseError as error:
            logger.error(f"Database error during read sub cost codes by parent: {error}")
            raise error
        except Exception as error:
            logger.error(f"Error during read sub cost codes by parent: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[SubCostCode]:
        """
        Read sub cost code by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except DatabaseError as error:
            logger.error(f"Database error during read sub cost code by ID: {error}")
            raise error
        except Exception as error:
            logger.error(f"Error during read sub cost code by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[SubCostCode]:
        """
        Read sub cost code by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except DatabaseError as error:
            logger.error(f"Database error during read sub cost code by public ID: {error}")
            raise error
        except Exception as error:
            logger.error(f"Error during read sub cost code by public ID: {error}")
            raise map_database_error(error)

    def read_by_number(self, number: str, cost_code_public_id: str) -> Optional[SubCostCode]:
        """
        Read sub cost code by number within a parent cost code.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeByNumber",
                    params={
                        "Number": number,
                        "CostCodePublicId": cost_code_public_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except DatabaseError as error:
            logger.error(f"Database error during read sub cost code by number: {error}")
            raise error
        except Exception as error:
            logger.error(f"Error during read sub cost code by number: {error}")
            raise map_database_error(error)

    def update_by_id(self, sub_cost_code: SubCostCode) -> Optional[SubCostCode]:
        """
        Update sub cost code by ID with optimistic concurrency control.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateSubCostCodeById",
                    params={
                        "Id": sub_cost_code.id,
                        "RowVersion": sub_cost_code.row_version_bytes,
                        "CostCodePublicId": sub_cost_code.cost_code_public_id,
                        "Number": sub_cost_code.number,
                        "Name": sub_cost_code.name,
                        "Description": sub_cost_code.description,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update SubCostCode failed due to concurrency mismatch.")
                    raise map_database_error(Exception("RowVersion conflict"))
                return self._from_db(row)
        except DatabaseError as error:
            logger.error(f"Database error during update sub cost code by ID: {error}")
            raise error
        except Exception as error:
            logger.error(f"Error during update sub cost code by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[SubCostCode]:
        """
        Delete sub cost code by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteSubCostCodeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except DatabaseError as error:
            logger.error(f"Database error during delete sub cost code by ID: {error}")
            raise error
        except Exception as error:
            logger.error(f"Error during delete sub cost code by ID: {error}")
            raise map_database_error(error)
