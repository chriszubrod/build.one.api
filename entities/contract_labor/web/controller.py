# Python Standard Library Imports
from decimal import Decimal
from typing import Optional
import logging

# Third-party Imports
from fastapi import APIRouter, Request, Depends, UploadFile, File
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

# Local Imports
from entities.contract_labor.business.model import ContractLabor
from entities.contract_labor.business.service import ContractLaborService
from entities.contract_labor.business.import_service import ContractLaborImportService
from entities.contract_labor.business.bill_service import ContractLaborBillService
from entities.contract_labor.persistence.repo import ContractLaborRepository
from entities.contract_labor.persistence.line_item_repo import ContractLaborLineItemRepository
from entities.vendor.business.service import VendorService
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.auth.business.service import get_current_user_web

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contract-labor", tags=["web", "contract_labor"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_contract_labor(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    page: int = 1,
    page_size: int = 50,
    search: Optional[str] = None,
    vendor_id: Optional[str] = None,
    project_id: Optional[str] = None,
    status: Optional[str] = None,
    billing_period: Optional[str] = None,
    sort_by: str = "WorkDate",
    sort_direction: str = "DESC",
):
    """
    List contract labor entries with pagination, search, and filtering.
    """
    # Validate page number
    if page < 1:
        page = 1
    
    # Validate page size
    if page_size < 10:
        page_size = 10
    elif page_size > 100:
        page_size = 100
    
    # Parse vendor_id
    vendor_id_int = None
    if vendor_id and vendor_id.strip():
        try:
            vendor_id_int = int(vendor_id)
        except (ValueError, TypeError):
            vendor_id_int = None
    
    # Parse project_id
    project_id_int = None
    if project_id and project_id.strip():
        try:
            project_id_int = int(project_id)
        except (ValueError, TypeError):
            project_id_int = None
    
    # Get entries with pagination
    service = ContractLaborService()
    entries = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search,
        vendor_id=vendor_id_int,
        project_id=project_id_int,
        status=status if status and status != "all" else None,
        billing_period_start=billing_period if billing_period else None,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    
    # Get total count
    total_count = service.count(
        search_term=search,
        vendor_id=vendor_id_int,
        project_id=project_id_int,
        status=status if status and status != "all" else None,
        billing_period_start=billing_period if billing_period else None,
    )
    
    # Calculate pagination
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    has_previous = page > 1
    has_next = page < total_pages
    
    # Get lookups
    vendors = VendorService().read_all()
    projects = ProjectService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    
    # Create mappings
    vendor_map = {v.id: v for v in vendors}
    project_map = {p.id: p for p in projects}
    sub_cost_code_map = {s.id: s for s in sub_cost_codes}
    
    # Enrich entries
    entries_enriched = []
    for entry in entries:
        entry_dict = entry.to_dict()
        # Convert Decimals to float for template
        for key, value in entry_dict.items():
            if isinstance(value, Decimal):
                entry_dict[key] = float(value)
        
        if entry.vendor_id and entry.vendor_id in vendor_map:
            entry_dict['vendor_name'] = vendor_map[entry.vendor_id].name
        if entry.project_id and entry.project_id in project_map:
            entry_dict['project_name'] = project_map[entry.project_id].name
            entry_dict['project_abbreviation'] = project_map[entry.project_id].abbreviation
        if entry.sub_cost_code_id and entry.sub_cost_code_id in sub_cost_code_map:
            entry_dict['sub_cost_code_number'] = sub_cost_code_map[entry.sub_cost_code_id].number
        entries_enriched.append(entry_dict)
    
    # Get unique billing periods for filter dropdown
    all_entries = service.read_all()
    billing_periods = sorted(set(
        e.billing_period_start for e in all_entries if e.billing_period_start
    ), reverse=True)
    
    return templates.TemplateResponse(
        "contract_labor/list.html",
        {
            "request": request,
            "entries": entries_enriched,
            "vendors": vendors,
            "projects": projects,
            "sub_cost_codes": sub_cost_codes,
            "billing_periods": billing_periods,
            "current_user": current_user,
            "current_path": request.url.path,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_previous": has_previous,
            "has_next": has_next,
            "search": search or "",
            "vendor_id": vendor_id_int,
            "project_id": project_id_int,
            "status": status or "",
            "billing_period": billing_period or "",
            "sort_by": sort_by,
            "sort_direction": sort_direction,
        },
    )


