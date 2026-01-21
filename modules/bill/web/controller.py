# Python Standard Library Imports
from decimal import Decimal
from typing import Optional
import re
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
from modules.payment_term.business.service import PaymentTermService
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
    is_draft: Optional[str] = "true",  # Default to showing only draft bills
    sort_by: str = "BillDate",
    sort_direction: str = "DESC",
):
    """
    List bills with pagination, search, and filtering.
    Defaults to showing only draft bills.
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
    # When searching, automatically search across all bills
    is_draft_filter = None
    if search and search.strip():
        # Search across all bills regardless of status filter
        is_draft_filter = None
    elif is_draft is not None and is_draft.strip():
        if is_draft.lower() in ('true', '1', 'yes'):
            is_draft_filter = True
        elif is_draft.lower() in ('false', '0', 'no'):
            is_draft_filter = False
        elif is_draft.lower() == 'all':
            is_draft_filter = None  # Show all bills
    
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
    payment_terms = PaymentTermService().read_all()
    return templates.TemplateResponse(
        "bill/create.html",
        {
            "request": request,
            "vendors": vendors,
            "sub_cost_codes": sub_cost_codes,
            "projects": projects,
            "payment_terms": payment_terms,
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
    payment_terms = PaymentTermService().read_all()
    
    # Find the vendor name if bill has a vendor_id
    vendor_name = None
    if bill and bill.vendor_id:
        for vendor in vendors:
            if vendor.id == bill.vendor_id:
                vendor_name = vendor.name
                break
    
    # Find the payment term name if bill has a payment_term_id
    payment_term_name = None
    if bill and bill.payment_term_id:
        for term in payment_terms:
            if term.id == bill.payment_term_id:
                payment_term_name = term.name
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
                        
                        # Get SharePoint location if bill is completed and line item has a project
                        if bill and not bill.is_draft and line_item.project_id:
                            try:
                                from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
                                from modules.module.business.service import ModuleService
                                
                                # Get the module (try Bills first, then Invoices, then first available)
                                module_service = ModuleService()
                                module = module_service.read_by_name("Bills")
                                if not module:
                                    module = module_service.read_by_name("Invoices")
                                if not module:
                                    all_modules = module_service.read_all()
                                    module = all_modules[0] if all_modules else None
                                
                                if module:
                                    project_module_connector = DriveItemProjectModuleConnector()
                                    module_folder = project_module_connector.get_folder_for_module(
                                        project_id=line_item.project_id,
                                        module_id=int(module.id)
                                    )
                                    
                                    if module_folder:
                                        # Get project, vendor, and sub_cost_code for filename generation
                                        project = None
                                        for p in projects:
                                            if p.id == line_item.project_id:
                                                project = p
                                                break
                                        
                                        vendor = None
                                        if bill.vendor_id:
                                            vendor = VendorService().read_by_id(id=bill.vendor_id)
                                        
                                        sub_cost_code = None
                                        if line_item.sub_cost_code_id:
                                            sub_cost_code = SubCostCodeService().read_by_id(id=str(line_item.sub_cost_code_id))
                                        
                                        # Generate expected filename (same format as sync)
                                        project_identifier = (project.abbreviation or project.name or "") if project else ""
                                        vendor_abbreviation = (vendor.abbreviation or vendor.name or "") if vendor else ""
                                        bill_number = bill.bill_number or ""
                                        description = line_item.description or ""
                                        sub_cost_code_number = sub_cost_code.number or "" if sub_cost_code else ""
                                        price = str(line_item.price) if line_item.price is not None else ""
                                        # Format date as mm-dd-yyyy to match sync format
                                        bill_date = ""
                                        if bill.bill_date:
                                            try:
                                                date_str = bill.bill_date[:10]  # Get YYYY-MM-DD part
                                                parts = date_str.split("-")
                                                if len(parts) == 3:
                                                    bill_date = f"{parts[1]}-{parts[2]}-{parts[0]}"  # mm-dd-yyyy
                                            except Exception:
                                                bill_date = bill.bill_date[:10]  # Fallback to original
                                        
                                        filename_parts = [
                                            project_identifier,
                                            vendor_abbreviation,
                                            bill_number,
                                            description,
                                            sub_cost_code_number,
                                            price,
                                            bill_date
                                        ]
                                        filename_parts = [part for part in filename_parts if part]
                                        base_filename = " - ".join(filename_parts)
                                        base_filename = re.sub(r'[<>:"/\\|?*]', '_', base_filename)
                                        file_extension = attachment.file_extension or ""
                                        if file_extension and not file_extension.startswith("."):
                                            file_extension = "." + file_extension
                                        expected_filename = base_filename + file_extension
                                        
                                        # Look up SharePoint file by DriveItem-Attachment mapping (preferred)
                                        # Falls back to filename search for backwards compatibility
                                        actual_file = None
                                        actual_filename = None
                                        actual_file_web_url = None
                                        
                                        # Try direct lookup via DriveItem-Attachment mapping first
                                        try:
                                            from integrations.ms.sharepoint.driveitem.connector.attachment.business.service import DriveItemAttachmentConnector
                                            attachment_connector = DriveItemAttachmentConnector()
                                            driveitem_info = attachment_connector.get_driveitem_for_attachment(attachment.id)
                                            
                                            if driveitem_info:
                                                actual_file = driveitem_info
                                                actual_filename = driveitem_info.get('name')
                                                actual_file_web_url = driveitem_info.get('web_url')
                                        except Exception as mapping_error:
                                            import logging
                                            logger = logging.getLogger(__name__)
                                            logger.debug(f"DriveItem-Attachment lookup failed (table may not exist): {mapping_error}")
                                        
                                        # Fall back to filename search if mapping not found or failed
                                        if not actual_file:
                                            try:
                                                from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
                                                from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
                                                
                                                drive_repo = MsDriveRepository()
                                                drive = drive_repo.read_by_id(module_folder.get('ms_drive_id'))
                                                if drive and module_folder.get('item_id'):
                                                    drive_item_service = MsDriveItemService()
                                                    browse_result = drive_item_service.browse_folder(
                                                        drive_public_id=drive.public_id,
                                                        item_id=module_folder.get('item_id')
                                                    )
                                                    
                                                    if browse_result.get('status_code') == 200:
                                                        items = browse_result.get('items', [])
                                                        expected_filename_lower = expected_filename.lower()
                                                        # Also try matching without extension for compatibility
                                                        expected_base_lower = expected_filename_lower.rsplit('.', 1)[0] if '.' in expected_filename_lower else expected_filename_lower
                                                        
                                                        for item in items:
                                                            if item.get('item_type') == 'file':
                                                                item_name = item.get('name', '')
                                                                item_name_lower = item_name.lower()
                                                                # Strip trailing period if present
                                                                item_name_clean = item_name_lower.rstrip('.')
                                                                
                                                                # Try exact match first, then base name match
                                                                if (item_name_lower == expected_filename_lower or 
                                                                    item_name_clean == expected_base_lower or
                                                                    item_name_lower.startswith(expected_base_lower)):
                                                                    actual_file = item
                                                                    actual_filename = item_name
                                                                    actual_file_web_url = item.get('web_url')
                                                                    break
                                            except Exception as search_error:
                                                import logging
                                                logger = logging.getLogger(__name__)
                                                logger.warning(f"Error searching for file in SharePoint: {search_error}")
                                        
                                        line_item_dict['sharepoint_location'] = {
                                            'folder_name': module_folder.get('name', 'Unknown'),
                                            'folder_web_url': module_folder.get('web_url'),
                                            'expected_filename': expected_filename,
                                            'actual_filename': actual_filename,
                                            'actual_file_web_url': actual_file_web_url,
                                            'file_found': actual_file is not None,
                                            'module_name': module.name
                                        }
                            except Exception as e:
                                # Log error but don't fail the view
                                import logging
                                logger = logging.getLogger(__name__)
                                logger.warning(f"Error getting SharePoint location for line item {line_item.id}: {e}")
            
            line_items_with_attachments.append(line_item_dict)
    
    bill_dict = bill.to_dict()
    if vendor_name:
        bill_dict['vendor_name'] = vendor_name
    if payment_term_name:
        bill_dict['payment_term_name'] = payment_term_name
    
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
    payment_terms = PaymentTermService().read_all()
    
    # Find the vendor public_id if bill has a vendor_id
    vendor_public_id = None
    if bill and bill.vendor_id:
        for vendor in vendors:
            if vendor.id == bill.vendor_id:
                vendor_public_id = vendor.public_id
                break
    
    # Find the payment_term public_id if bill has a payment_term_id
    payment_term_public_id = None
    if bill and bill.payment_term_id:
        for payment_term in payment_terms:
            if payment_term.id == bill.payment_term_id:
                payment_term_public_id = payment_term.public_id
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
    if payment_term_public_id:
        bill_dict['payment_term_public_id'] = payment_term_public_id
    
    return templates.TemplateResponse(
        "bill/edit.html",
        {
            "request": request,
            "bill": bill_dict,
            "vendors": vendors,
            "line_items": line_items_with_attachments,
            "sub_cost_codes": sub_cost_codes,
            "projects": projects,
            "payment_terms": payment_terms,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
