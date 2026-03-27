# Python Standard Library Imports
import logging
from decimal import Decimal
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status

# Local Imports
from entities.contract_labor.business.service import ContractLaborService
from entities.contract_labor.business.import_service import ContractLaborImportService
from entities.contract_labor.api.schemas import (
    ContractLaborCreate,
    ContractLaborUpdate,
    ContractLaborResponse,
    ContractLaborBulkMarkReady,
    ContractLaborBulkMarkReadyResponse,
    ContractLaborBulkDelete,
    ContractLaborBulkDeleteResponse,
    ContractLaborLastRateResponse,
    ContractLaborImportResponse,
    ContractLaborBillUpdate,
    ContractLaborBillUpdateResponse,
)
from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/contract-labor",
    tags=["api", "Contract Labor"],
)


@router.post("", response_model=ContractLaborResponse, status_code=201)
def create_contract_labor(contract_labor: ContractLaborCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new contract labor entry.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": contract_labor.vendor_public_id,
            "project_public_id": contract_labor.project_public_id,
            "employee_name": contract_labor.employee_name,
            "work_date": contract_labor.work_date,
            "time_in": contract_labor.time_in,
            "time_out": contract_labor.time_out,
            "break_time": contract_labor.break_time,
            "regular_hours": contract_labor.regular_hours,
            "overtime_hours": contract_labor.overtime_hours,
            "total_hours": contract_labor.total_hours,
            "hourly_rate": contract_labor.hourly_rate,
            "markup": contract_labor.markup,
            "sub_cost_code_id": contract_labor.sub_cost_code_id,
            "description": contract_labor.description,
            "status": contract_labor.status or "pending_review",
            "import_batch_id": contract_labor.import_batch_id,
            "source_file": contract_labor.source_file,
            "source_row": contract_labor.source_row,
        },
        workflow_type="contract_labor_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create contract labor")
        )
    
    return result.get("data")


