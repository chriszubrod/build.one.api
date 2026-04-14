# Python Standard Library Imports
from typing import List, Optional
import logging
import base64

# Third-party Imports
import pyodbc

# Local Imports
from entities.sub_cost_code.business.model import SubCostCode
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error
)

logger = logging.getLogger(__name__)


class SubCostCodeRepository:
    """
    Repository for SubCostCode entity persistence operations.
    """

    def __init__(self):
        """Initialize the SubCostCodeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[SubCostCode]:
        """
        Convert database row to SubCostCode model.
        """
        if not row:
            return None

        try:
            return SubCostCode(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                number=row.Number,
                name=row.Name,
                description=row.Description,
                cost_code_id=row.CostCodeId,
                aliases=getattr(row, "Aliases", None),
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
        number: str,
        name: str,
        description: Optional[str] = None,
        cost_code_id: int,
        aliases: Optional[str] = None,
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
                        "Number": number,
                        "Name": name,
                        "Description": description,
                        "CostCodeId": cost_code_id,
                        "Aliases": aliases,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create SubCostCode failed.")
                    raise map_database_error(Exception("Create SubCostCode failed."))
                return self._from_db(row)
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
        except Exception as error:
            logger.error(f"Error during read all sub cost codes: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[SubCostCode]:
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
        except Exception as error:
            logger.error(f"Error during read sub cost code by public ID: {error}")
            raise map_database_error(error)

    def read_by_number(self, number: str) -> Optional[SubCostCode]:
        """
        Read sub cost code by number.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeByNumber",
                    params={"Number": number},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read sub cost code by number: {error}")
            raise map_database_error(error)

    def read_by_cost_code_id(self, cost_code_id: int) -> List[SubCostCode]:
        """
        Read sub cost codes by parent cost code ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeByCostCodeId",
                    params={"CostCodeId": cost_code_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read sub cost codes by parent: {error}")
            raise map_database_error(error)

    def read_by_alias(self, alias: str) -> Optional[SubCostCode]:
        """
        Read sub cost code by alias value (searches pipe-delimited Aliases field).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadSubCostCodeByAlias",
                    params={"Alias": alias},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read sub cost code by alias: {error}")
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
                        "Number": sub_cost_code.number,
                        "Name": sub_cost_code.name,
                        "Description": sub_cost_code.description,
                        "CostCodeId": sub_cost_code.cost_code_id,
                        "Aliases": sub_cost_code.aliases,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateSubCostCodeById returned no row (id=%s); possible row-version conflict or record not found.",
                        sub_cost_code.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the sub cost code may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update sub cost code by ID: {error}")
            raise map_database_error(error)

    def upsert(
        self,
        *,
        number: str,
        name: str,
        description: Optional[str] = None,
        cost_code_id: int,
        aliases: Optional[str] = None,
    ) -> SubCostCode:
        """
        Create or update a sub cost code by Number + CostCodeId.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertSubCostCode",
                    params={
                        "Number": number,
                        "Name": name,
                        "Description": description,
                        "CostCodeId": cost_code_id,
                        "Aliases": aliases,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("UpsertSubCostCode did not return a row.")
                    raise map_database_error(Exception("UpsertSubCostCode failed."))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during upsert sub cost code: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[SubCostCode]:
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
        except Exception as error:
            logger.error(f"Error during delete sub cost code by ID: {error}")
            raise map_database_error(error)
