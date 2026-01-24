# Python Standard Library Imports
import logging
from typing import Optional, Tuple
from decimal import Decimal

# Third-party Imports

# Local Imports
from modules.contract_labor.business.model import ContractLabor
from modules.contract_labor.persistence.repo import ContractLaborRepository
from modules.vendor.business.service import VendorService
from modules.project.business.service import ProjectService
from modules.sub_cost_code.business.service import SubCostCodeService

logger = logging.getLogger(__name__)


class ContractLaborService:
    """
    Service for ContractLabor entity business operations.
    """

    def __init__(self, repo: Optional[ContractLaborRepository] = None):
        """Initialize the ContractLaborService."""
        self.repo = repo or ContractLaborRepository()

    def create(
        self,
        *,
        vendor_public_id: str,
        employee_name: str,
        work_date: str,
        total_hours: Decimal,
        project_public_id: Optional[str] = None,
        time_in: Optional[str] = None,
        time_out: Optional[str] = None,
        break_time: Optional[str] = None,
        regular_hours: Optional[Decimal] = None,
        overtime_hours: Optional[Decimal] = None,
        hourly_rate: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        status: str = "pending_review",
        import_batch_id: Optional[str] = None,
        source_file: Optional[str] = None,
        source_row: Optional[int] = None,
    ) -> ContractLabor:
        """
        Create a new contract labor entry.
        """
        # Validate and resolve vendor
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
        vendor_id = vendor.id
        
        # Resolve project if provided
        project_id = None
        if project_public_id:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            project_id = project.id
        
        # Validate SubCostCode if provided
        if sub_cost_code_id is not None:
            sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
            if not sub_cost_code:
                raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")
        
        # Calculate billing period
        billing_period_start = ContractLabor.calculate_billing_period_start(work_date)
        
        # Calculate total amount if rate is provided
        total_amount = None
        if hourly_rate is not None:
            base_amount = total_hours * hourly_rate
            if markup is not None:
                total_amount = base_amount * (Decimal("1") + markup)
            else:
                total_amount = base_amount
        
        return self.repo.create(
            vendor_id=vendor_id,
            project_id=project_id,
            employee_name=employee_name,
            work_date=work_date,
            time_in=time_in,
            time_out=time_out,
            break_time=break_time,
            regular_hours=regular_hours,
            overtime_hours=overtime_hours,
            total_hours=total_hours,
            hourly_rate=hourly_rate,
            markup=markup,
            total_amount=total_amount,
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            billing_period_start=billing_period_start,
            status=status,
            import_batch_id=import_batch_id,
            source_file=source_file,
            source_row=source_row,
        )

    def read_all(self) -> list[ContractLabor]:
        """
        Read all contract labor entries.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[ContractLabor]:
        """
        Read a contract labor entry by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[ContractLabor]:
        """
        Read a contract labor entry by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: int) -> list[ContractLabor]:
        """
        Read all contract labor entries for a specific vendor.
        """
        return self.repo.read_by_vendor_id(vendor_id)

    def read_by_vendor_public_id(self, vendor_public_id: str) -> list[ContractLabor]:
        """
        Read all contract labor entries for a vendor by public ID.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            return []
        return self.repo.read_by_vendor_id(vendor_id=vendor.id)

    def read_by_billing_period(self, billing_period_start: str) -> list[ContractLabor]:
        """
        Read all contract labor entries for a specific billing period.
        """
        return self.repo.read_by_billing_period(billing_period_start)

    def read_by_status(self, status: str) -> list[ContractLabor]:
        """
        Read all contract labor entries with a specific status.
        """
        return self.repo.read_by_status(status)

    def read_by_import_batch_id(self, import_batch_id: str) -> list[ContractLabor]:
        """
        Read all contract labor entries from a specific import batch.
        """
        return self.repo.read_by_import_batch_id(import_batch_id)

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
        return self.repo.read_paginated(
            page_number=page_number,
            page_size=page_size,
            search_term=search_term,
            vendor_id=vendor_id,
            project_id=project_id,
            status=status,
            billing_period_start=billing_period_start,
            start_date=start_date,
            end_date=end_date,
            sort_by=sort_by,
            sort_direction=sort_direction,
        )

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
        return self.repo.count(
            search_term=search_term,
            vendor_id=vendor_id,
            project_id=project_id,
            status=status,
            billing_period_start=billing_period_start,
            start_date=start_date,
            end_date=end_date,
        )

    def update_by_public_id(self, public_id: str, contract_labor) -> Optional[ContractLabor]:
        """
        Update a contract labor entry by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        
        # Update row version
        if hasattr(contract_labor, 'row_version') and contract_labor.row_version:
            existing.row_version = contract_labor.row_version
        
        # Resolve vendor if provided
        if hasattr(contract_labor, 'vendor_public_id') and contract_labor.vendor_public_id:
            vendor = VendorService().read_by_public_id(public_id=contract_labor.vendor_public_id)
            if not vendor:
                raise ValueError(f"Vendor with public_id '{contract_labor.vendor_public_id}' not found.")
            existing.vendor_id = vendor.id
        
        # Resolve project if provided (can be None to clear)
        if hasattr(contract_labor, 'project_public_id'):
            if contract_labor.project_public_id:
                project = ProjectService().read_by_public_id(public_id=contract_labor.project_public_id)
                if not project:
                    raise ValueError(f"Project with public_id '{contract_labor.project_public_id}' not found.")
                existing.project_id = project.id
            else:
                existing.project_id = None
        
        # Validate SubCostCode if provided
        if hasattr(contract_labor, 'sub_cost_code_id'):
            if contract_labor.sub_cost_code_id is not None:
                sub_cost_code = SubCostCodeService().read_by_id(id=str(contract_labor.sub_cost_code_id))
                if not sub_cost_code:
                    raise ValueError(f"SubCostCode with id '{contract_labor.sub_cost_code_id}' not found.")
            existing.sub_cost_code_id = contract_labor.sub_cost_code_id
        
        # Update simple fields
        if hasattr(contract_labor, 'employee_name') and contract_labor.employee_name is not None:
            existing.employee_name = contract_labor.employee_name
        if hasattr(contract_labor, 'work_date') and contract_labor.work_date is not None:
            existing.work_date = contract_labor.work_date
            # Recalculate billing period if work date changed
            existing.billing_period_start = ContractLabor.calculate_billing_period_start(contract_labor.work_date)
        if hasattr(contract_labor, 'time_in'):
            existing.time_in = contract_labor.time_in
        if hasattr(contract_labor, 'time_out'):
            existing.time_out = contract_labor.time_out
        if hasattr(contract_labor, 'break_time'):
            existing.break_time = contract_labor.break_time
        if hasattr(contract_labor, 'regular_hours'):
            existing.regular_hours = contract_labor.regular_hours
        if hasattr(contract_labor, 'overtime_hours'):
            existing.overtime_hours = contract_labor.overtime_hours
        if hasattr(contract_labor, 'total_hours') and contract_labor.total_hours is not None:
            existing.total_hours = contract_labor.total_hours
        if hasattr(contract_labor, 'hourly_rate'):
            existing.hourly_rate = contract_labor.hourly_rate
        if hasattr(contract_labor, 'markup'):
            existing.markup = contract_labor.markup
        if hasattr(contract_labor, 'description'):
            existing.description = contract_labor.description
        if hasattr(contract_labor, 'status') and contract_labor.status is not None:
            existing.status = contract_labor.status
        
        # Recalculate total amount
        if existing.hourly_rate is not None and existing.total_hours is not None:
            base_amount = existing.total_hours * existing.hourly_rate
            if existing.markup is not None:
                existing.total_amount = base_amount * (Decimal("1") + existing.markup)
            else:
                existing.total_amount = base_amount
        
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[ContractLabor]:
        """
        Delete a contract labor entry by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None

    def get_last_rate_for_vendor(self, vendor_public_id: str) -> Tuple[Optional[Decimal], Optional[Decimal]]:
        """
        Get the last used hourly rate and markup for a vendor (for carry-forward).
        
        Args:
            vendor_public_id: Public ID of the vendor
            
        Returns:
            Tuple of (hourly_rate, markup)
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            return (None, None)
        return self.repo.get_last_rate_for_vendor(vendor_id=vendor.id)

    def mark_as_ready(self, public_id: str) -> Optional[ContractLabor]:
        """
        Mark a contract labor entry as ready for billing.
        Validates that all required fields are set.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        
        # Validate required fields
        if not existing.vendor_id:
            raise ValueError("Vendor is required before marking as ready.")
        if not existing.project_id:
            raise ValueError("Project is required before marking as ready.")
        if not existing.sub_cost_code_id:
            raise ValueError("SubCostCode is required before marking as ready.")
        if not existing.hourly_rate:
            raise ValueError("Hourly rate is required before marking as ready.")
        if not existing.total_hours:
            raise ValueError("Total hours is required before marking as ready.")
        
        existing.status = "ready"
        return self.repo.update_by_id(existing)

    def bulk_mark_as_ready(self, public_ids: list[str]) -> dict:
        """
        Mark multiple contract labor entries as ready for billing.
        
        Returns:
            Dict with success count, error count, and error details
        """
        success_count = 0
        errors = []
        
        for public_id in public_ids:
            try:
                result = self.mark_as_ready(public_id=public_id)
                if result:
                    success_count += 1
                else:
                    errors.append({"public_id": public_id, "error": "Entry not found"})
            except ValueError as e:
                errors.append({"public_id": public_id, "error": str(e)})
            except Exception as e:
                logger.error(f"Error marking {public_id} as ready: {e}")
                errors.append({"public_id": public_id, "error": str(e)})
        
        return {
            "success_count": success_count,
            "error_count": len(errors),
            "errors": errors,
        }

    def bulk_delete(self, public_ids: list[str]) -> dict:
        """
        Delete multiple contract labor entries.
        Only entries with status 'pending_review' can be deleted.
        
        Returns:
            Dict with success count, error count, and error details
        """
        success_count = 0
        errors = []
        
        for public_id in public_ids:
            try:
                # Get the entry first to check status
                entry = self.read_by_public_id(public_id=public_id)
                if not entry:
                    errors.append({"public_id": public_id, "error": "Entry not found"})
                    continue
                
                # Only allow deletion of pending_review entries
                if entry.status == "billed":
                    errors.append({"public_id": public_id, "error": "Cannot delete billed entries"})
                    continue
                
                # Delete the entry
                result = self.repo.delete_by_public_id(public_id=public_id)
                if result:
                    success_count += 1
                else:
                    errors.append({"public_id": public_id, "error": "Delete failed"})
            except Exception as e:
                logger.error(f"Error deleting {public_id}: {e}")
                errors.append({"public_id": public_id, "error": str(e)})
        
        return {
            "success_count": success_count,
            "error_count": len(errors),
            "errors": errors,
        }

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
        return self.repo.bulk_update_status(
            ids=ids,
            status=status,
            bill_line_item_id=bill_line_item_id,
        )

    def get_ready_entries_grouped_for_billing(
        self,
        billing_period_start: str,
    ) -> dict:
        """
        Get all 'ready' entries for a billing period, grouped by vendor,
        then by SubCostCode+Project combination.
        
        This is used to prepare data for bill creation.
        
        Returns:
            Dict structure:
            {
                vendor_id: {
                    (sub_cost_code_id, project_id): [ContractLabor, ...],
                    ...
                },
                ...
            }
        """
        entries = self.repo.read_by_billing_period(billing_period_start)
        ready_entries = [e for e in entries if e.status == "ready"]
        
        grouped = {}
        for entry in ready_entries:
            vendor_id = entry.vendor_id
            if vendor_id not in grouped:
                grouped[vendor_id] = {}
            
            key = (entry.sub_cost_code_id, entry.project_id)
            if key not in grouped[vendor_id]:
                grouped[vendor_id][key] = []
            
            grouped[vendor_id][key].append(entry)
        
        return grouped
