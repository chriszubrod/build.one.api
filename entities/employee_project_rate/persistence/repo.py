# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.employee_project_rate.business.model import EmployeeProjectRate
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class EmployeeProjectRateRepository:
    """Repository for EmployeeProjectRate persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[EmployeeProjectRate]:
        if not row:
            return None
        try:
            return EmployeeProjectRate(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                employee_id=row.EmployeeId,
                project_id=row.ProjectId,
                hourly_rate=row.HourlyRate,
                markup=row.Markup,
                notes=getattr(row, "Notes", None),
                is_deleted=bool(row.IsDeleted),
                employee_name=getattr(row, "EmployeeName", None),
                employee_public_id=getattr(row, "EmployeePublicId", None),
                project_name=getattr(row, "ProjectName", None),
                project_public_id=getattr(row, "ProjectPublicId", None),
            )
        except Exception as error:
            logger.error(f"Error mapping EmployeeProjectRate row: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        employee_id: int,
        project_id: int,
        hourly_rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> EmployeeProjectRate:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateEmployeeProjectRate",
                    params={
                        "EmployeeId": employee_id,
                        "ProjectId": project_id,
                        "HourlyRate": hourly_rate,
                        "Markup": markup,
                        "Notes": notes,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateEmployeeProjectRate failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create employee project rate: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[EmployeeProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmployeeProjectRateById", params={"Id": id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during read employee project rate by id: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[EmployeeProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmployeeProjectRateByPublicId", params={"PublicId": public_id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during read employee project rate by public id: {error}")
            raise map_database_error(error)

    def read_by_employee_id(self, employee_id: int) -> list[EmployeeProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmployeeProjectRatesByEmployeeId", params={"EmployeeId": employee_id})
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error during read employee project rates by employee id: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> list[EmployeeProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadEmployeeProjectRatesByProjectId", params={"ProjectId": project_id})
                return [self._from_db(r) for r in cursor.fetchall() if r]
        except Exception as error:
            logger.error(f"Error during read employee project rates by project id: {error}")
            raise map_database_error(error)

    def update_by_id(self, rate: EmployeeProjectRate) -> Optional[EmployeeProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateEmployeeProjectRateById",
                    params={
                        "Id": rate.id,
                        "RowVersion": rate.row_version_bytes,
                        "HourlyRate": rate.hourly_rate,
                        "Markup": rate.markup,
                        "Notes": rate.notes,
                    },
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during update employee project rate: {error}")
            raise map_database_error(error)

    def soft_delete_by_public_id(self, public_id: str) -> Optional[EmployeeProjectRate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="SoftDeleteEmployeeProjectRateByPublicId", params={"PublicId": public_id})
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error during soft-delete employee project rate: {error}")
            raise map_database_error(error)

    def read_effective_rate(self, *, employee_id: int, project_id: int) -> dict:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEffectiveRateForEmployeeProject",
                    params={"EmployeeId": employee_id, "ProjectId": project_id},
                )
                row = cursor.fetchone()
                if not row:
                    return {"hourly_rate": None, "markup": None, "rate_source": "none"}
                return {
                    "hourly_rate": row.HourlyRate,
                    "markup": row.Markup,
                    "rate_source": row.RateSource,
                }
        except Exception as error:
            logger.error(f"Error during read_effective_rate (employee): {error}")
            raise map_database_error(error)