@router.get("/import")
async def import_page(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
):
    """
    Render the Excel import page.
    """
    return templates.TemplateResponse(
        "contract_labor/import.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.post("/import")
async def import_excel(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user_web),
):
    """
    Handle Excel file upload and import.
    Rates and markup are set per-vendor during the review step.
    """
    try:
        # Validate file type
        if not file.filename or not file.filename.endswith(('.xlsx', '.xls')):
            return templates.TemplateResponse(
                "contract_labor/import.html",
                {
                    "request": request,
                    "current_user": current_user,
                    "current_path": request.url.path,
                    "error": "Please upload an Excel file (.xlsx or .xls)",
                },
            )
        
        # Read file content
        file_content = await file.read()
        
        # Import - rates will be set per-vendor during review
        import_service = ContractLaborImportService()
        result = import_service.import_excel(
            file_content=file_content,
            filename=file.filename,
            carry_forward_rates=True,  # Always try to carry forward from previous entries
        )
        
        return templates.TemplateResponse(
            "contract_labor/import.html",
            {
                "request": request,
                "current_user": current_user,
                "current_path": request.url.path,
                "result": result,
            },
        )
        
    except Exception as e:
        logger.exception("Error importing Excel file")
        return templates.TemplateResponse(
            "contract_labor/import.html",
            {
                "request": request,
                "current_user": current_user,
                "current_path": request.url.path,
                "error": str(e),
            },
        )