@router.get("", response_model=list[ContractLaborResponse])
def read_contract_labors(
    page_number: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    search_term: Optional[str] = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    project_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    billing_period_start: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    sort_by: str = Query(default="WorkDate"),
    sort_direction: str = Query(default="DESC"),
):
    """
    Read contract labor entries with pagination and filtering.
    """
    try:
        service = ContractLaborService()
        results = service.read_paginated(
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
        return [
            ContractLaborResponse(
                id=r.id,
                public_id=r.public_id,
                row_version=r.row_version,
                created_datetime=r.created_datetime,
                modified_datetime=r.modified_datetime,
                vendor_id=r.vendor_id,
                project_id=r.project_id,
                employee_name=r.employee_name,
                work_date=r.work_date,
                time_in=r.time_in,
                time_out=r.time_out,
                break_time=r.break_time,
                regular_hours=r.regular_hours,
                overtime_hours=r.overtime_hours,
                total_hours=r.total_hours,
                hourly_rate=r.hourly_rate,
                markup=r.markup,
                total_amount=r.total_amount,
                sub_cost_code_id=r.sub_cost_code_id,
                description=r.description,
                billing_period_start=r.billing_period_start,
                status=r.status,
                bill_line_item_id=r.bill_line_item_id,
                import_batch_id=r.import_batch_id,
                source_file=r.source_file,
                source_row=r.source_row,
            )
            for r in results
        ]
    except Exception as e:
        logger.exception("Error reading contract labors")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/count")
def count_contract_labors(
    search_term: Optional[str] = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    project_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    billing_period_start: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
):
    """
    Count contract labor entries matching the filter criteria.
    """
    try:
        service = ContractLaborService()
        count = service.count(
            search_term=search_term,
            vendor_id=vendor_id,
            project_id=project_id,
            status=status,
            billing_period_start=billing_period_start,
            start_date=start_date,
            end_date=end_date,
        )
        return {"count": count}
    except Exception as e:
        logger.exception("Error counting contract labors")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/by-status/{status}", response_model=list[ContractLaborResponse])
def read_contract_labors_by_status(status: str):
    """
    Read all contract labor entries with a specific status.
    """
    try:
        service = ContractLaborService()
        results = service.read_by_status(status=status)
        return [
            ContractLaborResponse(
                id=r.id,
                public_id=r.public_id,
                row_version=r.row_version,
                created_datetime=r.created_datetime,
                modified_datetime=r.modified_datetime,
                vendor_id=r.vendor_id,
                project_id=r.project_id,
                employee_name=r.employee_name,
                work_date=r.work_date,
                time_in=r.time_in,
                time_out=r.time_out,
                break_time=r.break_time,
                regular_hours=r.regular_hours,
                overtime_hours=r.overtime_hours,
                total_hours=r.total_hours,
                hourly_rate=r.hourly_rate,
                markup=r.markup,
                total_amount=r.total_amount,
                sub_cost_code_id=r.sub_cost_code_id,
                description=r.description,
                billing_period_start=r.billing_period_start,
                status=r.status,
                bill_line_item_id=r.bill_line_item_id,
                import_batch_id=r.import_batch_id,
                source_file=r.source_file,
                source_row=r.source_row,
            )
            for r in results
        ]
    except Exception as e:
        logger.exception("Error reading contract labors by status")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/by-billing-period/{billing_period_start}", response_model=list[ContractLaborResponse])
def read_contract_labors_by_billing_period(billing_period_start: str):
    """
    Read all contract labor entries for a specific billing period.
    """
    try:
        service = ContractLaborService()
        results = service.read_by_billing_period(billing_period_start=billing_period_start)
        return [
            ContractLaborResponse(
                id=r.id,
                public_id=r.public_id,
                row_version=r.row_version,
                created_datetime=r.created_datetime,
                modified_datetime=r.modified_datetime,
                vendor_id=r.vendor_id,
                project_id=r.project_id,
                employee_name=r.employee_name,
                work_date=r.work_date,
                time_in=r.time_in,
                time_out=r.time_out,
                break_time=r.break_time,
                regular_hours=r.regular_hours,
                overtime_hours=r.overtime_hours,
                total_hours=r.total_hours,
                hourly_rate=r.hourly_rate,
                markup=r.markup,
                total_amount=r.total_amount,
                sub_cost_code_id=r.sub_cost_code_id,
                description=r.description,
                billing_period_start=r.billing_period_start,
                status=r.status,
                bill_line_item_id=r.bill_line_item_id,
                import_batch_id=r.import_batch_id,
                source_file=r.source_file,
                source_row=r.source_row,
            )
            for r in results
        ]
    except Exception as e:
        logger.exception("Error reading contract labors by billing period")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/by-import-batch/{import_batch_id}", response_model=list[ContractLaborResponse])
def read_contract_labors_by_import_batch(import_batch_id: str):
    """
    Read all contract labor entries from a specific import batch.
    """
    try:
        service = ContractLaborService()
        results = service.read_by_import_batch_id(import_batch_id=import_batch_id)
        return [
            ContractLaborResponse(
                id=r.id,
                public_id=r.public_id,
                row_version=r.row_version,
                created_datetime=r.created_datetime,
                modified_datetime=r.modified_datetime,
                vendor_id=r.vendor_id,
                project_id=r.project_id,
                employee_name=r.employee_name,
                work_date=r.work_date,
                time_in=r.time_in,
                time_out=r.time_out,
                break_time=r.break_time,
                regular_hours=r.regular_hours,
                overtime_hours=r.overtime_hours,
                total_hours=r.total_hours,
                hourly_rate=r.hourly_rate,
                markup=r.markup,
                total_amount=r.total_amount,
                sub_cost_code_id=r.sub_cost_code_id,
                description=r.description,
                billing_period_start=r.billing_period_start,
                status=r.status,
                bill_line_item_id=r.bill_line_item_id,
                import_batch_id=r.import_batch_id,
                source_file=r.source_file,
                source_row=r.source_row,
            )
            for r in results
        ]
    except Exception as e:
        logger.exception("Error reading contract labors by import batch")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/last-rate/{vendor_public_id}", response_model=ContractLaborLastRateResponse)
def get_last_rate_for_vendor(vendor_public_id: str):
    """
    Get the last used hourly rate and markup for a vendor (for carry-forward).
    """
    try:
        service = ContractLaborService()
        hourly_rate, markup = service.get_last_rate_for_vendor(vendor_public_id=vendor_public_id)
        return ContractLaborLastRateResponse(
            vendor_public_id=vendor_public_id,
            hourly_rate=hourly_rate,
            markup=markup,
        )
    except Exception as e:
        logger.exception("Error getting last rate for vendor")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{public_id}", response_model=ContractLaborResponse)
def read_contract_labor_by_public_id(public_id: str):
    """
    Read a contract labor entry by public ID.
    """
    try:
        service = ContractLaborService()
        result = service.read_by_public_id(public_id=public_id)
        if not result:
            raise HTTPException(status_code=404, detail="Contract labor entry not found")
        return ContractLaborResponse(
            id=result.id,
            public_id=result.public_id,
            row_version=result.row_version,
            created_datetime=result.created_datetime,
            modified_datetime=result.modified_datetime,
            vendor_id=result.vendor_id,
            project_id=result.project_id,
            employee_name=result.employee_name,
            work_date=result.work_date,
            time_in=result.time_in,
            time_out=result.time_out,
            break_time=result.break_time,
            regular_hours=result.regular_hours,
            overtime_hours=result.overtime_hours,
            total_hours=result.total_hours,
            hourly_rate=result.hourly_rate,
            markup=result.markup,
            total_amount=result.total_amount,
            sub_cost_code_id=result.sub_cost_code_id,
            description=result.description,
            billing_period_start=result.billing_period_start,
            status=result.status,
            bill_line_item_id=result.bill_line_item_id,
            import_batch_id=result.import_batch_id,
            source_file=result.source_file,
            source_row=result.source_row,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error reading contract labor by public ID")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{public_id}", response_model=ContractLaborResponse)
def update_contract_labor(public_id: str, contract_labor: ContractLaborUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a contract labor entry by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "contract_labor": contract_labor,
        },
        workflow_type="contract_labor_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update contract labor")
        )
    
    return result.get("data")


