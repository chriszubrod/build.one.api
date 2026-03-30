# Python Standard Library Imports
import io
import json
import logging
import re
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import StreamingResponse

# Local Imports
from shared.rbac import require_module_web
from shared.rbac_constants import Modules
from entities.inbox.business.service import InboxService
from entities.vendor.business.service import VendorService
from entities.project.business.service import ProjectService
from entities.payment_term.business.service import PaymentTermService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.bill.business.service import BillService
from entities.expense.business.service import ExpenseService
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
from shared.storage import AzureBlobStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/inbox", tags=["web", "inbox"])
templates = Jinja2Templates(directory="templates")


# =============================================================================
# Inbox list — main landing page
# =============================================================================

@router.get("")
@router.get("/")
async def inbox_list(
    request: Request,
    current_user: dict = Depends(require_module_web(Modules.INBOX)),
    folder: str = "inbox",
    top: int = 50,
    skip: int = 0,
    unread_only: bool = False,
    flagged_only: bool = True,
):
    """
    Main inbox view.  Lists emails from the invoice inbox with AI classifications.
    Defaults to showing only flagged (red flag) emails.
    """
    svc = InboxService()
    result = svc.list_inbox(folder=folder, top=top, skip=skip, unread_only=unread_only, flagged_only=flagged_only)

    messages = result.get("messages", [])
    error = None
    if result.get("status_code") not in (200, 201):
        error = result.get("message", "Could not load inbox.")
        messages = []

    # Unread count badge
    unread_count = sum(1 for m in messages if not m.get("is_read", True))

    return templates.TemplateResponse(
        "inbox/list.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
            "messages": messages,
            "total_count": result.get("total_count", 0),
            "has_more": result.get("has_more", False),
            "unread_count": unread_count,
            "folder": folder,
            "skip": skip,
            "top": top,
            "unread_only": unread_only,
            "flagged_only": flagged_only,
            "mailbox": result.get("mailbox", ""),
            "error": error,
        },
    )


# =============================================================================
# Single message view + extraction
# =============================================================================

@router.get("/message/{message_id}")
async def view_message(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX)),
):
    """
    View a single message with AI classification and extracted field suggestions.
    """
    svc = InboxService()
    msg_result = svc.get_message_detail(message_id=message_id)

    if msg_result.get("status_code") not in (200, 201):
        return templates.TemplateResponse(
            "inbox/list.html",
            {
                "request": request,
                "current_user": current_user,
                "current_path": request.url.path,
                "messages": [],
                "error": msg_result.get("message", "Message not found."),
                "total_count": 0,
                "has_more": False,
                "unread_count": 0,
                "folder": "inbox",
                "skip": 0,
                "top": 50,
                "unread_only": False,
                "mailbox": "",
            },
        )

    email = msg_result.get("email", {})
    classification = msg_result.get("classification", {})

    # Strip <base> tags from HTML email bodies — vendor emails (e.g. NetSuite)
    # can include <base href="..."> which hijacks all relative URLs on the page.
    if email.get("body_content") and email.get("body_content_type") == "html":
        email["body_content"] = re.sub(
            r"<base\b[^>]*>", "", email["body_content"], flags=re.IGNORECASE
        )

    # Load lookup data for the process form
    vendor_svc = VendorService()
    project_svc = ProjectService()
    payment_term_svc = PaymentTermService()
    sub_cost_code_svc = SubCostCodeService()

    vendors = vendor_svc.read_all()
    projects = project_svc.read_all()
    payment_terms = payment_term_svc.read_all()
    sub_cost_codes = sub_cost_code_svc.read_all()

    return templates.TemplateResponse(
        "inbox/message.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
            "email": email,
            "classification": classification,
            "vendors": vendors,
            "projects": projects,
            "payment_terms": payment_terms,
            "sub_cost_codes": sub_cost_codes,
            "extraction": None,  # Will be loaded via AJAX on demand
        },
    )


# =============================================================================
# AJAX — Fetch message detail as JSON (used by bill/edit inline email view)
# =============================================================================