@router.get("/bills")
async def bills_page(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
):
    """
    Bills page - view and generate bills from ready entries.
    """
    service = ContractLaborService()
    line_item_repo = ContractLaborLineItemRepository()
    
    # Get lookups for display
    projects = ProjectService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    project_map = {p.id: p for p in projects}
    scc_map = {s.id: s for s in sub_cost_codes}
    
    # Get all ready entries
    ready_entries = service.read_by_status(status="ready")
    
    # Group by bill_vendor_id (or employee_name if no vendor assigned)
    vendor_groups = {}
    for entry in ready_entries:
        # Use bill_vendor_id if set, otherwise use employee_name as key
        vendor_key = entry.bill_vendor_id or f"employee:{entry.employee_name}"
        
        if vendor_key not in vendor_groups:
            vendor_groups[vendor_key] = {
                "vendor_id": entry.bill_vendor_id,
                "vendor_name": None,  # Will be populated below
                "employee_name": entry.employee_name,
                "line_items": [],  # Collect all line items for display
                "by_date": {},  # Aggregate by date for summary: date -> {billed_hours, cost_before_markup, price_after_markup}
                "total_hours": 0,
                "total_amount": 0,
                "min_date": None,
                "max_date": None,
            }
        
        # Get line items for this entry
        line_items = line_item_repo.read_by_contract_labor_id(contract_labor_id=entry.id)
        
        for li in line_items:
            # Get project and subcostcode display names
            project = project_map.get(li.project_id)
            scc = scc_map.get(li.sub_cost_code_id)
            
            line_item_dict = {
                "date": li.line_date or entry.work_date,
                "sub_cost_code": f"{scc.number}" if scc else "",
                "description": li.description or "",
                "amount": float(li.price or 0),
                "project": f"{project.abbreviation or project.name}" if project else "",
                "entry_public_id": entry.public_id,
            }
            vendor_groups[vendor_key]["line_items"].append(line_item_dict)
            vendor_groups[vendor_key]["total_amount"] += float(li.price or 0)
            
            # Aggregate by date for Line Items Summary
            date = li.line_date or entry.work_date
            if date:
                hours = float(li.hours or 0)
                rate = float(li.rate or 0)
                cost_before_markup = (hours / 8.0) * rate
                price_after_markup = float(li.price or 0)
                if date not in vendor_groups[vendor_key]["by_date"]:
                    vendor_groups[vendor_key]["by_date"][date] = {
                        "billed_hours": 0.0,
                        "cost_before_markup": 0.0,
                        "price_after_markup": 0.0,
                    }
                vendor_groups[vendor_key]["by_date"][date]["billed_hours"] += hours
                vendor_groups[vendor_key]["by_date"][date]["cost_before_markup"] += cost_before_markup
                vendor_groups[vendor_key]["by_date"][date]["price_after_markup"] += price_after_markup
        
        vendor_groups[vendor_key]["total_hours"] += float(entry.total_hours or 0)
        
        # Track date range
        if entry.work_date:
            if not vendor_groups[vendor_key]["min_date"] or entry.work_date < vendor_groups[vendor_key]["min_date"]:
                vendor_groups[vendor_key]["min_date"] = entry.work_date
            if not vendor_groups[vendor_key]["max_date"] or entry.work_date > vendor_groups[vendor_key]["max_date"]:
                vendor_groups[vendor_key]["max_date"] = entry.work_date
    
    # Get vendor names
    vendors = VendorService().read_all()
    vendor_map = {v.id: v.name for v in vendors}
    
    vendors_with_entries = []
    for vendor_key, data in vendor_groups.items():
        if data["vendor_id"]:
            data["vendor_name"] = vendor_map.get(data["vendor_id"], f"Vendor #{data['vendor_id']}")
        else:
            data["vendor_name"] = data["employee_name"] or "Unknown"
        # Build line items summary by day (billed hours, cost before markup, price after markup)
        data["line_items_summary"] = [
            {
                "date": d,
                "billed_hours": round(v["billed_hours"], 2),
                "cost_before_markup": round(v["cost_before_markup"], 2),
                "price_after_markup": round(v["price_after_markup"], 2),
            }
            for d, v in sorted(data["by_date"].items())
        ]
        vendors_with_entries.append(data)
    
    # Sort by vendor name
    vendors_with_entries.sort(key=lambda x: x["vendor_name"] or "")
    
    return templates.TemplateResponse(
        "contract_labor/bills.html",
        {
            "request": request,
            "vendors_with_entries": vendors_with_entries,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_entry(
    request: Request,
    public_id: str,
    current_user: dict = Depends(get_current_user_web),
):
    """
    Edit a contract labor entry.
    """
    service = ContractLaborService()
    entry = service.read_by_public_id(public_id=public_id)
    
    if not entry:
        return RedirectResponse(url="/contract-labor/list", status_code=303)
    
    # Get lookups
    vendors = VendorService().read_all()
    projects = ProjectService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    
    # Find vendor and project info
    vendor = None
    if entry.vendor_id:
        for v in vendors:
            if v.id == entry.vendor_id:
                vendor = v
                break
    
    project = None
    if entry.project_id:
        for p in projects:
            if p.id == entry.project_id:
                project = p
                break
    
    entry_dict = entry.to_dict()
    # Convert Decimals
    for key, value in entry_dict.items():
        if isinstance(value, Decimal):
            entry_dict[key] = float(value)

    # Default Bill Date, Due Date, Bill Number from Work Date (same rules as billing period)
    if entry.work_date and (not entry.bill_date or not entry.due_date or not entry.bill_number):
        billing_period = ContractLabor.calculate_billing_period_start(entry.work_date)
        if billing_period:
            if not entry.bill_date:
                entry_dict["bill_date"] = billing_period
            if not entry.due_date:
                due = ContractLaborBillService().get_due_date(billing_period)
                entry_dict["due_date"] = due
            if not entry.bill_number:
                # Format YYYY.MM.DD (e.g. 2026.02.15)
                entry_dict["bill_number"] = billing_period.replace("-", ".")

    if vendor:
        entry_dict['vendor_name'] = vendor.name
        entry_dict['vendor_public_id'] = vendor.public_id
    if project:
        entry_dict['project_name'] = project.name
        entry_dict['project_public_id'] = project.public_id
    
    # Get line items for this entry
    line_item_repo = ContractLaborLineItemRepository()
    line_items_raw = line_item_repo.read_by_contract_labor_id(contract_labor_id=entry.id)
    line_items = []
    for item in line_items_raw:
        item_dict = item.to_dict()
        # Convert Decimals
        for key, value in item_dict.items():
            if isinstance(value, Decimal):
                item_dict[key] = float(value)
        line_items.append(item_dict)
    
    # Get daily summary for this employee on this date (across all entries)
    daily_summary = {
        "total_imported_hours": 0.0,
        "entry_count": 0,
        "allocated_other_entries": 0.0,
        "allocated_this_entry": 0.0,
        "remaining_to_allocate": 0.0,
    }
    if entry.employee_name and entry.work_date:
        repo = ContractLaborRepository()
        daily_summary = repo.get_daily_summary(
            employee_name=entry.employee_name,
            work_date=entry.work_date,
            exclude_entry_id=entry.id,
        )
    
    return templates.TemplateResponse(
        "contract_labor/edit.html",
        {
            "request": request,
            "entry": entry_dict,
            "line_items": line_items,
            "vendors": vendors,
            "projects": projects,
            "sub_cost_codes": sub_cost_codes,
            "daily_summary": daily_summary,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_entry(
    request: Request,
    public_id: str,
    current_user: dict = Depends(get_current_user_web),
):
    """
    View a contract labor entry (redirects to edit for now).
    """
    return RedirectResponse(url=f"/contract-labor/{public_id}/edit", status_code=303)
