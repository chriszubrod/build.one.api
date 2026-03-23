# Python Standard Library Imports
from decimal import Decimal
from typing import Optional
import logging
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.expense.business.service import ExpenseService
from entities.vendor.business.service import VendorService
from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import PurchaseExpenseRepository
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService
from entities.auth.business.service import get_current_user_web
from entities.inbox.persistence.repo import InboxRecordRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/expense", tags=["web", "expense"])
templates = Jinja2Templates(directory="templates")


def _get_expense_folder_summary(company_id: int) -> Optional[dict]:
    """
    Get summary of the linked SharePoint expense source folder.
    Returns dict with folder_name, folder_web_url, file_count, is_linked.
    """
    try:
        from integrations.ms.sharepoint.driveitem.connector.expense_folder.business.service import DriveItemExpenseFolderConnector
        from integrations.ms.sharepoint.external import client as sp_client

        connector = DriveItemExpenseFolderConnector()
        source_folder = connector.get_folder(company_id, "source")

        if not source_folder:
            return {"is_linked": False}

        drive_id = source_folder.get("drive_id")
        item_id = source_folder.get("item_id")

        file_count = 0
        if drive_id and item_id:
            try:
                children = sp_client.list_drive_item_children(drive_id, item_id)
                if children.get("status_code") == 200:
                    for item in children.get("items", []):
                        name = item.get("name", "")
                        if item.get("item_type") == "file" and (name.lower().endswith('.pdf') or '.' not in name):
                            file_count += 1
            except Exception as e:
                logger.warning("Failed to count files in expense source folder: %s", e)

        return {
            "is_linked": True,
            "folder_name": source_folder.get("name"),
            "folder_web_url": source_folder.get("web_url"),
            "file_count": file_count,
        }
    except Exception as e:
        logger.warning("Error getting expense folder summary: %s", e)
        return None