@router.get("/message/{message_id}/detail")
async def get_message_detail_json(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX)),
):
    """
    Return full message details as JSON for inline display on other pages
    (e.g. the bill edit page).
    """
    svc = InboxService()
    msg_result = svc.get_message_detail(message_id=message_id)

    status = msg_result.get("status_code", 500)
    if status not in (200, 201):
        return JSONResponse(
            {"status_code": status, "message": msg_result.get("message", "Message not found.")},
            status_code=status,
        )

    email = msg_result.get("email", {})

    # Strip <base> tags from HTML bodies (same sanitisation as the template route)
    if email.get("body_content") and email.get("body_content_type") == "html":
        email["body_content"] = re.sub(
            r"<base\b[^>]*>", "", email["body_content"], flags=re.IGNORECASE
        )

    return JSONResponse({"status_code": 200, "email": email}, status_code=200)


# =============================================================================
# AJAX — Extract document intelligence fields from a message attachment
# =============================================================================

@router.post("/message/{message_id}/extract")
async def extract_message(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX)),
    attachment_id: Optional[str] = None,
):
    """
    Run Document Intelligence on the message attachment and return extracted fields.
    Called via AJAX from the message view — returns JSON.
    """
    svc = InboxService()
    result = svc.extract_from_message(
        message_id=message_id,
        attachment_id=attachment_id,
    )

    status = result.get("status_code", 500)
    return JSONResponse(content=result, status_code=status)


# =============================================================================
# Attachment inline preview (streams bytes from MS Graph)
# =============================================================================