@router.delete("/{public_id}", response_model=ContractLaborResponse)
def delete_contract_labor(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a contract labor entry by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="contract_labor_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete contract labor")
        )
    
    return result.get("data")


@router.put("/{public_id}/bill", response_model=ContractLaborBillUpdateResponse)
def update_contract_labor_bill(public_id: str, bill_update: ContractLaborBillUpdate):
    """
    Update bill info and line items for a contract labor entry.
    Creates, updates, or deletes line items as needed.
    """
    try:
        service = ContractLaborService()
        line_item_repo = ContractLaborLineItemRepository()
        
        # Get the existing entry
        entry = service.read_by_public_id(public_id=public_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Contract labor entry not found")
        
        # Update the entry's bill fields
        from decimal import Decimal
        import base64
        
        updated = service.repo.update_bill_info(
            id=entry.id,
            row_version=base64.b64decode(bill_update.row_version),
            bill_vendor_id=bill_update.bill_vendor_id,
            bill_date=bill_update.bill_date,
            due_date=bill_update.due_date,
            bill_number=bill_update.bill_number,
            status=bill_update.status,
        )
        
        if not updated:
            raise HTTPException(status_code=409, detail="Concurrent modification detected. Please refresh and try again.")
        
        # Get existing line items
        existing_items = line_item_repo.read_by_contract_labor_id(contract_labor_id=entry.id)
        existing_ids = {item.id for item in existing_items}
        
        # Track line items from the request
        submitted_ids = set()
        items_created = 0
        items_updated = 0
        items_deleted = 0
        
        for item_data in bill_update.line_items:
            if item_data.id:
                # Update existing item
                submitted_ids.add(item_data.id)
                existing_item = next((i for i in existing_items if i.id == item_data.id), None)
                if existing_item:
                    line_item_repo.update_by_id(
                        id=item_data.id,
                        row_version=base64.b64decode(item_data.row_version) if item_data.row_version else existing_item.row_version_bytes,
                        line_date=item_data.line_date,
                        project_id=item_data.project_id if not item_data.is_overhead else None,
                        sub_cost_code_id=item_data.sub_cost_code_id,
                        description=item_data.description,
                        hours=item_data.hours,
                        rate=item_data.rate,
                        markup=item_data.markup,
                        price=item_data.price,
                        is_billable=item_data.is_billable,
                        is_overhead=item_data.is_overhead,
                        bill_line_item_id=existing_item.bill_line_item_id,
                    )
                    items_updated += 1
            else:
                # Create new item
                line_item_repo.create(
                    contract_labor_id=entry.id,
                    line_date=item_data.line_date,
                    project_id=item_data.project_id if not item_data.is_overhead else None,
                    sub_cost_code_id=item_data.sub_cost_code_id,
                    description=item_data.description,
                    hours=item_data.hours,
                    rate=item_data.rate,
                    markup=item_data.markup,
                    price=item_data.price,
                    is_billable=item_data.is_billable,
                    is_overhead=item_data.is_overhead,
                )
                items_created += 1
        
        # Delete items that were removed
        for item_id in existing_ids - submitted_ids:
            line_item_repo.delete_by_id(id=item_id)
            items_deleted += 1
        
        return ContractLaborBillUpdateResponse(
            public_id=updated.public_id,
            row_version=updated.row_version,
            bill_vendor_id=updated.bill_vendor_id,
            bill_date=updated.bill_date,
            due_date=updated.due_date,
            bill_number=updated.bill_number,
            status=updated.status,
            line_items_created=items_created,
            line_items_updated=items_updated,
            line_items_deleted=items_deleted,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating contract labor bill")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{public_id}/mark-ready", response_model=ContractLaborResponse)
def mark_contract_labor_as_ready(public_id: str):
    """
    Mark a contract labor entry as ready for billing.
    Validates that all required fields (vendor, project, subcostcode, rate, hours) are set.
    """
    try:
        service = ContractLaborService()
        result = service.mark_as_ready(public_id=public_id)
        if not result:
            raise HTTPException(status_code=404, detail="Contract labor entry not found")
        return ContractLaborResponse(
            id=result.id,
            public_id=result.public_id,
            row_version=result.row_version,
            created_datetime=result.created_datetime,
            modified_datetime=result.modified_datetime,
            vendor_id=result.vendor_id,
            project_id=result.project_id,
            employee_name=result.employee_name,
            work_date=result.work_date,
            time_in=result.time_in,
            time_out=result.time_out,
            break_time=result.break_time,
            regular_hours=result.regular_hours,
            overtime_hours=result.overtime_hours,
            total_hours=result.total_hours,
            hourly_rate=result.hourly_rate,
            markup=result.markup,
            total_amount=result.total_amount,
            sub_cost_code_id=result.sub_cost_code_id,
            description=result.description,
            billing_period_start=result.billing_period_start,
            status=result.status,
            bill_line_item_id=result.bill_line_item_id,
            import_batch_id=result.import_batch_id,
            source_file=result.source_file,
            source_row=result.source_row,
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Error marking contract labor as ready")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bulk-mark-ready", response_model=ContractLaborBulkMarkReadyResponse)
def bulk_mark_contract_labors_as_ready(request: ContractLaborBulkMarkReady):
    """
    Mark multiple contract labor entries as ready for billing.
    """
    try:
        service = ContractLaborService()
        result = service.bulk_mark_as_ready(public_ids=request.public_ids)
        return ContractLaborBulkMarkReadyResponse(
            success_count=result["success_count"],
            error_count=result["error_count"],
            errors=result["errors"],
        )
    except Exception as e:
        logger.exception("Error bulk marking contract labors as ready")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bulk-delete", response_model=ContractLaborBulkDeleteResponse)
def bulk_delete_contract_labors(request: ContractLaborBulkDelete):
    """
    Delete multiple contract labor entries.
    Only entries with status 'pending_review' can be deleted.
    """
    try:
        service = ContractLaborService()
        result = service.bulk_delete(public_ids=request.public_ids)
        return ContractLaborBulkDeleteResponse(
            success_count=result["success_count"],
            error_count=result["error_count"],
            errors=result["errors"],
        )
    except Exception as e:
        logger.exception("Error bulk deleting contract labors")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import", response_model=ContractLaborImportResponse)
async def import_contract_labor_excel(
    file: UploadFile = File(...),
    import_batch_id: Optional[str] = Form(None),
    default_hourly_rate: Optional[str] = Form(None),
    default_markup: Optional[str] = Form(None),
    carry_forward_rates: bool = Form(True),
):
    """
    Import contract labor entries from an Excel file.
    
    The Excel file should have the following columns:
    - A: Date (e.g., "Tuesday, January 20, 2026")
    - B: Job (Project name/abbreviation)
    - C: Name (Vendor/contractor name)
    - D: Time In
    - E: Time Out
    - F: Break Time
    - G: Regular Time
    - H: OT (Overtime)
    - I: Total Work Time
    - J: Notes
    
    Args:
        file: Excel file (.xlsx)
        import_batch_id: Optional batch ID for grouping (auto-generated if not provided)
        default_hourly_rate: Default hourly rate to apply to all entries
        default_markup: Default markup to apply (e.g., "0.10" for 10%)
        carry_forward_rates: Whether to use previous rates for returning vendors
    """
    try:
        # Validate file type
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            raise HTTPException(status_code=400, detail="File must be an Excel file (.xlsx or .xls)")
        
        # Read file content
        file_content = await file.read()
        
        # Parse optional decimal values
        hourly_rate = None
        if default_hourly_rate:
            try:
                hourly_rate = Decimal(default_hourly_rate)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid hourly rate format")
        
        markup = None
        if default_markup:
            try:
                markup = Decimal(default_markup)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid markup format")
        
        # Import the file
        import_service = ContractLaborImportService()
        result = import_service.import_excel(
            file_content=file_content,
            filename=file.filename,
            import_batch_id=import_batch_id,
            default_hourly_rate=hourly_rate,
            default_markup=markup,
            carry_forward_rates=carry_forward_rates,
        )
        
        return ContractLaborImportResponse(
            import_batch_id=result["import_batch_id"],
            total_rows=result["total_rows"],
            imported_count=result["imported_count"],
            skipped_count=result["skipped_count"],
            error_count=result["error_count"],
            errors=result["errors"],
            unmatched_vendors=result["unmatched_vendors"],
            unmatched_projects=result["unmatched_projects"],
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error importing contract labor Excel file")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import/preview")
async def preview_contract_labor_import(
    file: UploadFile = File(...),
    max_rows: int = Query(default=10, ge=1, le=50),
):
    """
    Preview what would be imported from an Excel file without actually importing.
    Useful for validation before committing the import.
    
    Returns:
    - Total row count
    - Sample of rows with matched/unmatched indicators
    - Lists of matched and unmatched vendors/projects
    """
    try:
        # Validate file type
        if not file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            raise HTTPException(status_code=400, detail="File must be an Excel file (.xlsx or .xls)")
        
        # Read file content
        file_content = await file.read()
        
        # Get preview
        import_service = ContractLaborImportService()
        preview = import_service.get_import_preview(
            file_content=file_content,
            filename=file.filename,
            max_rows=max_rows,
        )
        
        return preview
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error previewing contract labor import")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# PDF Generation Endpoints
# ============================================================================

@router.post("/billing/regenerate-pdf")
async def regenerate_pdf_for_entries(body: ContractLaborBulkDelete):
    """
    Regenerate invoice PDF for selected billed ContractLabor entries.
    Returns the PDF directly for viewing in a new browser tab.
    Accepts {"public_ids": ["...", "..."]}
    """
    from fastapi.responses import Response
    try:
        from entities.contract_labor.business.bill_service import ContractLaborBillService
        bill_service = ContractLaborBillService()
        result = bill_service.regenerate_pdf_for_entries(public_ids=body.public_ids)

        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        return Response(
            content=result["pdf_bytes"],
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename=\"{result['filename']}\""
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error regenerating PDF for entries")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/billing/generate-pdfs/{bill_public_id}")
async def generate_pdfs_for_bill(bill_public_id: str):
    """
    Generate PDF attachments for all line items in a specific bill.
    
    Each PDF contains the time log entries for that line item.
    PDFs are uploaded to Azure Blob Storage and linked to BillLineItems.
    
    Filename pattern:
    {Project.Abbreviation} - {Vendor.Name} - {Bill.Date} - {Description} - {SubCostCode} - {Amount} - {Date}.pdf
    """
    try:
        from entities.contract_labor.business.pdf_service import ContractLaborPDFService
        pdf_service = ContractLaborPDFService()
        result = pdf_service.generate_pdfs_for_bill(bill_public_id=bill_public_id)
        return result
    except Exception as e:
        logger.exception("Error generating PDFs for bill")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/billing/generate-all-pdfs")
async def generate_all_pdfs(
    vendor_id: Optional[int] = Query(default=None, description="Filter by vendor ID"),
    billing_period_start: Optional[str] = Query(default=None, description="Filter by billing period (YYYY-MM-DD)"),
):
    """
    Generate PDF attachments for all billed contract labor entries.
    
    Finds all billed entries, groups by bill, and generates PDFs for each.
    """
    try:
        from entities.contract_labor.business.pdf_service import ContractLaborPDFService
        pdf_service = ContractLaborPDFService()
        result = pdf_service.generate_pdfs_for_billed_entries(
            vendor_id=vendor_id,
            billing_period_start=billing_period_start,
        )
        return result
    except Exception as e:
        logger.exception("Error generating PDFs for billed entries")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-bills/{vendor_id}")
def generate_bills_for_vendor(vendor_id: int, billing_period: Optional[str] = None):
    """
    Generate bills for ready entries for a vendor within a billing period.
    Groups by project and creates one bill per project.
    Also generates invoice PDFs and uploads to Azure Blob Storage.
    """
    try:
        from entities.contract_labor.business.bill_service import ContractLaborBillService
        bill_service = ContractLaborBillService()
        result = bill_service.generate_bills_for_vendor(vendor_id=vendor_id, billing_period_start=billing_period)
        return result
    except Exception as e:
        logger.exception("Error generating bills for vendor")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/preview-pdf/{vendor_id}")
def preview_pdf_for_vendor(vendor_id: int, project_id: Optional[int] = None, billing_period: Optional[str] = None):
    """
    Preview PDF for a vendor (returns PDF directly for download).
    If project_id is provided, generates PDF for that specific project only.
    """
    from fastapi.responses import Response
    try:
        from entities.contract_labor.business.bill_service import ContractLaborBillService
        bill_service = ContractLaborBillService()
        result = bill_service.preview_pdf_for_vendor(vendor_id=vendor_id, project_id=project_id, billing_period_start=billing_period)
        
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        
        return Response(
            content=result["pdf_bytes"],
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"inline; filename=\"{result['filename']}\""
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error previewing PDF for vendor")
        raise HTTPException(status_code=500, detail=str(e))