def _find_matching_qbo_purchase(expense, vendors) -> Optional[dict]:
    """
    Find the best matching uncategorized QBO purchase transaction for a
    draft expense.  Matches by vendor name, amount (±5%), date (±7 days).
    Returns the best match dict or None.
    """
    import re
    from datetime import datetime

    try:
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
    except ImportError:
        return None

    # Resolve vendor name
    vendor_name = None
    if expense.vendor_id:
        for v in vendors:
            if v.id == expense.vendor_id:
                vendor_name = v.name
                break
    if not vendor_name:
        return None

    uncategorized = QboPurchaseService().get_lines_needing_update()
    if not uncategorized:
        return None

    def tokenize(text):
        if not text:
            return set()
        tokens = set(re.split(r'\W+', text.lower()))
        tokens.discard("")
        return tokens - {"the", "inc", "llc", "ltd", "co", "corp"}

    def similarity(a, b):
        if not a or not b:
            return 0.0
        inter = a & b
        union = a | b
        jaccard = len(inter) / len(union) if union else 0.0
        contain = len(inter) / len(a) if a else 0.0
        return max(jaccard, contain * 0.85)

    def parse_date(d):
        if not d:
            return None
        if isinstance(d, datetime):
            return d
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y"):
            try:
                return datetime.strptime(str(d)[:len(fmt) + 5], fmt)
            except (ValueError, IndexError):
                continue
        try:
            return datetime.fromisoformat(str(d).replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None

    vtokens = tokenize(vendor_name)
    exp_amount = float(expense.total_amount) if expense.total_amount else None
    exp_date = parse_date(expense.expense_date)

    best, best_score = None, 0.0

    for line in uncategorized:
        lv = (line.get("entity_ref_name") or "").strip()
        if not lv:
            continue

        vscore = similarity(vtokens, tokenize(lv))
        if vscore < 0.3:
            continue

        score = vscore * 0.50

        la = line.get("line_amount")
        if exp_amount and la and exp_amount > 0:
            diff = abs(la - exp_amount) / exp_amount
            if diff <= 0.05:
                score += (1.0 - diff) * 0.30
            elif diff <= 0.15:
                score += 0.15
        else:
            score += 0.05

        ld = parse_date(line.get("txn_date"))
        if exp_date and ld:
            days = abs((exp_date - ld).days)
            if days <= 1:
                score += 0.20
            elif days <= 7:
                score += (1.0 - (days - 1) * (0.7 / 6)) * 0.20

        if score > best_score and score >= 0.40:
            best_score = score
            best = {
                "qbo_purchase_id": line.get("qbo_purchase_id"),
                "qbo_purchase_line_id": line.get("qbo_purchase_line_id"),
                "vendor_name": lv,
                "amount": la,
                "txn_date": str(line.get("txn_date")) if line.get("txn_date") else None,
                "doc_number": line.get("doc_number"),
                "confidence": round(best_score, 2),
                "realm_id": line.get("realm_id"),
            }

    return best


@router.get("/list")
async def list_expenses(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    page: int = 1,
    page_size: int = 50,
    search: Optional[str] = None,
    vendor_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_draft: Optional[str] = "true",  # Default to showing only draft expenses
    sort_by: str = "ExpenseDate",
    sort_direction: str = "DESC",
):
    """
    List expenses with pagination, search, and filtering.
    Defaults to showing only draft expenses.
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
    # When searching, automatically search across all expenses
    is_draft_filter = None
    if search and search.strip():
        # Search across all expenses regardless of status filter
        is_draft_filter = None
    elif is_draft is not None and is_draft.strip():
        if is_draft.lower() in ('true', '1', 'yes'):
            is_draft_filter = True
        elif is_draft.lower() in ('false', '0', 'no'):
            is_draft_filter = False
        elif is_draft.lower() == 'all':
            is_draft_filter = None  # Show all expenses
    
    # Get expenses with pagination
    expenses = ExpenseService().read_paginated(
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
    total_count = ExpenseService().count(
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
    
    # Get expense folder summary for SharePoint folder processing section
    expense_folder_summary = _get_expense_folder_summary(company_id=1)

    # Get uncategorized QBO purchase lines
    uncategorized_lines = []
    try:
        from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
        uncategorized_lines = QboPurchaseService().get_lines_needing_update()
    except Exception as e:
        logger.debug("Failed to load uncategorized lines: %s", e)

    # Get reference data for inline dropdowns
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()

    # Create a mapping of vendor_id to vendor_name
    vendor_map = {vendor.id: vendor.name for vendor in vendors}

    # Add vendor names to expenses
    expenses_with_vendors = []
    for expense in expenses:
        expense_dict = expense.to_dict()
        if expense.vendor_id and expense.vendor_id in vendor_map:
            expense_dict['vendor_name'] = vendor_map[expense.vendor_id]
        expenses_with_vendors.append(expense_dict)
    
    return templates.TemplateResponse(
        "expense/list.html",
        {
            "request": request,
            "expenses": expenses_with_vendors,
            "vendors": vendors,
            "expense_folder_summary": expense_folder_summary,
            "uncategorized_lines": uncategorized_lines,
            "uncategorized_count": len(uncategorized_lines),
            "sub_cost_codes": [scc.to_dict() if hasattr(scc, 'to_dict') else {"id": scc.id, "number": scc.number, "name": scc.name} for scc in sub_cost_codes],
            "projects": [p.to_dict() if hasattr(p, 'to_dict') else {"id": p.id, "public_id": p.public_id, "name": p.name, "abbreviation": getattr(p, 'abbreviation', None)} for p in projects],
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


@router.get("/edit/{public_id}")
async def edit_expense_redirect(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """Redirect legacy /expense/edit/{id} to /expense/{id}/edit."""
    return RedirectResponse(url=f"/expense/{public_id}/edit", status_code=302)


@router.get("/create")
async def create_expense(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create expense form.
    """
    vendors = VendorService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()
    return templates.TemplateResponse(
        "expense/create.html",
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
async def view_expense(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View an expense.
    """
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    vendors = VendorService().read_all()
    projects = ProjectService().read_all()
    
    # Find the vendor name if expense has a vendor_id
    vendor_name = None
    if expense and expense.vendor_id:
        for vendor in vendors:
            if vendor.id == expense.vendor_id:
                vendor_name = vendor.name
                break
    
    # Fetch line items associated with this expense
    line_items = []
    line_items_with_attachments = []
    if expense and expense.id:
        line_items = ExpenseLineItemService().read_by_expense_id(expense_id=expense.id)
        
        # Fetch attachments for each line item
        expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
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
            all_links = expense_line_item_attachment_service.read_by_expense_line_item_ids(line_item_public_ids)
            attachment_links_map = {link.expense_line_item_public_id: link for link in all_links}
            
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
    
    if not expense:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Expense not found")

    expense_dict = expense.to_dict()
    if vendor_name:
        expense_dict['vendor_name'] = vendor_name

    return templates.TemplateResponse(
        "expense/view.html",
        {
            "request": request,
            "expense": expense_dict,
            "line_items": line_items_with_attachments,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_expense(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit an expense.
    """
    expense = ExpenseService().read_by_public_id(public_id=public_id)
    vendors = VendorService().read_all()
    sub_cost_codes = SubCostCodeService().read_all()
    projects = ProjectService().read_all()
    
    # Find the vendor public_id if expense has a vendor_id
    vendor_public_id = None
    if expense and expense.vendor_id:
        for vendor in vendors:
            if vendor.id == expense.vendor_id:
                vendor_public_id = vendor.public_id
                break
    
    # Fetch line items associated with this expense
    line_items = []
    line_items_with_attachments = []
    if expense and expense.id:
        line_items = ExpenseLineItemService().read_by_expense_id(expense_id=expense.id)
        
        # Fetch attachments for all line items in batch (avoid N+1 queries)
        expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
        attachment_service = AttachmentService()
        
        # Pre-fetch all attachments for these line items in one query
        line_item_public_ids = [li.public_id for li in line_items if li.public_id]
        attachment_links_map = {}
        attachments_map = {}
        if line_item_public_ids:
            all_links = expense_line_item_attachment_service.read_by_expense_line_item_ids(line_item_public_ids)
            attachment_links_map = {link.expense_line_item_public_id: link for link in all_links}
            
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
    
    if not expense:
        raise HTTPException(status_code=404, detail="Expense not found")

    expense_dict = expense.to_dict()

    # Convert Decimal values to floats for JSON serialization
    for key, value in expense_dict.items():
        if isinstance(value, Decimal):
            expense_dict[key] = float(value)
    
    if vendor_public_id:
        expense_dict['vendor_public_id'] = vendor_public_id

    has_qbo_purchase_mapping = False
    if expense and expense.id:
        pe_mapping = PurchaseExpenseRepository().read_by_expense_id(expense_id=int(expense.id))
        has_qbo_purchase_mapping = pe_mapping is not None

    # --- QBO Purchase matching for inbox-created draft expenses ---
    qbo_purchase_match = None
    source_email = None
    if expense and expense.is_draft and not has_qbo_purchase_mapping:
        try:
            inbox_record = InboxRecordRepository().read_by_record_public_id(public_id)
            if inbox_record and inbox_record.message_id:
                source_email = {
                    "message_id": inbox_record.message_id,
                    "subject": inbox_record.subject,
                    "from_email": inbox_record.from_email,
                    "from_name": inbox_record.from_name,
                }
                qbo_purchase_match = _find_matching_qbo_purchase(expense, vendors)
        except Exception as exc:
            logger.debug(
                "QBO purchase match lookup for expense %s failed (non-fatal): %s",
                public_id, exc,
            )

    _ALLOWED_RETURN_PREFIXES = ("/expense/list", "/invoice/")
    return_to = request.query_params.get("return_to") or ""
    if return_to and not any(return_to.startswith(p) for p in _ALLOWED_RETURN_PREFIXES):
        return_to = ""

    return templates.TemplateResponse(
        "expense/edit.html",
        {
            "request": request,
            "expense": expense_dict,
            "vendors": vendors,
            "line_items": line_items_with_attachments,
            "sub_cost_codes": sub_cost_codes,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
            "has_qbo_purchase_mapping": has_qbo_purchase_mapping,
            "qbo_purchase_match": qbo_purchase_match,
            "source_email": source_email,
            "return_to": return_to,
        },
    )