@router.get("/message/{message_id}/attachment/{attachment_id}/view")
async def view_inbox_attachment(
    request: Request,
    message_id: str,
    attachment_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX)),
):
    """
    Stream an inbox message attachment for inline browser preview.
    Proxies the MS Graph attachment download so the browser can render
    PDFs/images in an iframe.
    """
    svc = InboxService()
    result = svc.download_attachment(
        message_id=message_id,
        attachment_id=attachment_id,
    )

    if result.get("status_code") != 200 or not result.get("content"):
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to download attachment"),
        )

    content_type = result.get("content_type", "application/octet-stream")
    filename = (result.get("filename") or "attachment").replace('"', "'")

    return StreamingResponse(
        io.BytesIO(result["content"]),
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


# =============================================================================
# AJAX — Forward message to a PM for review/approval
# =============================================================================

@router.post("/message/{message_id}/forward-to-pm")
async def forward_to_pm(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX, "can_update")),
):
    """
    Forward the inbox message to a PM's email address.
    Returns JSON { status_code, message }.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status_code": 400, "message": "Invalid JSON body."}, status_code=400)

    pm_email = body.get("pm_email", "").strip()
    note = body.get("note", "").strip()

    if not pm_email:
        return JSONResponse(
            {"status_code": 422, "message": "PM email address is required."},
            status_code=422,
        )

    svc = InboxService()
    result = svc.forward_to_pm(message_id=message_id, pm_email=pm_email, note=note or None)

    status = result.get("status_code", 500)
    if status not in (200, 201, 202):
        return JSONResponse(result, status_code=max(status, 400))

    logger.info(
        "Inbox: forwarded message %s to PM %s (user=%s)",
        message_id, pm_email, current_user.get("username"),
    )
    return JSONResponse({"status_code": 200, "message": f"Forwarded to {pm_email}."}, status_code=200)


# =============================================================================
# AJAX — Create a Bill from extracted + user-confirmed fields
# =============================================================================

@router.post("/message/{message_id}/create-bill")
async def create_bill_from_inbox(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX, "can_create")),
):
    """
    Create a draft Bill record from user-confirmed extracted fields.
    The user has reviewed/edited the extraction on the message page and submits here.
    Returns JSON with the new bill public_id for redirect.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status_code": 400, "message": "Invalid JSON body."}, status_code=400)

    from datetime import date as _date
    vendor_public_id = body.get("vendor_public_id") or None
    bill_number = body.get("bill_number") or None
    bill_date = body.get("bill_date") or str(_date.today())
    due_date = body.get("due_date") or bill_date
    total_amount = body.get("total_amount")
    memo = body.get("memo")
    payment_term_public_id = body.get("payment_term_public_id")

    bill_svc = BillService()

    try:
        bill = bill_svc.create(
            vendor_public_id=vendor_public_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            payment_term_public_id=payment_term_public_id,
            is_draft=True,
        )
    except ValueError as exc:
        return JSONResponse({"status_code": 409, "message": str(exc)}, status_code=409)
    except Exception as exc:
        logger.exception("Failed to create bill from inbox message %s", message_id)
        return JSONResponse(
            {"status_code": 500, "message": f"Bill creation failed: {exc}"},
            status_code=500,
        )

    # Create a single line item for the entire bill
    line_item = None
    try:
        from decimal import Decimal as _Decimal
        amt = _Decimal(str(total_amount)) if total_amount else None
        line_item = BillLineItemService().create(
            bill_public_id=bill.public_id,
            description=memo or "Invoice",
            quantity=1,
            rate=amt,
            amount=amt,
            is_billable=body.get("is_billable", True),
            markup=_Decimal("0"),
            price=amt,
            project_public_id=body.get("project_public_id") or None,
            sub_cost_code_id=int(body.get("sub_cost_code_id")) if body.get("sub_cost_code_id") else None,
            is_draft=True,
        )
    except Exception as exc:
        logger.warning("Failed to create line item for bill %s: %s", bill.public_id, exc)

    # Attach the source document to the line item
    attachment_id = body.get("attachment_id")
    attachment_message_id = body.get("attachment_message_id") or message_id
    if line_item and attachment_id:
        try:
            inbox_svc = InboxService()
            download = inbox_svc.download_attachment(attachment_message_id, attachment_id)
            if download.get("status_code") == 200:
                file_bytes = download["content"]
                dl_filename = download.get("filename", "attachment.pdf")
                dl_content_type = download.get("content_type", "application/pdf")
                ext = dl_filename.rsplit(".", 1)[-1] if "." in dl_filename else "pdf"

                storage = AzureBlobStorage()
                blob_name = f"inbox/{bill.public_id}/{dl_filename}"
                blob_url = storage.upload_file(
                    blob_name=blob_name,
                    file_content=file_bytes,
                    content_type=dl_content_type,
                )

                attachment = AttachmentService().create(
                    filename=dl_filename,
                    original_filename=dl_filename,
                    file_extension=ext,
                    content_type=dl_content_type,
                    file_size=len(file_bytes),
                    file_hash=AttachmentService.calculate_hash(file_bytes),
                    blob_url=blob_url,
                    description=f"Source invoice - {dl_filename}",
                    category="invoice",
                )

                BillLineItemAttachmentService().create(
                    bill_line_item_public_id=line_item.public_id,
                    attachment_public_id=attachment.public_id,
                )
                logger.info("Attached %s to bill %s line item", dl_filename, bill.public_id)
        except Exception as exc:
            logger.warning("Failed to attach document to bill %s: %s", bill.public_id, exc)

    logger.info(
        "Inbox: created Bill %s from message %s (user=%s)",
        bill.public_id, message_id, current_user.get("username"),
    )

    # Mark the email as processed and persist outcome for ML training
    InboxService().mark_processed(
        message_id,
        record_type="bill",
        record_public_id=bill.public_id,
        processed_via="web",
    )

    return JSONResponse(
        {
            "status_code": 201,
            "message": "Bill created successfully.",
            "bill_public_id": bill.public_id,
            "redirect_url": "/inbox",
        },
        status_code=201,
    )


# =============================================================================
# AJAX — Create an Expense from extracted + user-confirmed fields
# =============================================================================

