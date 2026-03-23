# Python Standard Library Imports
from decimal import Decimal
from typing import Optional
import logging
from fastapi import APIRouter, HTTPException, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.invoice.business.service import InvoiceService
from entities.invoice_line_item.business.service import InvoiceLineItemService
from entities.invoice_attachment.business.service import InvoiceAttachmentService
from entities.attachment.business.service import AttachmentService
from entities.project.business.service import ProjectService
from entities.payment_term.business.service import PaymentTermService
from entities.customer.business.service import CustomerService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.auth.business.service import get_current_user_web

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoice", tags=["web", "invoice"])
templates = Jinja2Templates(directory="templates")


def _format_amount(value, decimals=2):
    if value is None:
        return ""
    try:
        return f"{float(value):,.{int(decimals)}f}"
    except (TypeError, ValueError):
        return str(value) if value != "" else ""


templates.env.filters["format_amount"] = _format_amount


def _date_to_mm_dd_yyyy(val: Optional[str]) -> str:
    if not val or not isinstance(val, str) or len(val) < 10:
        return val or ""
    s = val.strip()[:10]
    if s[4] != "-" or s[7] != "-":
        return val
    y, m, d = s[:4], s[5:7], s[8:10]
    if y.startswith("00") and len(y) == 4:
        y = "20" + y[2:]
    return f"{m}-{d}-{y}"


