# Python Standard Library Imports
import base64
import logging
from typing import Optional, Tuple
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.contract_labor.business.model import ContractLabor
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ContractLaborRepository:
    """
    Repository for ContractLabor persistence operations.
    """

    def __init__(self):
        """Initialize the ContractLaborRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ContractLabor]:
        """
        Convert a database row into a ContractLabor dataclass.
        """
        if not row:
            return None

        try:
            return ContractLabor(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=getattr(row, "VendorId", None),
                project_id=getattr(row, "ProjectId", None),
                employee_name=getattr(row, "EmployeeName", None),
                job_name=getattr(row, "JobName", None),
                work_date=getattr(row, "WorkDate", None),
                time_in=getattr(row, "TimeIn", None),
                time_out=getattr(row, "TimeOut", None),
                break_time=getattr(row, "BreakTime", None),
                regular_hours=Decimal(str(getattr(row, "RegularHours", None))) if getattr(row, "RegularHours", None) is not None else None,
                overtime_hours=Decimal(str(getattr(row, "OvertimeHours", None))) if getattr(row, "OvertimeHours", None) is not None else None,
                total_hours=Decimal(str(getattr(row, "TotalHours", None))) if getattr(row, "TotalHours", None) is not None else None,
                hourly_rate=Decimal(str(getattr(row, "HourlyRate", None))) if getattr(row, "HourlyRate", None) is not None else None,
                markup=Decimal(str(getattr(row, "Markup", None))) if getattr(row, "Markup", None) is not None else None,
                total_amount=Decimal(str(getattr(row, "TotalAmount", None))) if getattr(row, "TotalAmount", None) is not None else None,
                sub_cost_code_id=getattr(row, "SubCostCodeId", None),
                description=getattr(row, "Description", None),
                billing_period_start=getattr(row, "BillingPeriodStart", None),
                status=getattr(row, "Status", None),
                bill_line_item_id=getattr(row, "BillLineItemId", None),
                bill_vendor_id=getattr(row, "BillVendorId", None),
                bill_date=getattr(row, "BillDate", None),
                due_date=getattr(row, "DueDate", None),
                bill_number=getattr(row, "BillNumber", None),
                import_batch_id=getattr(row, "ImportBatchId", None),
                source_file=getattr(row, "SourceFile", None),
                source_row=getattr(row, "SourceRow", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during contract labor mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during contract labor mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        employee_name: str,
        work_date: str,
        total_hours: Decimal,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        job_name: Optional[str] = None,
        time_in: Optional[str] = None,
        time_out: Optional[str] = None,
        break_time: Optional[str] = None,
        regular_hours: Optional[Decimal] = None,
        overtime_hours: Optional[Decimal] = None,
        hourly_rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        total_amount: Optional[Decimal] = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        billing_period_start: Optional[str] = None,
        status: str = "pending_review",
        bill_line_item_id: Optional[int] = None,
        import_batch_id: Optional[str] = None,
        source_file: Optional[str] = None,
        source_row: Optional[int] = None,
    ) -> ContractLabor:
        """
        Create a new contract labor entry.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateContractLabor",
                    params={
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "EmployeeName": employee_name,
                        "JobName": job_name,
                        "WorkDate": work_date,
                        "TimeIn": time_in,
                        "TimeOut": time_out,
                        "BreakTime": break_time,
                        "RegularHours": float(regular_hours) if regular_hours is not None else None,
                        "OvertimeHours": float(overtime_hours) if overtime_hours is not None else None,
                        "TotalHours": float(total_hours),
                        "HourlyRate": float(hourly_rate) if hourly_rate is not None else None,
                        "Markup": float(markup) if markup is not None else None,
                        "TotalAmount": float(total_amount) if total_amount is not None else None,
                        "SubCostCodeId": sub_cost_code_id,
                        "Description": description,
                        "BillingPeriodStart": billing_period_start,
                        "Status": status,
                        "BillLineItemId": bill_line_item_id,
                        "ImportBatchId": import_batch_id,
                        "SourceFile": source_file,
                        "SourceRow": source_row,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateContractLabor did not return a row.")
                    raise map_database_error(Exception("CreateContractLabor failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create contract labor: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[ContractLabor]:
        """
        Read all contract labor entries.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLabors",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all contract labors: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ContractLabor]:
        """
        Read a contract labor entry by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read contract labor by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ContractLabor]:
        """
        Read a contract labor entry by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read contract labor by public ID: {error}")
            raise map_database_error(error)

    def read_by_natural_key(
        self,
        *,
        employee_name: str,
        work_date: str,
        job_name: Optional[str] = None,
        time_in: Optional[str] = None,
        time_out: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[ContractLabor]:
        """
        Find an existing entry with the same natural key (for duplicate detection).
        Key: employee_name, work_date, job_name, time_in, time_out, description.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborByNaturalKey",
                    params={
                        "EmployeeName": employee_name or "",
                        "WorkDate": work_date,
                        "JobName": job_name,
                        "TimeIn": time_in,
                        "TimeOut": time_out,
                        "Description": description,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during read contract labor by natural key: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> list[ContractLabor]:
        """
        Read all contract labor entries for a specific vendor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborsByVendorId",
                    params={"VendorId": vendor_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contract labors by vendor ID: {error}")
            raise map_database_error(error)

    def read_by_billing_period(self, billing_period_start: str) -> list[ContractLabor]:
        """
        Read all contract labor entries for a specific billing period.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborsByBillingPeriod",
                    params={"BillingPeriodStart": billing_period_start},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contract labors by billing period: {error}")
            raise map_database_error(error)

    def read_by_status(self, status: str) -> list[ContractLabor]:
        """
        Read all contract labor entries with a specific status.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborsByStatus",
                    params={"Status": status},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contract labors by status: {error}")
            raise map_database_error(error)

    def read_by_import_batch_id(self, import_batch_id: str) -> list[ContractLabor]:
        """
        Read all contract labor entries from a specific import batch.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborsByImportBatchId",
                    params={"ImportBatchId": import_batch_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contract labors by import batch ID: {error}")
            raise map_database_error(error)

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        billing_period_start: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sort_by: str = "WorkDate",
        sort_direction: str = "DESC",
    ) -> list[ContractLabor]:
        """
        Read contract labor entries with pagination and filtering.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborsPaginated",
                    params={
                        "PageNumber": page_number,
                        "PageSize": page_size,
                        "SearchTerm": search_term,
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "Status": status,
                        "BillingPeriodStart": billing_period_start,
                        "StartDate": start_date,
                        "EndDate": end_date,
                        "SortBy": sort_by,
                        "SortDirection": sort_direction,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read contract labors paginated: {error}")
            raise map_database_error(error)

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        billing_period_start: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        """
        Count contract labor entries matching the filter criteria.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CountContractLabors",
                    params={
                        "SearchTerm": search_term,
                        "VendorId": vendor_id,
                        "ProjectId": project_id,
                        "Status": status,
                        "BillingPeriodStart": billing_period_start,
                        "StartDate": start_date,
                        "EndDate": end_date,
                    },
                )
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count contract labors: {error}")
            raise map_database_error(error)

    def update_by_id(self, contract_labor: ContractLabor) -> Optional[ContractLabor]:
        """
        Update a contract labor entry by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": contract_labor.id,
                    "RowVersion": contract_labor.row_version_bytes,
                    "VendorId": contract_labor.vendor_id,
                    "ProjectId": contract_labor.project_id,
                    "EmployeeName": contract_labor.employee_name,
                    "JobName": contract_labor.job_name,
                    "WorkDate": contract_labor.work_date,
                    "TimeIn": contract_labor.time_in,
                    "TimeOut": contract_labor.time_out,
                    "BreakTime": contract_labor.break_time,
                    "RegularHours": float(contract_labor.regular_hours) if contract_labor.regular_hours is not None else None,
                    "OvertimeHours": float(contract_labor.overtime_hours) if contract_labor.overtime_hours is not None else None,
                    "TotalHours": float(contract_labor.total_hours) if contract_labor.total_hours is not None else None,
                    "HourlyRate": float(contract_labor.hourly_rate) if contract_labor.hourly_rate is not None else None,
                    "Markup": float(contract_labor.markup) if contract_labor.markup is not None else None,
                    "TotalAmount": float(contract_labor.total_amount) if contract_labor.total_amount is not None else None,
                    "SubCostCodeId": contract_labor.sub_cost_code_id,
                    "Description": contract_labor.description,
                    "BillingPeriodStart": contract_labor.billing_period_start,
                    "Status": contract_labor.status,
                    "BillLineItemId": contract_labor.bill_line_item_id,
                    "ImportBatchId": contract_labor.import_batch_id,
                    "SourceFile": contract_labor.source_file,
                    "SourceRow": contract_labor.source_row,
                }
                call_procedure(
                    cursor=cursor,
                    name="UpdateContractLaborById",
                    params=params,
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update contract labor by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ContractLabor]:
        """
        Delete a contract labor entry by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteContractLaborById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete contract labor by ID: {error}")
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[ContractLabor]:
        """
        Delete a contract labor entry by public ID.
        """
        try:
            # First get the entry to find the internal ID
            entry = self.read_by_public_id(public_id=public_id)
            if not entry:
                return None
            return self.delete_by_id(id=entry.id)
        except Exception as error:
            logger.error(f"Error during delete contract labor by public ID: {error}")
            raise map_database_error(error)

    def update_bill_info(
        self,
        *,
        id: int,
        row_version: bytes,
        bill_vendor_id: Optional[int] = None,
        bill_date: Optional[str] = None,
        due_date: Optional[str] = None,
        bill_number: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[ContractLabor]:
        """
        Update only the bill-related fields of a contract labor entry.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateContractLaborBillInfo",
                    params={
                        "Id": id,
                        "RowVersion": row_version,
                        "BillVendorId": bill_vendor_id,
                        "BillDate": bill_date,
                        "DueDate": due_date,
                        "BillNumber": bill_number,
                        "Status": status,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during update contract labor bill info: {error}")
            raise map_database_error(error)

    def get_last_rate_for_vendor(self, vendor_id: int) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        Get the last used hourly rate and markup for a vendor (for carry-forward).
        
        Returns:
            Tuple of (hourly_rate, markup)
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadLastRateForVendor",
                    params={"VendorId": vendor_id},
                )
                row = cursor.fetchone()
                if row:
                    hourly_rate = Decimal(str(row.HourlyRate)) if getattr(row, "HourlyRate", None) is not None else None
                    markup = Decimal(str(row.Markup)) if getattr(row, "Markup", None) is not None else None
                    return (hourly_rate, markup)
                return (None, None)
        except Exception as error:
            logger.error(f"Error during get last rate for vendor: {error}")
            raise map_database_error(error)

    def bulk_update_status(
        self,
        ids: list[int],
        status: str,
        bill_line_item_id: Optional[int] = None,
    ) -> int:
        """
        Bulk update status for multiple contract labor entries.
        
        Returns:
            Number of rows updated
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                ids_str = ",".join(str(id) for id in ids)
                call_procedure(
                    cursor=cursor,
                    name="UpdateContractLaborStatusByIds",
                    params={
                        "Ids": ids_str,
                        "Status": status,
                        "BillLineItemId": bill_line_item_id,
                    },
                )
                row = cursor.fetchone()
                return row.UpdatedCount if row else 0
        except Exception as error:
            logger.error(f"Error during bulk update status: {error}")
            raise map_database_error(error)

    def update_status_and_link(
        self,
        id: int,
        status: str,
        bill_line_item_id: Optional[int] = None,
    ) -> Optional[ContractLabor]:
        """
        Update status and bill_line_item_id for a single entry.
        Used when linking contract labor entries to bill line items.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Use direct SQL update for this simple operation
                cursor.execute(
                    """
                    UPDATE [dbo].[ContractLabor]
                    SET [Status] = ?,
                        [BillLineItemId] = ?,
                        [ModifiedDatetime] = GETUTCDATE()
                    WHERE [Id] = ?
                    """,
                    (status, bill_line_item_id, id),
                )
                conn.commit()
                return self.read_by_id(id)
        except Exception as error:
            logger.error(f"Error during update status and link: {error}")
            raise map_database_error(error)

    def read_by_bill_line_item_id(self, bill_line_item_id: int) -> list[ContractLabor]:
        """
        Read all contract labor entries linked to a specific bill line item.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT * FROM [dbo].[ContractLabor]
                    WHERE [BillLineItemId] = ?
                    ORDER BY [WorkDate], [EmployeeName]
                    """,
                    (bill_line_item_id,),
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read by bill line item ID: {error}")
            raise map_database_error(error)

    def get_daily_summary(
        self,
        *,
        employee_name: str,
        work_date: str,
        exclude_entry_id: int = None,
    ) -> dict:
        """
        Get daily summary for an employee on a specific date.
        Returns dict with:
        - total_imported_hours: Total hours from all imports for this worker/date
        - entry_count: Number of entries for this worker/date
        - allocated_other_entries: Hours already allocated in line items (other entries)
        - allocated_this_entry: Hours allocated in line items (this entry)
        - remaining_to_allocate: Hours left to allocate
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadContractLaborDailySummary",
                    params={
                        "EmployeeName": employee_name,
                        "WorkDate": work_date,
                        "ExcludeEntryId": exclude_entry_id,
                    },
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "total_imported_hours": float(row.TotalImportedHours) if row.TotalImportedHours else 0.0,
                        "entry_count": row.EntryCount or 0,
                        "allocated_other_entries": float(row.AllocatedOtherEntries) if row.AllocatedOtherEntries else 0.0,
                        "allocated_this_entry": float(row.AllocatedThisEntry) if row.AllocatedThisEntry else 0.0,
                        "remaining_to_allocate": float(row.RemainingToAllocate) if row.RemainingToAllocate else 0.0,
                    }
                return {
                    "total_imported_hours": 0.0,
                    "entry_count": 0,
                    "allocated_other_entries": 0.0,
                    "allocated_this_entry": 0.0,
                    "remaining_to_allocate": 0.0,
                }
        except Exception as error:
            logger.error(f"Error getting daily summary: {error}")
            raise map_database_error(error)
