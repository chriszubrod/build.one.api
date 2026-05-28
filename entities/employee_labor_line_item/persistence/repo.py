# Python Standard Library Imports
import base64
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.employee_labor_line_item.business.model import EmployeeLaborLineItem
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class EmployeeLaborLineItemRepository:
    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[EmployeeLaborLineItem]:
        if not row:
            return None
        try:
            return EmployeeLaborLineItem(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                employee_labor_id=row.EmployeeLaborId,
                line_date=row.LineDate,
                project_id=row.ProjectId,
                sub_cost_code_id=row.SubCostCodeId,
                description=row.Description,
                hours=row.Hours,
                rate=row.Rate,
                markup=row.Markup,
                price=row.Price,
                is_billable=bool(row.IsBillable),
                is_overhead=bool(row.IsOverhead),
                invoice_line_item_id=row.InvoiceLineItemId,
            )
        except Exception as error:
            logger.error(f"Error mapping EmployeeLaborLineItem: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        employee_labor_id: int,
        line_date: Optional[str] = None,
        project_id: Optional[int] = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        hours: Optional[Decimal] = None,
        rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        is_billable: bool = True,
        is_overhead: bool = False,
        invoice_line_item_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> EmployeeLaborLineItem:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="CreateEmployeeLaborLineItem",
                params={
                    "EmployeeLaborId": employee_labor_id,
                    "LineDate": line_date,
                    "ProjectId": project_id,
                    "SubCostCodeId": sub_cost_code_id,
                    "Description": description,
                    "Hours": hours,
                    "Rate": rate,
                    "Markup": markup,
                    "Price": price,
                    "IsBillable": 1 if is_billable else 0,
                    "IsOverhead": 1 if is_overhead else 0,
                    "InvoiceLineItemId": invoice_line_item_id,
                    "CreatedByUserId": created_by_user_id,
                },
            )
            row = cursor.fetchone()
            if not row:
                raise map_database_error(Exception("CreateEmployeeLaborLineItem failed"))
            return self._from_db(row)

    def read_by_id(self, id: int) -> Optional[EmployeeLaborLineItem]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadEmployeeLaborLineItemById", params={"Id": id})
            return self._from_db(cursor.fetchone())

    def read_by_public_id(self, public_id: str) -> Optional[EmployeeLaborLineItem]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="ReadEmployeeLaborLineItemByPublicId", params={"PublicId": public_id})
            return self._from_db(cursor.fetchone())

    def read_by_employee_labor_id(self, employee_labor_id: int) -> list[EmployeeLaborLineItem]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="ReadEmployeeLaborLineItemsByEmployeeLaborId",
                params={"EmployeeLaborId": employee_labor_id},
            )
            return [self._from_db(r) for r in cursor.fetchall() if r]

    def update_by_id(self, li: EmployeeLaborLineItem) -> Optional[EmployeeLaborLineItem]:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="UpdateEmployeeLaborLineItemById",
                params={
                    "Id": li.id,
                    "RowVersion": li.row_version_bytes,
                    "LineDate": li.line_date,
                    "ProjectId": li.project_id,
                    "SubCostCodeId": li.sub_cost_code_id,
                    "Description": li.description,
                    "Hours": li.hours,
                    "Rate": li.rate,
                    "Markup": li.markup,
                    "Price": li.price,
                    "IsBillable": 1 if li.is_billable else 0 if li.is_billable is not None else None,
                    "IsOverhead": 1 if li.is_overhead else 0 if li.is_overhead is not None else None,
                    "InvoiceLineItemId": li.invoice_line_item_id,
                },
            )
            return self._from_db(cursor.fetchone())

    def delete_by_id(self, id: int) -> None:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(cursor=cursor, name="DeleteEmployeeLaborLineItemById", params={"Id": id})

    def delete_by_employee_labor_id(self, employee_labor_id: int) -> None:
        with get_connection() as conn:
            cursor = conn.cursor()
            call_procedure(
                cursor=cursor,
                name="DeleteEmployeeLaborLineItemsByEmployeeLaborId",
                params={"EmployeeLaborId": employee_labor_id},
            )
