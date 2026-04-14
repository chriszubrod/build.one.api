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
    """

    def __init__(self):
        """Initialize the CostCodeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[CostCode]:
        """
        Convert database row to CostCode model.
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
                description=row.Description,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during from db: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Internal error during from db: {error}")
            raise map_database_error(error)

    def create(self, *, number: str, name: str, description: Optional[str] = None) -> CostCode:
        """
        Create a new cost code.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateCostCode",
                    params={
                        "Number": number,
                        "Name": name,
                        "Description": description,
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
        Read all cost codes.
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

    def read_by_id(self, id: int) -> Optional[CostCode]:
        """
        Read cost code by ID.
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
                        "Description": cost_code.description,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateCostCodeById returned no row (id=%s); possible row-version conflict or record not found.",
                        cost_code.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the cost code may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update cost code by ID: {error}")
            raise map_database_error(error)

    def upsert(self, *, number: str, name: str, description: Optional[str] = None) -> CostCode:
        """
        Create or update a cost code by Number.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertCostCode",
                    params={
                        "Number": number,
                        "Name": name,
                        "Description": description,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("UpsertCostCode did not return a row.")
                    raise map_database_error(Exception("UpsertCostCode failed."))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during upsert cost code: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[CostCode]:
        """
        Delete cost code by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteCostCodeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete cost code by ID: {error}")
            raise map_database_error(error)
