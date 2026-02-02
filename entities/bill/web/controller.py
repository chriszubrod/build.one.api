# Python Standard Library Imports
from decimal import Decimal
from typing import Optional
import re
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.bill.business.service import BillService
from entities.vendor.business.service import VendorService
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService
from entities.payment_term.business.service import PaymentTermService
from entities.auth.business.service import get_current_user_web

logger = logging.getLogger(__name__)

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
    
    # Get pending bill workflows (confirmed as "bill" but not yet processed)
    pending_workflows = _get_pending_bill_workflows(current_user.get("tenant_id", 1))
    
    return templates.TemplateResponse(
        "bill/list.html",
        {
            "request": request,
            "bills": bills_with_vendors,
            "vendors": vendors,
            "pending_workflows": pending_workflows,
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


def _get_pending_bill_workflows(tenant_id: int) -> list:
    """
    Get bill_processing workflows that need attention.
    
    Returns workflows that:
    - Are bill_processing workflows in non-completed states (needs_review, received, etc.)
    - OR are email_intake workflows confirmed as 'bill' without a child workflow
    """
    try:
        from workflows.workflow.persistence.repo import WorkflowRepository
        
        repo = WorkflowRepository()
        
        # Get bill_processing workflows that are NOT completed (need attention)
        bill_processing_workflows = repo.read_by_tenant_and_type(
            tenant_id=tenant_id,
            workflow_type='bill_processing',
            state=None,
        )
        
        # Filter to those needing attention (not completed, not abandoned)
        pending_bill_workflows = [
            wf for wf in bill_processing_workflows 
            if wf.state not in ['completed', 'abandoned', 'cancelled']
        ]
        
        # Also get completed email_intake workflows to check for any without children
        email_intake_workflows = repo.read_by_tenant_and_type(
            tenant_id=tenant_id,
            workflow_type='email_intake',
            state='completed',
        )
        
        # Pre-fetch all parent workflows to avoid N+1 queries
        parent_ids = set()
        for wf in pending_bill_workflows:
            parent_id = (wf.context or {}).get("parent_workflow_id")
            if parent_id:
                parent_ids.add(parent_id.lower() if isinstance(parent_id, str) else parent_id)
        
        parent_workflows_map = {}
        for parent_id in parent_ids:
            parent = repo.read_by_public_id(parent_id)
            if parent:
                parent_workflows_map[parent_id] = parent
        
        pending = []
        
        # Add bill_processing workflows that need attention
        for wf in pending_bill_workflows:
            ctx = wf.context or {}
            
            # Get parent workflow info for email details from pre-fetched map
            parent_id = ctx.get("parent_workflow_id")
            parent_ctx = {}
            if parent_id:
                parent_id_lower = parent_id.lower() if isinstance(parent_id, str) else parent_id
                parent = parent_workflows_map.get(parent_id_lower)
                if parent:
                    parent_ctx = parent.context or {}
            
            # Get email info from parent or current context
            email = parent_ctx.get("email") or ctx.get("email", {})
            classification = parent_ctx.get("classification") or ctx.get("classification", {})
            extracted = ctx.get("extracted", {})
            
            pending.append({
                "public_id": wf.public_id,
                "subject": email.get("subject", "No subject"),
                "from_address": email.get("from_address", "Unknown"),
                "from_name": email.get("from_name"),
                "received_at": email.get("received_at"),
                "created_at": wf.created_at,
                "confidence": classification.get("confidence", 0),
                "child_workflow_id": wf.public_id,  # This IS the child workflow
                "child_state": wf.state,
                "has_error": wf.state == "needs_review",
                "workflow_type": "bill_processing",
                "extracted_vendor_name": extracted.get("vendor_name"),
                "matched_vendor_id": ctx.get("matched_vendor_id"),
            })
        
        # Also check for email_intake confirmed as bill but without a child workflow
        for wf in email_intake_workflows:
            ctx = wf.context or {}
            confirmed_type = ctx.get("confirmed_entity_type") or ctx.get("entity_type")
            
            if confirmed_type != "bill":
                continue
            
            # Skip if already has a child workflow (handled above)
            child_id = ctx.get("child_workflow_id")
            if child_id:
                continue
            
            # Get email info for display
            email = ctx.get("email", {})
            classification = ctx.get("classification", {})
            
            pending.append({
                "public_id": wf.public_id,
                "subject": email.get("subject", "No subject"),
                "from_address": email.get("from_address", "Unknown"),
                "from_name": email.get("from_name"),
                "received_at": email.get("received_at"),
                "created_at": wf.created_at,
                "confidence": classification.get("confidence", 0),
                "child_workflow_id": None,
                "child_state": None,
                "has_error": False,
                "workflow_type": "email_intake",
            })
        
        # Sort by created_at descending
        pending.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        
        return pending
        
    except Exception as e:
        logger.warning(f"Error getting pending bill workflows: {e}")
        return []


def _get_workflow_for_bill(bill_public_id: str) -> Optional[dict]:
    """
    Find the workflow that created this bill.
    
    Searches for bill_processing workflows with created_bill_public_id matching the bill.
    Returns the workflow context including email conversation.
    """
    try:
        from workflows.workflow.persistence.repo import WorkflowRepository
        from shared.database import get_connection
        
        repo = WorkflowRepository()
        
        # Use direct SQL to find the workflow with this bill in context
        # More efficient than loading all workflows
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT PublicId
                FROM dbo.Workflow
                WHERE WorkflowType = 'bill_processing'
                  AND JSON_VALUE(Context, '$.created_bill_public_id') = ?
            """, (bill_public_id,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            wf_public_id = row[0]
        
        # Now fetch the full workflow
        wf = repo.read_by_public_id(str(wf_public_id).lower())
        if not wf:
            return None
        
        ctx = wf.context or {}
        
        # Get parent for full conversation
        parent_id = ctx.get("parent_workflow_id")
        parent_ctx = {}
        
        if parent_id:
            parent = repo.read_by_public_id(parent_id.lower() if isinstance(parent_id, str) else parent_id)
            if parent:
                parent_ctx = parent.context or {}
        
        # Merge conversation from parent or child
        conversation = parent_ctx.get("conversation") or ctx.get("conversation") or []
        
        return {
            "workflow_id": wf.public_id,
            "parent_workflow_id": parent_id,
            "conversation": conversation,
            "email": parent_ctx.get("email") or ctx.get("email", {}),
            "classification": parent_ctx.get("classification") or ctx.get("classification", {}),
            "extracted": ctx.get("extracted", {}),
            "attachment_blob_urls": parent_ctx.get("attachment_blob_urls") or ctx.get("attachment_blob_urls", []),
        }
        
    except Exception as e:
        logger.warning(f"Error getting workflow for bill {bill_public_id}: {e}")
        return None


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
        
        # Pre-fetch all sub_cost_codes to avoid N+1 queries
        sub_cost_codes = SubCostCodeService().read_all()
        sub_cost_code_map = {scc.id: scc for scc in sub_cost_codes}
        
        # Pre-fetch all attachments for these line items in one query
        line_item_public_ids = [li.public_id for li in line_items if li.public_id]
        attachment_links_map = {}
        attachments_map = {}
        if line_item_public_ids:
            # Get all attachment links for these line items
            all_links = bill_line_item_attachment_service.read_by_bill_line_item_ids(line_item_public_ids)
            attachment_links_map = {link.bill_line_item_public_id: link for link in all_links}
            
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
                        
                        # Skip SharePoint location lookup on page load - too slow
                        # SharePoint location can be fetched via AJAX if needed
                        if False and bill and not bill.is_draft and line_item.project_id:
                            try:
                                from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
                                from entities.module.business.service import ModuleService
                                
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
    
    # Try to get workflow conversation if this bill was created from a workflow
    workflow_conversation = None
    workflow_data = None
    if bill and bill.public_id:
        workflow_data = _get_workflow_for_bill(bill.public_id)
        if workflow_data:
            workflow_conversation = workflow_data.get("conversation", [])
    
    return templates.TemplateResponse(
        "bill/view.html",
        {
            "request": request,
            "bill": bill_dict,
            "line_items": line_items_with_attachments,
            "workflow_conversation": workflow_conversation,
            "workflow_data": workflow_data,
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
        
        # Fetch attachments for all line items in batch (avoid N+1 queries)
        bill_line_item_attachment_service = BillLineItemAttachmentService()
        attachment_service = AttachmentService()
        
        # Pre-fetch all attachments for these line items in one query
        line_item_public_ids = [li.public_id for li in line_items if li.public_id]
        attachment_links_map = {}
        attachments_map = {}
        if line_item_public_ids:
            all_links = bill_line_item_attachment_service.read_by_bill_line_item_ids(line_item_public_ids)
            attachment_links_map = {link.bill_line_item_public_id: link for link in all_links}
            
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
    
    bill_dict = bill.to_dict()
    
    # Convert Decimal values to floats for JSON serialization
    for key, value in bill_dict.items():
        if isinstance(value, Decimal):
            bill_dict[key] = float(value)
    
    if vendor_public_id:
        bill_dict['vendor_public_id'] = vendor_public_id
    if payment_term_public_id:
        bill_dict['payment_term_public_id'] = payment_term_public_id
    
    # Fetch workflow data for email conversation display
    workflow_conversation = None
    workflow_data = None
    if bill and bill.public_id:
        workflow_data = _get_workflow_for_bill(bill.public_id)
        if workflow_data:
            workflow_conversation = workflow_data.get("conversation", [])
    
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
            "workflow_conversation": workflow_conversation,
            "workflow_data": workflow_data,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
