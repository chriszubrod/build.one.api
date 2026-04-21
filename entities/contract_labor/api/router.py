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
    ContractLaborBulkMarkReady,
    ContractLaborBulkDelete,
    ContractLaborBillUpdate,
)
from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/contract-labor",
    tags=["api", "Contract Labor"],
)


@router.post("", status_code=201)
def create_contract_labor(contract_labor: ContractLaborCreate, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_create"))):
    """
    Create a new contract labor entry.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create contract labor")

    return item_response(result.get("data"))


@router.get("")
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
    current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR)),
):
    """
    Read contract labor entries with pagination and filtering.
    """
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
    return list_response([r.to_dict() for r in results])


@router.get("/count")
def count_contract_labors(
    search_term: Optional[str] = Query(default=None),
    vendor_id: Optional[int] = Query(default=None),
    project_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    billing_period_start: Optional[str] = Query(default=None),
    start_date: Optional[str] = Query(default=None),
    end_date: Optional[str] = Query(default=None),
    current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR)),
):
    """
    Count contract labor entries matching the filter criteria.
    """
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
    return item_response({"count": count})


@router.get("/by-status/{status}")
def read_contract_labors_by_status(status: str, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR))):
    """
    Read all contract labor entries with a specific status.
    """
    service = ContractLaborService()
    results = service.read_by_status(status=status)
    return list_response([r.to_dict() for r in results])


@router.get("/by-billing-period/{billing_period_start}")
def read_contract_labors_by_billing_period(billing_period_start: str, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR))):
    """
    Read all contract labor entries for a specific billing period.
    """
    service = ContractLaborService()
    results = service.read_by_billing_period(billing_period_start=billing_period_start)
    return list_response([r.to_dict() for r in results])


@router.get("/by-import-batch/{import_batch_id}")
def read_contract_labors_by_import_batch(import_batch_id: str, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR))):
    """
    Read all contract labor entries from a specific import batch.
    """
    service = ContractLaborService()
    results = service.read_by_import_batch_id(import_batch_id=import_batch_id)
    return list_response([r.to_dict() for r in results])


@router.get("/last-rate/{vendor_public_id}")
def get_last_rate_for_vendor(vendor_public_id: str, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR))):
    """
    Get the last used hourly rate and markup for a vendor (for carry-forward).
    """
    service = ContractLaborService()
    hourly_rate, markup = service.get_last_rate_for_vendor(vendor_public_id=vendor_public_id)
    return item_response({
        "vendor_public_id": vendor_public_id,
        "hourly_rate": str(hourly_rate) if hourly_rate is not None else None,
        "markup": str(markup) if markup is not None else None,
    })


@router.get("/{public_id}")
def read_contract_labor_by_public_id(public_id: str, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR))):
    """
    Read a contract labor entry by public ID.
    """
    service = ContractLaborService()
    result = service.read_by_public_id(public_id=public_id)
    if not result:
        raise_not_found("Contract labor entry")
    return item_response(result.to_dict())


@router.put("/{public_id}")
def update_contract_labor(public_id: str, contract_labor: ContractLaborUpdate, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_update"))):
    """
    Update a contract labor entry by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "contract_labor": contract_labor,
        },
        workflow_type="contract_labor_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update contract labor")

    return item_response(result.get("data"))


