# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.contract.business.model import Contract
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class ContractRepository:
    """Repository for Contract persistence operations (pyodbc + stored procedures)."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Contract]:
        """Convert a database row into a Contract dataclass."""
        if not row:
            return None
        try:
            return Contract(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                created_by_user_id=row.CreatedByUserId,
                project_id=row.ProjectId,
                builders_fee_rate=row.BuildersFeeRate,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during contract mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during contract mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        project_id: int,
        builders_fee_rate: Optional[Decimal] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Contract:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateContract",
                    params={
                        "ProjectId": project_id,
                        "BuildersFeeRate": builders_fee_rate,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateContract did not return a row.")
                    raise map_database_error(Exception("CreateContract failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create contract: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Contract]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractByPublicId",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during read contract by public ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> list[Contract]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractsByProjectId",
                    params={"ProjectId": project_id},
                )
                return [self._from_db(row) for row in cursor.fetchall() if row]
        except Exception as error:
            logger.error(f"Error during read contracts by project ID: {error}")
            raise map_database_error(error)

    def update_by_public_id(self, contract: Contract) -> Optional[Contract]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateContractByPublicId",
                    params={
                        "PublicId": contract.public_id,
                        "RowVersion": contract.row_version_bytes,
                        "BuildersFeeRate": contract.builders_fee_rate,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during update contract by public ID: {error}")
            raise map_database_error(error)
