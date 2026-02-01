# Python Standard Library Imports
from decimal import Decimal
from typing import Optional
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from services.bill_credit.business.service import BillCreditService
from services.vendor.business.service import VendorService
from services.bill_credit_line_item.business.service import BillCreditLineItemService
from services.bill_credit_line_item_attachment.business.service import BillCreditLineItemAttachmentService
from services.attachment.business.service import AttachmentService
from services.sub_cost_code.business.service import SubCostCodeService
from services.project.business.service import ProjectService
from services.auth.business.service import get_current_user_web

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bill-credit", tags=["web", "bill_credit"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_bill_credits(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    page: int = 1,
    page_size: int = 50,
    search: Optional[str] = None,
    vendor_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_draft: Optional[str] = "true",  # Default to showing only draft bill credits
    sort_by: str = "CreditDate",
    sort_direction: str = "DESC",
):
    """
    List bill credits with pagination, search, and filtering.
    Defaults to showing only draft bill credits.
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
    
    # Parse is_draft filter (default to True for drafts only)
    # When searching, automatically search across all bill credits
    is_draft_filter = None
    if search and search.strip():
        # Search across all bill credits regardless of status filter
        is_draft_filter = None
    elif is_draft is not None and is_draft.strip():
        if is_draft.lower() in ('true', '1', 'yes'):
            is_draft_filter = True
        elif is_draft.lower() in ('false', '0', 'no'):
            is_draft_filter = False
        elif is_draft.lower() == 'all':
            is_draft_filter = None  # Show all bill credits
    
    # Get bill credits with pagination
    bill_credits = BillCreditService().read_paginated(
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
    total_count = BillCreditService().count(
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
    
    # Add vendor names to bill credits
    bill_credits_with_vendors = []
    for bill_credit in bill_credits:
        bill_credit_dict = bill_credit.to_dict()
        if bill_credit.vendor_id and bill_credit.vendor_id in vendor_map:
            bill_credit_dict['vendor_name'] = vendor_map[bill_credit.vendor_id]
        bill_credits_with_vendors.append(bill_credit_dict)
    
    return templates.TemplateResponse(
        "bill_credit/list.html",
        {
            "request": request,
            "bill_credits": bill_credits_with_vendors,
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
async def create_bill_credit(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create bill credit form.
    """
    vendors = VendorService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()
    return templates.TemplateResponse(
        "bill_credit/create.html",
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
async def view_bill_credit(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a bill credit.
    """
    bill_credit = BillCreditService().read_by_public_id(public_id=public_id)
    vendors = VendorService().read_all()
    projects = ProjectService().read_all()
    
    # Find the vendor name if bill credit has a vendor_id
    vendor_name = None
    if bill_credit and bill_credit.vendor_id:
        for vendor in vendors:
            if vendor.id == bill_credit.vendor_id:
                vendor_name = vendor.name
                break
    
    # Fetch line items associated with this bill credit
    line_items = []
    line_items_with_attachments = []
    if bill_credit and bill_credit.id:
        line_items = BillCreditLineItemService().read_by_bill_credit_id(bill_credit_id=bill_credit.id)
        
        # Fetch attachments for each line item
        bill_credit_line_item_attachment_service = BillCreditLineItemAttachmentService()
        attachment_service = AttachmentService()
        
        # Pre-fetch all sub_cost_codes to avoid N+1 queries
        sub_cost_codes = SubCostCodeService().read_all()
        sub_cost_code_map = {scc.id: scc for scc in sub_cost_codes}
        
        # Pre-fetch all attachments for these line items in one query
        line_item_public_ids = [li.public_id for li in line_items if li.public_id]
        attachment_links_map = {}
        attachments_map = {}
        if line_item_public_ids:
            # Get all attachment links for these line items
            all_links = bill_credit_line_item_attachment_service.read_by_bill_credit_line_item_ids(line_item_public_ids)
            attachment_links_map = {link.bill_credit_line_item_public_id: link for link in all_links}
            
            # Get all attachments in one query
            attachment_ids = [link.attachment_id for link in all_links if link.attachment_id]
            if attachment_ids:
                all_attachments = attachment_service.read_by_ids(attachment_ids)
                attachments_map = {att.id: att for att in all_attachments}
        
        for line_item in line_items:
            line_item_dict = line_item.to_dict()
            
            # Get project name if line item has a project_id
            if line_item.project_id:
                for project in projects:
                    if project.id == line_item.project_id:
                        line_item_dict['project_name'] = project.name
                        break
            
            # Get sub_cost_code details from pre-fetched map
            if line_item.sub_cost_code_id:
                sub_cost_code = sub_cost_code_map.get(line_item.sub_cost_code_id)
                if sub_cost_code:
                    line_item_dict['sub_cost_code_number'] = sub_cost_code.number
                    line_item_dict['sub_cost_code_name'] = sub_cost_code.name
            
            # Get attachment for this line item from pre-fetched maps
            if line_item.public_id:
                attachment_link = attachment_links_map.get(line_item.public_id)
                
                if attachment_link and attachment_link.attachment_id:
                    # Get attachment from pre-fetched map
                    attachment = attachments_map.get(attachment_link.attachment_id)
                    if attachment:
                        line_item_dict['attachment'] = attachment.to_dict()
            
            line_items_with_attachments.append(line_item_dict)
    
    bill_credit_dict = bill_credit.to_dict()
    if vendor_name:
        bill_credit_dict['vendor_name'] = vendor_name
    
    return templates.TemplateResponse(
        "bill_credit/view.html",
        {
            "request": request,
            "bill_credit": bill_credit_dict,
            "line_items": line_items_with_attachments,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_bill_credit(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a bill credit.
    """
    bill_credit = BillCreditService().read_by_public_id(public_id=public_id)
    vendors = VendorService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()
    
    # Find the vendor public_id if bill credit has a vendor_id
    vendor_public_id = None
    if bill_credit and bill_credit.vendor_id:
        for vendor in vendors:
            if vendor.id == bill_credit.vendor_id:
                vendor_public_id = vendor.public_id
                break
    
    # Fetch line items associated with this bill credit
    line_items = []
    line_items_with_attachments = []
    if bill_credit and bill_credit.id:
        line_items = BillCreditLineItemService().read_by_bill_credit_id(bill_credit_id=bill_credit.id)
        
        # Fetch attachments for all line items in batch (avoid N+1 queries)
        bill_credit_line_item_attachment_service = BillCreditLineItemAttachmentService()
        attachment_service = AttachmentService()
        
        # Pre-fetch all attachments for these line items in one query
        line_item_public_ids = [li.public_id for li in line_items if li.public_id]
        attachment_links_map = {}
        attachments_map = {}
        if line_item_public_ids:
            all_links = bill_credit_line_item_attachment_service.read_by_bill_credit_line_item_ids(line_item_public_ids)
            attachment_links_map = {link.bill_credit_line_item_public_id: link for link in all_links}
            
            attachment_ids = [link.attachment_id for link in all_links if link.attachment_id]
            if attachment_ids:
                all_attachments = attachment_service.read_by_ids(attachment_ids)
                attachments_map = {att.id: att for att in all_attachments}
        
        for line_item in line_items:
            line_item_dict = line_item.to_dict()
            
            # Convert Decimal values to floats for JSON serialization
            for key, value in line_item_dict.items():
                if isinstance(value, Decimal):
                    line_item_dict[key] = float(value)
            
            # Get attachment for this line item from pre-fetched maps
            if line_item.public_id:
                attachment_link = attachment_links_map.get(line_item.public_id)
                
                if attachment_link and attachment_link.attachment_id:
                    attachment = attachments_map.get(attachment_link.attachment_id)
                    if attachment:
                        attachment_dict = attachment.to_dict()
                        # Convert Decimal values in attachment if any
                        for key, value in attachment_dict.items():
                            if isinstance(value, Decimal):
                                attachment_dict[key] = float(value)
                        line_item_dict['attachment'] = attachment_dict
                        line_item_dict['attachment_link'] = attachment_link.to_dict()
            
            line_items_with_attachments.append(line_item_dict)
    
    bill_credit_dict = bill_credit.to_dict()
    
    # Convert Decimal values to floats for JSON serialization
    for key, value in bill_credit_dict.items():
        if isinstance(value, Decimal):
            bill_credit_dict[key] = float(value)
    
    if vendor_public_id:
        bill_credit_dict['vendor_public_id'] = vendor_public_id
    
    return templates.TemplateResponse(
        "bill_credit/edit.html",
        {
            "request": request,
            "bill_credit": bill_credit_dict,
            "vendors": vendors,
            "line_items": line_items_with_attachments,
            "sub_cost_codes": sub_cost_codes,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