@router.post("/message/{message_id}/create-expense")
async def create_expense_from_inbox(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX, "can_create")),
):
    """
    Create a draft Expense record from user-confirmed extracted fields.
    Returns JSON with the new expense public_id for redirect.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status_code": 400, "message": "Invalid JSON body."}, status_code=400)

    vendor_public_id = body.get("vendor_public_id")
    reference_number = body.get("reference_number") or body.get("bill_number")
    expense_date = body.get("expense_date") or body.get("bill_date")
    total_amount = body.get("total_amount")
    memo = body.get("memo")
    line_items = body.get("line_items", [])

    if not vendor_public_id or not expense_date:
        return JSONResponse(
            {"status_code": 422, "message": "vendor and expense_date are required."},
            status_code=422,
        )

    expense_svc = ExpenseService()
    line_item_svc = ExpenseLineItemService()

    try:
        expense = expense_svc.create(
            vendor_public_id=vendor_public_id,
            expense_date=expense_date,
            reference_number=reference_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=True,
        )
    except Exception as exc:
        logger.exception("Failed to create expense from inbox message %s", message_id)
        return JSONResponse(
            {"status_code": 500, "message": f"Expense creation failed: {exc}"},
            status_code=500,
        )

    for li in line_items:
        try:
            from decimal import Decimal as _Decimal
            amt = li.get("amount")
            line_item_svc.create(
                expense_public_id=expense.public_id,
                description=li.get("description", ""),
                amount=_Decimal(str(amt)) if amt else None,
                project_public_id=li.get("project_public_id") or None,
            )
        except Exception as exc:
            logger.warning("Failed to create line item for expense %s: %s", expense.public_id, exc)

    logger.info(
        "Inbox: created Expense %s from message %s (user=%s)",
        expense.public_id, message_id, current_user.get("username"),
    )

    # Mark the email as processed and persist outcome for ML training
    InboxService().mark_processed(
        message_id,
        record_type="expense",
        record_public_id=expense.public_id,
        processed_via="web",
    )

    return JSONResponse(
        {
            "status_code": 201,
            "message": "Expense created successfully.",
            "expense_public_id": expense.public_id,
            "redirect_url": "/inbox",
        },
        status_code=201,
    )


# =============================================================================
# AJAX — Create a Vendor Credit from extracted + user-confirmed fields
# =============================================================================

@router.post("/message/{message_id}/create-credit")
async def create_credit_from_inbox(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX, "can_create")),
):
    """
    Create a draft BillCredit record from user-confirmed extracted fields.
    Returns JSON with the new credit public_id for redirect.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status_code": 400, "message": "Invalid JSON body."}, status_code=400)

    vendor_public_id = body.get("vendor_public_id")
    credit_number = body.get("bill_number") or body.get("credit_number")
    credit_date = body.get("bill_date") or body.get("credit_date")
    total_amount = body.get("total_amount")
    memo = body.get("memo")
    line_items = body.get("line_items", [])

    if not vendor_public_id or not credit_date:
        return JSONResponse(
            {"status_code": 422, "message": "vendor and credit_date are required."},
            status_code=422,
        )

    credit_svc = BillCreditService()
    line_item_svc = BillCreditLineItemService()

    try:
        credit = credit_svc.create(
            vendor_public_id=vendor_public_id,
            credit_date=credit_date,
            credit_number=credit_number or "",
            total_amount=total_amount,
            memo=memo,
            is_draft=True,
        )
    except ValueError as exc:
        return JSONResponse({"status_code": 409, "message": str(exc)}, status_code=409)
    except Exception as exc:
        logger.exception("Failed to create credit from inbox message %s", message_id)
        return JSONResponse(
            {"status_code": 500, "message": f"Credit creation failed: {exc}"},
            status_code=500,
        )

    for li in line_items:
        try:
            from decimal import Decimal as _Decimal
            amt = li.get("amount")
            line_item_svc.create(
                bill_credit_public_id=credit.public_id,
                description=li.get("description", ""),
                amount=_Decimal(str(amt)) if amt else None,
                project_public_id=li.get("project_public_id") or None,
            )
        except Exception as exc:
            logger.warning("Failed to create line item for credit %s: %s", credit.public_id, exc)

    logger.info(
        "Inbox: created BillCredit %s from message %s (user=%s)",
        credit.public_id, message_id, current_user.get("username"),
    )

    InboxService().mark_processed(
        message_id,
        record_type="vendor_credit",
        record_public_id=credit.public_id,
        processed_via="web",
    )

    return JSONResponse(
        {
            "status_code": 201,
            "message": "Credit created successfully.",
            "credit_public_id": credit.public_id,
            "redirect_url": "/inbox",
        },
        status_code=201,
    )


