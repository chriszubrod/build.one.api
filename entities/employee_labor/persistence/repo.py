# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.employee_labor.business.model import EmployeeLabor
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class EmployeeLaborRepository:
    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[EmployeeLabor]:
        if not row:
            return None
        try:
            return EmployeeLabor(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                employee_id=row.EmployeeId,
                project_id=row.ProjectId,
                work_date=row.WorkDate,
                billing_period_start=row.BillingPeriodStart,
                billing_period_end=row.BillingPeriodEnd,
                total_hours=row.TotalHours,
                hourly_rate=row.HourlyRate,
                markup=row.Markup,
                total_amount=row.TotalAmount,
                sub_cost_code_id=row.SubCostCodeId,
                description=row.Description,
                status=row.Status,
                source_time_entry_id=row.SourceTimeEntryId,
                invoice_line_item_id=row.InvoiceLineItemId,
                employee_name=getattr(row, "EmployeeName", None),
                project_name=getattr(row, "ProjectName", None),
            )
        except Exception as error:
            logger.error(f"Error mapping EmployeeLabor: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        employee_id: int,
        project_id: Optional[int],
        work_date: str,
        billing_period_start: str,
        billing_period_end: str,
        total_hours: Optional[Decimal] = None,
        hourly_rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        total_amount: Optional[Decimal] = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        status: str = "pending_review",
        source_time_entry_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> EmployeeLabor:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateEmployeeLabor",
                    params={
                        "EmployeeId": employee_id,
                        "ProjectId": project_id,
                        "WorkDate": work_date,
                        "BillingPeriodStart": billing_period_start,
                        "BillingPeriodEnd": billing_period_end,
                        "TotalHours": total_hours if total_hours is not None else Decimal("0"),
                        "HourlyRate": hourly_rate,
                        "Markup": markup,
                        "TotalAmount": total_amount,
                        "SubCostCodeId": sub_cost_code_id,
                        "Description": description,
                        "Status": status,
                        "SourceTimeEntryId": source_time_entry_id,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("CreateEmployeeLabor failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create employee labor: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[EmployeeLabor]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadEmployeeLaborById", params={"Id": id})
            return self._from_db(cursor.fetchone())

    def read_by_public_id(self, public_id: str) -> Optional[EmployeeLabor]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadEmployeeLaborByPublicId", params={"PublicId": public_id})
            return self._from_db(cursor.fetchone())

    def read_by_natural_key(
        self,
        *,
        employee_id: int,
        project_id: Optional[int],
        work_date: str,
        billing_period_start: str,
    ) -> Optional[EmployeeLabor]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ReadEmployeeLaborByNaturalKey",
                params={
                    "EmployeeId": employee_id,
                    "ProjectId": project_id,
                    "WorkDate": work_date,
                    "BillingPeriodStart": billing_period_start,
                },
            )
            return self._from_db(cursor.fetchone())

    def read_by_billing_period(self, billing_period_start: str) -> list[EmployeeLabor]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ReadEmployeeLaborsByBillingPeriod",
                params={"BillingPeriodStart": billing_period_start},
            )
            return [self._from_db(r) for r in cursor.fetchall() if r]

    def read_by_status(self, status: str, billing_period_start: Optional[str] = None) -> list[EmployeeLabor]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ReadEmployeeLaborsByStatus",
                params={"Status": status, "BillingPeriodStart": billing_period_start},
            )
            return [self._from_db(r) for r in cursor.fetchall() if r]

    def update_by_id(self, el: EmployeeLabor) -> Optional[EmployeeLabor]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="UpdateEmployeeLaborById",
                params={
                    "Id": el.id,
                    "RowVersion": el.row_version_bytes,
                    "ProjectId": el.project_id,
                    "TotalHours": el.total_hours,
                    "HourlyRate": el.hourly_rate,
                    "Markup": el.markup,
                    "TotalAmount": el.total_amount,
                    "SubCostCodeId": el.sub_cost_code_id,
                    "Description": el.description,
                    "Status": el.status,
                    "InvoiceLineItemId": el.invoice_line_item_id,
                },
            )
            return self._from_db(cursor.fetchone())

    def delete_by_id(self, id: int) -> None:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="DeleteEmployeeLaborById", params={"Id": id})
