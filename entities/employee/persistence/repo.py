# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.employee.business.model import Employee
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class EmployeeRepository:
    """Repository for Employee persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Employee]:
        """Convert a database row into an Employee dataclass."""
        if not row:
            return None

        try:
            return Employee(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                firstname=row.Firstname,
                lastname=row.Lastname,
                email=getattr(row, "Email", None),
                hourly_rate=row.HourlyRate,
                markup=row.Markup,
                is_active=bool(row.IsActive),
                is_deleted=bool(row.IsDeleted),
                notes=getattr(row, "Notes", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during employee mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during employee mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        firstname: str,
        lastname: str,
        email: Optional[str] = None,
        hourly_rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        is_active: bool = True,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Employee:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Firstname": firstname,
                    "Lastname": lastname,
                    "Email": email,
                    "HourlyRate": hourly_rate,
                    "Markup": markup,
                    "IsActive": 1 if is_active else 0,
                    "Notes": notes,
                    "CreatedByUserId": created_by_user_id,
                }
                call_procedure(
                    cursor=cursor,
                    name="CreateEmployee",
                    params=params,
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateEmployee did not return a row.")
                    raise map_database_error(Exception("CreateEmployee failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create employee: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Employee]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmployees",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all employees: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Employee]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmployeeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read employee by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Employee]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmployeeByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read employee by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, firstname: str, lastname: str) -> Optional[Employee]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadEmployeeByName",
                    params={"Firstname": firstname, "Lastname": lastname},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read employee by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, employee: Employee) -> Optional[Employee]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": employee.id,
                    "RowVersion": employee.row_version_bytes,
                    "Firstname": employee.firstname,
                    "Lastname": employee.lastname,
                    "Email": employee.email,
                    "HourlyRate": employee.hourly_rate,
                    "Markup": employee.markup,
                    "Notes": employee.notes,
                }
                if employee.is_active is not None:
                    params["IsActive"] = 1 if employee.is_active else 0
                call_procedure(
                    cursor=cursor,
                    name="UpdateEmployeeById",
                    params=params,
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update employee by ID: {error}")
            raise map_database_error(error)

    def soft_delete_by_public_id(self, public_id: str) -> Optional[Employee]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="SoftDeleteEmployeeByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during soft delete employee by public ID: {error}")
            raise map_database_error(error)
