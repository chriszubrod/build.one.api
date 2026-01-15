# Python Standard Library Imports
from decimal import Decimal
from typing import Optional
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.bill.business.service import BillService
from modules.vendor.business.service import VendorService
from modules.bill_line_item.business.service import BillLineItemService
from modules.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from modules.attachment.business.service import AttachmentService
from modules.sub_cost_code.business.service import SubCostCodeService
from modules.project.business.service import ProjectService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/bill", tags=["web", "bill"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_bills(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    page: int = 1,
    page_size: int = 50,
    search: Optional[str] = None,
    vendor_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_draft: Optional[str] = None,
    sort_by: str = "BillDate",
    sort_direction: str = "DESC",
):
    """
    List bills with pagination, search, and filtering.
    """
    # Validate page number
    if page < 1:
        page = 1
    
    # Validate page size (limit to reasonable range)
    if page_size < 10:
        page_size = 10
    elif page_size > 100:
        page_size = 100
    
    # Parse vendor_id - handle empty strings
    vendor_id_int = None
    if vendor_id and vendor_id.strip():
        try:
            vendor_id_int = int(vendor_id)
        except (ValueError, TypeError):
            vendor_id_int = None
    
    # Parse is_draft filter
    is_draft_filter = None
    if is_draft is not None:
        is_draft_filter = is_draft.lower() in ('true', '1', 'yes')
    
    # Get bills with pagination
    bills = BillService().read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search,
        vendor_id=vendor_id_int,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft_filter,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )
    
    # Get total count for pagination
    total_count = BillService().count(
        search_term=search,
        vendor_id=vendor_id_int,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft_filter,
    )
    
    # Calculate pagination info
    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    has_previous = page > 1
    has_next = page < total_pages
    
    # Get vendors for filter dropdown
    vendors = VendorService().read_all()
    
    # Create a mapping of vendor_id to vendor_name
    vendor_map = {vendor.id: vendor.name for vendor in vendors}
    
    # Add vendor names to bills
    bills_with_vendors = []
    for bill in bills:
        bill_dict = bill.to_dict()
        if bill.vendor_id and bill.vendor_id in vendor_map:
            bill_dict['vendor_name'] = vendor_map[bill.vendor_id]
        bills_with_vendors.append(bill_dict)
    
    return templates.TemplateResponse(
        "bill/list.html",
        {
            "request": request,
            "bills": bills_with_vendors,
            "vendors": vendors,
            "current_user": current_user,
            "current_path": request.url.path,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_previous": has_previous,
            "has_next": has_next,
            "search": search or "",
            "vendor_id": vendor_id_int if vendor_id_int else None,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "is_draft": is_draft or "",
            "sort_by": sort_by,
            "sort_direction": sort_direction,
        },
    )


@router.get("/create")
async def create_bill(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create bill form.
    """
    vendors = VendorService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()
    return templates.TemplateResponse(
        "bill/create.html",
        {
            "request": request,
            "vendors": vendors,
            "sub_cost_codes": sub_cost_codes,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_bill(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a bill.
    """
    bill = BillService().read_by_public_id(public_id=public_id)
    vendors = VendorService().read_all()
    projects = ProjectService().read_all()
    
    # Find the vendor name if bill has a vendor_id
    vendor_name = None
    if bill and bill.vendor_id:
        for vendor in vendors:
            if vendor.id == bill.vendor_id:
                vendor_name = vendor.name
                break
    
    # Fetch line items associated with this bill
    line_items = []
    line_items_with_attachments = []
    if bill and bill.id:
        line_items = BillLineItemService().read_by_bill_id(bill_id=bill.id)
        
        # Fetch attachments for each line item
        bill_line_item_attachment_service = BillLineItemAttachmentService()
        attachment_service = AttachmentService()
        
        for line_item in line_items:
            line_item_dict = line_item.to_dict()
            
            # Get project name if line item has a project_id
            if line_item.project_id:
                for project in projects:
                    if project.id == line_item.project_id:
                        line_item_dict['project_name'] = project.name
                        break
            
            # Get attachment for this line item (1-1 relationship)
            if line_item.public_id:
                attachment_link = bill_line_item_attachment_service.read_by_bill_line_item_id(
                    bill_line_item_public_id=line_item.public_id
                )
                
                if attachment_link and attachment_link.attachment_id:
                    # Fetch the actual attachment
                    attachment = attachment_service.read_by_id(id=attachment_link.attachment_id)
                    if attachment:
                        line_item_dict['attachment'] = attachment.to_dict()
            
            line_items_with_attachments.append(line_item_dict)
    
    bill_dict = bill.to_dict()
    if vendor_name:
        bill_dict['vendor_name'] = vendor_name
    
    return templates.TemplateResponse(
        "bill/view.html",
        {
            "request": request,
            "bill": bill_dict,
            "line_items": line_items_with_attachments,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_bill(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a bill.
    """
    bill = BillService().read_by_public_id(public_id=public_id)
    vendors = VendorService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()
    
    # Find the vendor public_id if bill has a vendor_id
    vendor_public_id = None
    if bill and bill.vendor_id:
        for vendor in vendors:
            if vendor.id == bill.vendor_id:
                vendor_public_id = vendor.public_id
                break
    
    # Fetch line items associated with this bill
    line_items = []
    line_items_with_attachments = []
    if bill and bill.id:
        line_items = BillLineItemService().read_by_bill_id(bill_id=bill.id)
        
        # Fetch attachments for each line item
        bill_line_item_attachment_service = BillLineItemAttachmentService()
        attachment_service = AttachmentService()
        
        for line_item in line_items:
            line_item_dict = line_item.to_dict()
            
            # Convert Decimal values to floats for JSON serialization
            for key, value in line_item_dict.items():
                if isinstance(value, Decimal):
                    line_item_dict[key] = float(value)
            
            # Get attachment for this line item (1-1 relationship)
            if line_item.public_id:
                attachment_link = bill_line_item_attachment_service.read_by_bill_line_item_id(
                    bill_line_item_public_id=line_item.public_id
                )
                
                if attachment_link and attachment_link.attachment_id:
                    # Fetch the actual attachment
                    attachment = attachment_service.read_by_id(id=attachment_link.attachment_id)
                    if attachment:
                        attachment_dict = attachment.to_dict()
                        # Convert Decimal values in attachment if any
                        for key, value in attachment_dict.items():
                            if isinstance(value, Decimal):
                                attachment_dict[key] = float(value)
                        line_item_dict['attachment'] = attachment_dict
                        line_item_dict['attachment_link'] = attachment_link.to_dict()
            
            line_items_with_attachments.append(line_item_dict)
    
    bill_dict = bill.to_dict()
    
    # Convert Decimal values to floats for JSON serialization
    for key, value in bill_dict.items():
        if isinstance(value, Decimal):
            bill_dict[key] = float(value)
    
    if vendor_public_id:
        bill_dict['vendor_public_id'] = vendor_public_id
    
    return templates.TemplateResponse(
        "bill/edit.html",
        {
            "request": request,
            "bill": bill_dict,
            "vendors": vendors,
            "line_items": line_items_with_attachments,
            "sub_cost_codes": sub_cost_codes,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