# =============================================================================
# AJAX — Mark message as read / unread
# =============================================================================

@router.post("/message/{message_id}/mark-read")
async def mark_message_read(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX, "can_update")),
):
    """
    Toggle a message's read/unread status.
    Expects JSON body: { "is_read": true/false }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status_code": 400, "message": "Invalid JSON body."}, status_code=400)

    is_read = body.get("is_read", True)

    svc = InboxService()
    result = svc.mark_read(message_id=message_id, is_read=is_read)

    status = result.get("status_code", 500)
    return JSONResponse(result, status_code=status)


# =============================================================================
# AJAX — Flag / unflag a message
# =============================================================================

@router.post("/message/{message_id}/flag")
async def flag_message(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX, "can_update")),
):
    """
    Toggle a message's flagged status.
    Expects JSON body: { "flagged": true/false }
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status_code": 400, "message": "Invalid JSON body."}, status_code=400)

    flagged = body.get("flagged", True)

    svc = InboxService()
    result = svc.flag_message(message_id=message_id, flagged=flagged)

    status = result.get("status_code", 500)
    return JSONResponse(result, status_code=status)


# =============================================================================
# AJAX — Classify a message (full heuristic + agent)
# =============================================================================

@router.post("/message/{message_id}/classify")
async def classify_message(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX, "can_update")),
):
    """
    Run full AI classification (heuristic + LangGraph agent) on a message.
    Persists the result and returns the classification JSON.
    Called via AJAX for lazy background classification or manual re-classify.
    """
    svc = InboxService()
    result = svc.classify_message(message_id=message_id)

    status = result.get("status_code", 500)
    return JSONResponse(result, status_code=status)


# =============================================================================
# AJAX — Fetch conversation thread for a message
# =============================================================================

@router.get("/message/{message_id}/thread")
async def get_message_thread(
    request: Request,
    message_id: str,
    current_user: dict = Depends(require_module_web(Modules.INBOX)),
):
    """
    Fetch all messages in the same conversation thread.
    Returns JSON array of thread messages sorted by receivedDateTime.
    """
    from integrations.ms.mail.external.client import (
        get_message as graph_get_message,
        search_all_messages,
    )

    # First, get the current message to find its conversationId
    svc = InboxService()
    msg_result = svc.get_message_detail(message_id=message_id)

    if msg_result.get("status_code") not in (200, 201):
        return JSONResponse(
            {"status_code": 404, "message": "Message not found.", "messages": []},
            status_code=404,
        )

    email = msg_result.get("email", {})
    conversation_id = email.get("conversation_id")

    if not conversation_id:
        return JSONResponse(
            {"status_code": 200, "message": "No conversation thread.", "messages": []},
            status_code=200,
        )

    # Search for all messages in this conversation
    try:
        thread_result = search_all_messages(conversation_id=conversation_id, top=25)
    except Exception as exc:
        logger.warning("Thread search failed for message %s: %s", message_id, exc)
        return JSONResponse(
            {"status_code": 200, "message": "Thread search failed.", "messages": []},
            status_code=200,
        )

    thread_messages = thread_result.get("messages", [])

    # Format for the sidebar
    formatted = []
    for msg in thread_messages:
        body_content = msg.get("body_content", "") or ""
        body_preview = msg.get("body_preview", "") or ""
        # Truncate preview to 300 chars
        if len(body_preview) > 300:
            body_preview = body_preview[:300] + "..."

        formatted.append({
            "message_id": msg.get("message_id"),
            "from_name": msg.get("from_name", ""),
            "from_email": msg.get("from_email", ""),
            "subject": msg.get("subject", ""),
            "body_preview": body_preview,
            "body_content": body_content,
            "received_datetime": msg.get("received_datetime", ""),
            "has_attachments": msg.get("has_attachments", False),
            "attachment_count": len(msg.get("attachments", [])) if msg.get("attachments") else 0,
            "is_current": msg.get("message_id") == message_id,
        })

    return JSONResponse(
        {"status_code": 200, "messages": formatted},
        status_code=200,
    )