@router.delete("/{public_id}")
def delete_contract_labor(public_id: str, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_delete"))):
    """
    Delete a contract labor entry by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="contract_labor_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete contract labor")

    return item_response(result.get("data"))


@router.put("/{public_id}/bill")
def update_contract_labor_bill(public_id: str, bill_update: ContractLaborBillUpdate, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_update"))):
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
            raise_not_found("Contract labor entry")

        # Update the entry's bill fields
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

        return item_response({
            "public_id": updated.public_id,
            "row_version": updated.row_version,
            "bill_vendor_id": updated.bill_vendor_id,
            "bill_date": updated.bill_date,
            "due_date": updated.due_date,
            "bill_number": updated.bill_number,
            "status": updated.status,
            "line_items_created": items_created,
            "line_items_updated": items_updated,
            "line_items_deleted": items_deleted,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error updating contract labor bill")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{public_id}/mark-ready")
def mark_contract_labor_as_ready(public_id: str, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_update"))):
    """
    Mark a contract labor entry as ready for billing.
    Validates that all required fields (vendor, project, subcostcode, rate, hours) are set.
    """
    try:
        service = ContractLaborService()
        result = service.mark_as_ready(public_id=public_id)
        if not result:
            raise_not_found("Contract labor entry")
        return item_response(result.to_dict())
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Error marking contract labor as ready")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bulk-mark-ready")
def bulk_mark_contract_labors_as_ready(request: ContractLaborBulkMarkReady, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_update"))):
    """
    Mark multiple contract labor entries as ready for billing.
    """
    service = ContractLaborService()
    result = service.bulk_mark_as_ready(public_ids=request.public_ids)
    return item_response({
        "success_count": result["success_count"],
        "error_count": result["error_count"],
        "errors": result["errors"],
    })


@router.post("/bulk-delete")
def bulk_delete_contract_labors(request: ContractLaborBulkDelete, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_delete"))):
    """
    Delete multiple contract labor entries.
    Only entries with status 'pending_review' can be deleted.
    """
    service = ContractLaborService()
    result = service.bulk_delete(public_ids=request.public_ids)
    return item_response({
        "success_count": result["success_count"],
        "error_count": result["error_count"],
        "errors": result["errors"],
    })


@router.post("/import")
async def import_contract_labor_excel(
    file: UploadFile = File(...),
    import_batch_id: Optional[str] = Form(None),
    default_hourly_rate: Optional[str] = Form(None),
    default_markup: Optional[str] = Form(None),
    carry_forward_rates: bool = Form(True),
    current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_create")),
):
    """
    Import contract labor entries from an Excel file.
    """
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

    return item_response({
        "import_batch_id": result["import_batch_id"],
        "total_rows": result["total_rows"],
        "imported_count": result["imported_count"],
        "skipped_count": result["skipped_count"],
        "error_count": result["error_count"],
        "errors": result["errors"],
        "unmatched_vendors": result["unmatched_vendors"],
        "unmatched_projects": result["unmatched_projects"],
    })


@router.post("/import/preview")
async def preview_contract_labor_import(
    file: UploadFile = File(...),
    max_rows: int = Query(default=10, ge=1, le=50),
    current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_create")),
):
    """
    Preview what would be imported from an Excel file without actually importing.
    """
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

    return item_response(preview)


# ============================================================================
# PDF Generation Endpoints
# ============================================================================

@router.post("/billing/regenerate-pdf")
async def regenerate_pdf_for_entries(body: ContractLaborBulkDelete, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_create"))):
    """
    Regenerate invoice PDF for selected billed ContractLabor entries.
    Returns the PDF directly for viewing in a new browser tab.
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
async def generate_pdfs_for_bill(bill_public_id: str, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_create"))):
    """
    Generate PDF attachments for all line items in a specific bill.
    """
    try:
        from entities.contract_labor.business.pdf_service import ContractLaborPDFService
        pdf_service = ContractLaborPDFService()
        result = pdf_service.generate_pdfs_for_bill(bill_public_id=bill_public_id)
        return item_response(result)
    except Exception as e:
        logger.exception("Error generating PDFs for bill")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/billing/generate-all-pdfs")
async def generate_all_pdfs(
    vendor_id: Optional[int] = Query(default=None, description="Filter by vendor ID"),
    billing_period_start: Optional[str] = Query(default=None, description="Filter by billing period (YYYY-MM-DD)"),
    current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_create")),
):
    """
    Generate PDF attachments for all billed contract labor entries.
    """
    try:
        from entities.contract_labor.business.pdf_service import ContractLaborPDFService
        pdf_service = ContractLaborPDFService()
        result = pdf_service.generate_pdfs_for_billed_entries(
            vendor_id=vendor_id,
            billing_period_start=billing_period_start,
        )
        return item_response(result)
    except Exception as e:
        logger.exception("Error generating PDFs for billed entries")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-bills/{vendor_id}")
def generate_bills_for_vendor(vendor_id: int, billing_period: Optional[str] = None, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR, "can_create"))):
    """
    Generate bills for ready entries for a vendor within a billing period.
    """
    try:
        from entities.contract_labor.business.bill_service import ContractLaborBillService
        bill_service = ContractLaborBillService()
        result = bill_service.generate_bills_for_vendor(vendor_id=vendor_id, billing_period_start=billing_period)
        return item_response(result)
    except Exception as e:
        logger.exception("Error generating bills for vendor")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/preview-pdf/{vendor_id}")
def preview_pdf_for_vendor(vendor_id: int, project_id: Optional[int] = None, billing_period: Optional[str] = None, current_user: dict = Depends(require_module_api(Modules.CONTRACT_LABOR))):
    """
    Preview PDF for a vendor (returns PDF directly for download).
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