def _enrich_line_items(line_items) -> list[dict]:
    """
    Batch-enrich invoice line items with parent number, vendor, cost code,
    and attachment indicator in a single DB round-trip per source type.
    """
    from shared.database import get_connection

    bill_ids = []
    expense_ids = []
    credit_ids = []
    for li in line_items:
        if li.source_type == "BillLineItem" and li.bill_line_item_id:
            bill_ids.append(li.bill_line_item_id)
        elif li.source_type == "ExpenseLineItem" and li.expense_line_item_id:
            expense_ids.append(li.expense_line_item_id)
        elif li.source_type == "BillCreditLineItem" and li.bill_credit_line_item_id:
            credit_ids.append(li.bill_credit_line_item_id)

    bill_map = {}
    expense_map = {}
    credit_map = {}

    with get_connection() as conn:
        cursor = conn.cursor()

        if bill_ids:
            placeholders = ",".join("?" * len(bill_ids))
            cursor.execute(f"""
                SELECT bli.Id,
                       b.BillNumber AS ParentNumber,
                       b.PublicId AS ParentPublicId,
                       b.BillDate AS SourceDate,
                       v.Name AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.BillLineItem bli
                JOIN dbo.Bill b ON b.Id = bli.BillId
                LEFT JOIN dbo.Vendor v ON v.Id = b.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = bli.SubCostCodeId
                LEFT JOIN dbo.CostCode cc ON cc.Id = scc.CostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.BillLineItemAttachment blia
                    JOIN dbo.Attachment a ON a.Id = blia.AttachmentId
                    WHERE blia.BillLineItemId = bli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE bli.Id IN ({placeholders})
            """, bill_ids)
            for row in cursor.fetchall():
                bill_map[row.Id] = {
                    "parent_number": row.ParentNumber or "",
                    "parent_public_id": str(row.ParentPublicId) if row.ParentPublicId else "",
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "sub_cost_code_number": row.SccNumber or "",
                    "sub_cost_code_name": row.SccName or "",
                    "cost_code_number": row.CcNumber or "",
                    "cost_code_name": row.CcName or "",
                    "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                }

        if expense_ids:
            placeholders = ",".join("?" * len(expense_ids))
            cursor.execute(f"""
                SELECT eli.Id,
                       e.ReferenceNumber AS ParentNumber,
                       e.PublicId AS ParentPublicId,
                       e.ExpenseDate AS SourceDate,
                       v.Name AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.ExpenseLineItem eli
                JOIN dbo.Expense e ON e.Id = eli.ExpenseId
                LEFT JOIN dbo.Vendor v ON v.Id = e.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = eli.SubCostCodeId
                LEFT JOIN dbo.CostCode cc ON cc.Id = scc.CostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.ExpenseLineItemAttachment elia
                    JOIN dbo.Attachment a ON a.Id = elia.AttachmentId
                    WHERE elia.ExpenseLineItemId = eli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE eli.Id IN ({placeholders})
            """, expense_ids)
            for row in cursor.fetchall():
                expense_map[row.Id] = {
                    "parent_number": row.ParentNumber or "",
                    "parent_public_id": str(row.ParentPublicId) if row.ParentPublicId else "",
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "sub_cost_code_number": row.SccNumber or "",
                    "sub_cost_code_name": row.SccName or "",
                    "cost_code_number": row.CcNumber or "",
                    "cost_code_name": row.CcName or "",
                    "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                }

        if credit_ids:
            placeholders = ",".join("?" * len(credit_ids))
            cursor.execute(f"""
                SELECT bcli.Id,
                       bc.CreditNumber AS ParentNumber,
                       bc.PublicId AS ParentPublicId,
                       bc.CreditDate AS SourceDate,
                       v.Name AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.BillCreditLineItem bcli
                JOIN dbo.BillCredit bc ON bc.Id = bcli.BillCreditId
                LEFT JOIN dbo.Vendor v ON v.Id = bc.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = bcli.SubCostCodeId
                LEFT JOIN dbo.CostCode cc ON cc.Id = scc.CostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.BillCreditLineItemAttachment bclia
                    JOIN dbo.Attachment a ON a.Id = bclia.AttachmentId
                    WHERE bclia.BillCreditLineItemId = bcli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE bcli.Id IN ({placeholders})
            """, credit_ids)
            for row in cursor.fetchall():
                credit_map[row.Id] = {
                    "parent_number": row.ParentNumber or "",
                    "parent_public_id": str(row.ParentPublicId) if row.ParentPublicId else "",
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "sub_cost_code_number": row.SccNumber or "",
                    "sub_cost_code_name": row.SccName or "",
                    "cost_code_number": row.CcNumber or "",
                    "cost_code_name": row.CcName or "",
                    "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                }

        cursor.close()

    results = []
    for li in line_items:
        li_dict = li.to_dict()
        for key, value in li_dict.items():
            if isinstance(value, Decimal):
                li_dict[key] = float(value)

        enrichment = {}
        if li.source_type == "BillLineItem" and li.bill_line_item_id:
            enrichment = bill_map.get(li.bill_line_item_id, {})
        elif li.source_type == "ExpenseLineItem" and li.expense_line_item_id:
            enrichment = expense_map.get(li.expense_line_item_id, {})
        elif li.source_type == "BillCreditLineItem" and li.bill_credit_line_item_id:
            enrichment = credit_map.get(li.bill_credit_line_item_id, {})

        # Skip orphaned non-Manual items whose source record no longer exists
        if li.source_type != "Manual" and li.source_type is not None:
            has_fk = li.bill_line_item_id or li.expense_line_item_id or li.bill_credit_line_item_id
            if not has_fk or not enrichment:
                continue

        li_dict.setdefault("parent_number", "")
        li_dict.setdefault("parent_public_id", "")
        li_dict.setdefault("source_date", "")
        li_dict.setdefault("vendor_name", "")
        li_dict.setdefault("sub_cost_code_number", "")
        li_dict.setdefault("sub_cost_code_name", "")
        li_dict.setdefault("cost_code_number", "")
        li_dict.setdefault("cost_code_name", "")
        li_dict.setdefault("attachment_public_id", "")
        li_dict.update(enrichment)

        results.append(li_dict)

    return results


@router.get("/list")
async def list_invoices(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    page: int = 1,
    page_size: int = 50,
    search: Optional[str] = None,
    project_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_draft: Optional[str] = "all",
    sort_by: str = "InvoiceDate",
    sort_direction: str = "DESC",
):
    if page < 1:
        page = 1
    if page_size < 10:
        page_size = 10
    elif page_size > 100:
        page_size = 100

    project_id_int = None
    if project_id and project_id.strip():
        try:
            project_id_int = int(project_id)
        except (ValueError, TypeError):
            project_id_int = None

    is_draft_filter = None
    if search and search.strip():
        is_draft_filter = None
    elif is_draft is not None and is_draft.strip():
        if is_draft.lower() in ('true', '1', 'yes'):
            is_draft_filter = True
        elif is_draft.lower() in ('false', '0', 'no'):
            is_draft_filter = False
        elif is_draft.lower() == 'all':
            is_draft_filter = None

    invoices = InvoiceService().read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search,
        project_id=project_id_int,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft_filter,
        sort_by=sort_by,
        sort_direction=sort_direction,
    )

    total_count = InvoiceService().count(
        search_term=search,
        project_id=project_id_int,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft_filter,
    )

    total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 1
    has_previous = page > 1
    has_next = page < total_pages

    projects = ProjectService().read_all()
    project_map = {p.id: p.name for p in projects}

    invoices_with_projects = []
    for inv in invoices:
        inv_dict = inv.to_dict()
        if inv.project_id and inv.project_id in project_map:
            inv_dict['project_name'] = project_map[inv.project_id]
        invoices_with_projects.append(inv_dict)

    return_to = request.url.path + ("?" + request.url.query if request.url.query else "")

    return templates.TemplateResponse(
        "invoice/list.html",
        {
            "request": request,
            "invoices": invoices_with_projects,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
            "return_to": return_to,
            "page": page,
            "page_size": page_size,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_previous": has_previous,
            "has_next": has_next,
            "search": search or "",
            "project_id": project_id_int if project_id_int else None,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "is_draft": is_draft or "",
            "sort_by": sort_by,
            "sort_direction": sort_direction,
        },
    )


@router.get("/create")
async def create_invoice(request: Request, id: Optional[str] = None, current_user: dict = Depends(get_current_user_web)):
    projects = ProjectService().read_all()
    payment_terms = PaymentTermService().read_all()

    invoice_dict = None
    line_items_dicts = []
    if id:
        invoice = InvoiceService().read_by_public_id(public_id=id)
        if invoice:
            invoice_dict = invoice.to_dict()
            for key in ("invoice_date", "due_date"):
                invoice_dict[key] = _date_to_mm_dd_yyyy(invoice_dict.get(key))
            for p in projects:
                if p.id == invoice.project_id:
                    invoice_dict['project_public_id'] = p.public_id
                    break
            for t in payment_terms:
                if t.id == invoice.payment_term_id:
                    invoice_dict['payment_term_public_id'] = t.public_id
                    break
            line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
            line_items_dicts = _enrich_line_items(line_items)

    return templates.TemplateResponse(
        "invoice/create.html",
        {
            "request": request,
            "projects": projects,
            "payment_terms": payment_terms,
            "current_user": current_user,
            "current_path": request.url.path,
            "invoice": invoice_dict,
            "line_items": line_items_dicts,
        },
    )


@router.get("/{public_id}")
async def view_invoice(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    invoice = InvoiceService().read_by_public_id(public_id=public_id)
    projects = ProjectService().read_all()
    payment_terms = PaymentTermService().read_all()

    project_name = None
    if invoice and invoice.project_id:
        for p in projects:
            if p.id == invoice.project_id:
                project_name = p.name
                break

    payment_term_name = None
    if invoice and invoice.payment_term_id:
        for term in payment_terms:
            if term.id == invoice.payment_term_id:
                payment_term_name = term.name
                break

    line_items = []
    if invoice and invoice.id:
        line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)

    line_items_dicts = _enrich_line_items(line_items)

    # Fetch invoice-level attachments
    invoice_attachments = []
    if invoice and invoice.id:
        attachment_service = AttachmentService()
        inv_attachment_links = InvoiceAttachmentService().read_by_invoice_id(invoice_id=invoice.id)
        for link in inv_attachment_links:
            if link.attachment_id:
                attachment = attachment_service.read_by_id(id=link.attachment_id)
                if attachment:
                    invoice_attachments.append({
                        "link": link.to_dict(),
                        "attachment": attachment.to_dict(),
                    })

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice_dict = invoice.to_dict()
    if project_name:
        invoice_dict['project_name'] = project_name
    if payment_term_name:
        invoice_dict['payment_term_name'] = payment_term_name

    for key in ("invoice_date", "due_date"):
        invoice_dict[key] = _date_to_mm_dd_yyyy(invoice_dict.get(key))

    return_to = request.query_params.get("return_to") or ""
    if return_to and not return_to.startswith("/invoice/list"):
        return_to = ""

    return templates.TemplateResponse(
        "invoice/view.html",
        {
            "request": request,
            "invoice": invoice_dict,
            "line_items": line_items_dicts,
            "invoice_attachments": invoice_attachments,
            "current_user": current_user,
            "current_path": request.url.path,
            "return_to": return_to,
        },
    )


@router.get("/{public_id}/edit")
async def edit_invoice(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    invoice = InvoiceService().read_by_public_id(public_id=public_id)
    projects = ProjectService().read_all()
    payment_terms = PaymentTermService().read_all()

    project_public_id = None
    if invoice and invoice.project_id:
        for p in projects:
            if p.id == invoice.project_id:
                project_public_id = p.public_id
                break

    payment_term_public_id = None
    if invoice and invoice.payment_term_id:
        for term in payment_terms:
            if term.id == invoice.payment_term_id:
                payment_term_public_id = term.public_id
                break

    line_items = []
    line_items_dicts = []
    if invoice and invoice.id:
        # On draft invoices, refresh Price from source BillLineItem/ExpenseLineItem
        # for any InvoiceLineItems where Price is NULL but the source now has a value.
        # This handles the case where a bill was corrected after the line item was loaded.
        if invoice.is_draft:
            from shared.database import get_connection
            with get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE ili
                    SET ili.Price = bli.Price
                    FROM dbo.InvoiceLineItem ili
                    JOIN dbo.BillLineItem bli ON bli.Id = ili.BillLineItemId
                    WHERE ili.InvoiceId = ?
                      AND ili.Price IS NULL
                      AND bli.Price IS NOT NULL
                """, [invoice.id])
                cursor.execute("""
                    UPDATE ili
                    SET ili.Price = eli.Price
                    FROM dbo.InvoiceLineItem ili
                    JOIN dbo.ExpenseLineItem eli ON eli.Id = ili.ExpenseLineItemId
                    WHERE ili.InvoiceId = ?
                      AND ili.Price IS NULL
                      AND eli.Price IS NOT NULL
                """, [invoice.id])
        line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
        line_items_dicts = _enrich_line_items(line_items)
        _type_order = {"BillLineItem": 0, "BillCreditLineItem": 1, "ExpenseLineItem": 2, "Manual": 3}
        line_items_dicts.sort(key=lambda li: (
            _type_order.get(li.get("source_type", ""), 9),
            (li.get("vendor_name") or "").lower()
        ))

    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    invoice_dict = invoice.to_dict()
    for key, value in invoice_dict.items():
        if isinstance(value, Decimal):
            invoice_dict[key] = float(value)

    if project_public_id:
        invoice_dict['project_public_id'] = project_public_id
    if payment_term_public_id:
        invoice_dict['payment_term_public_id'] = payment_term_public_id

    for key in ("invoice_date", "due_date"):
        invoice_dict[key] = _date_to_mm_dd_yyyy(invoice_dict.get(key))

    return_to = request.query_params.get("return_to") or ""
    if return_to and not return_to.startswith("/invoice/list"):
        return_to = ""

    sub_cost_codes = SubCostCodeService().read_all()

    return templates.TemplateResponse(
        "invoice/edit.html",
        {
            "request": request,
            "invoice": invoice_dict,
            "projects": projects,
            "line_items": line_items_dicts,
            "payment_terms": payment_terms,
            "sub_cost_codes": sub_cost_codes,
            "current_user": current_user,
            "current_path": request.url.path,
            "return_to": return_to,
        },
    )
