"""
Copilot Agent Tools

LangChain @tool wrappers around all 18 copilot capabilities.
Each tool uses lazy imports to avoid circular dependencies.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Document / QA Tools
# ---------------------------------------------------------------------------

@tool
def search_documents(
    query: str,
    category: Optional[str] = None,
    top: int = 5,
) -> str:
    """Search indexed documents using hybrid keyword and semantic search.

    Use this to find documents by content, vendor name, project name, or any other terms.

    Args:
        query: The search query. Can be a vendor name, keyword, phrase, or natural language description.
        category: Optional filter by document category (bill, invoice, receipt, purchase_order, quote, etc.)
        top: Maximum number of results to return (default 5).
    """
    from entities.search.business.service import get_search_service

    service = get_search_service()
    results = service.hybrid_search(query=query, category=category, top=top)

    data = {
        "count": len(results),
        "documents": [
            {
                "filename": r.get("original_filename") or r.get("filename", "Unknown"),
                "category": r.get("category", "Uncategorized"),
                "score": r.get("@search.score"),
                "content_preview": (r.get("content", "") or "")[:200],
                "public_id": r.get("public_id"),
            }
            for r in results
        ],
    }
    return json.dumps(data, default=str)


@tool
def answer_question(
    question: str,
    category: Optional[str] = None,
    max_documents: int = 5,
) -> str:
    """Answer a natural language question by searching relevant documents and synthesizing an answer with source citations.

    Best for questions about document content, project costs, or vendor details found in uploaded documents.

    Args:
        question: The question to answer based on indexed documents.
        category: Optional category filter to narrow document search.
        max_documents: Maximum number of documents to search (default 5, max 10).
    """
    from entities.qa.business.service import get_qa_service

    service = get_qa_service()
    result = service.ask(question=question, category=category, max_documents=max_documents)

    sources = []
    if result.get("sources"):
        sources = [
            {"filename": s.get("filename"), "public_id": s.get("public_id")}
            for s in result["sources"][:3]
        ]

    data = {
        "answer": result.get("answer", "No answer found."),
        "sources": sources,
    }
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# System Status
# ---------------------------------------------------------------------------

@tool
def get_system_status() -> str:
    """Get current system status including counts of documents pending extraction and categorization.

    Use this when users ask about system health, pending work, or what needs attention.
    """
    from entities.attachment.persistence.repo import AttachmentRepository

    repo = AttachmentRepository()
    pending_ext = repo.read_pending_extraction()
    pending_cat = repo.read_pending_categorization(limit=10)

    data = {
        "pending_extraction": len(pending_ext),
        "pending_categorization": len(pending_cat),
        "recent_uncategorized": [
            {"filename": doc.original_filename or doc.filename}
            for doc in pending_cat[:5]
        ],
    }
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# Entity List Tools
# ---------------------------------------------------------------------------

@tool
def list_bills(
    search_term: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_draft: Optional[bool] = None,
    page: int = 1,
    page_size: int = 10,
) -> str:
    """Query bills (accounts payable invoices FROM vendors).

    Returns bill records with bill number, vendor, dates, amounts, and status.
    Supports filtering by search term, date range, and draft status.

    Args:
        search_term: Search term to filter bills (searches bill number, vendor name, memo).
        start_date: Filter bills on or after this date (YYYY-MM-DD format).
        end_date: Filter bills on or before this date (YYYY-MM-DD format).
        is_draft: Filter by draft status. True for drafts only, false for finalized only, omit for all.
        page: Page number for pagination (default 1).
        page_size: Number of results per page (default 10, max 50).
    """
    from entities.bill.business.service import BillService

    service = BillService()
    page_size = min(page_size, 50)

    bills = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft,
    )
    total = service.count(
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft,
    )

    data = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "bills": [
            {
                "public_id": b.public_id,
                "bill_number": b.bill_number,
                "bill_date": b.bill_date,
                "due_date": b.due_date,
                "total_amount": b.total_amount,
                "memo": b.memo,
                "is_draft": b.is_draft,
                "vendor_id": b.vendor_id,
            }
            for b in bills
        ],
    }
    return json.dumps(data, default=str)


@tool
def list_vendors() -> str:
    """List all vendors. Returns vendor names, public IDs, and draft status."""
    from entities.vendor.business.service import VendorService

    service = VendorService()
    vendors = service.read_all()

    data = {
        "count": len(vendors),
        "vendors": [
            {
                "public_id": v.public_id,
                "name": v.name,
                "abbreviation": v.abbreviation,
                "is_draft": v.is_draft,
            }
            for v in vendors
        ],
    }
    return json.dumps(data, default=str)


@tool
def list_projects() -> str:
    """List all projects. Returns project names, abbreviations, descriptions, and statuses."""
    from entities.project.business.service import ProjectService

    service = ProjectService()
    projects = service.read_all()

    data = {
        "count": len(projects),
        "projects": [
            {
                "public_id": p.public_id,
                "name": p.name,
                "abbreviation": p.abbreviation,
                "description": p.description,
                "status": p.status,
            }
            for p in projects
        ],
    }
    return json.dumps(data, default=str)


@tool
def list_expenses(
    search_term: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_draft: Optional[bool] = None,
    page: int = 1,
    page_size: int = 10,
) -> str:
    """Query expenses (direct purchases, not billed through vendors).

    Returns expense records with reference numbers, vendor, dates, amounts, and status.

    Args:
        search_term: Search term to filter expenses.
        start_date: Filter expenses on or after this date (YYYY-MM-DD format).
        end_date: Filter expenses on or before this date (YYYY-MM-DD format).
        is_draft: Filter by draft status.
        page: Page number for pagination (default 1).
        page_size: Number of results per page (default 10, max 50).
    """
    from entities.expense.business.service import ExpenseService

    service = ExpenseService()
    page_size = min(page_size, 50)

    expenses = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft,
    )
    total = service.count(
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft,
    )

    data = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "expenses": [
            {
                "public_id": e.public_id,
                "reference_number": e.reference_number,
                "expense_date": e.expense_date,
                "total_amount": e.total_amount,
                "memo": e.memo,
                "is_draft": e.is_draft,
                "vendor_id": e.vendor_id,
            }
            for e in expenses
        ],
    }
    return json.dumps(data, default=str)


@tool
def list_invoices(
    search_term: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    is_draft: Optional[bool] = None,
    page: int = 1,
    page_size: int = 10,
) -> str:
    """Query invoices (accounts receivable documents TO customers).

    Returns invoice records with invoice number, project, dates, amounts, and status.

    Args:
        search_term: Search term to filter invoices.
        start_date: Filter invoices on or after this date (YYYY-MM-DD format).
        end_date: Filter invoices on or before this date (YYYY-MM-DD format).
        is_draft: Filter by draft status.
        page: Page number for pagination (default 1).
        page_size: Number of results per page (default 10, max 50).
    """
    from entities.invoice.business.service import InvoiceService

    service = InvoiceService()
    page_size = min(page_size, 50)

    invoices = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft,
    )
    total = service.count(
        search_term=search_term,
        start_date=start_date,
        end_date=end_date,
        is_draft=is_draft,
    )

    data = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "invoices": [
            {
                "public_id": i.public_id,
                "invoice_number": i.invoice_number,
                "invoice_date": i.invoice_date,
                "due_date": i.due_date,
                "total_amount": i.total_amount,
                "memo": i.memo,
                "is_draft": i.is_draft,
                "project_id": i.project_id,
            }
            for i in invoices
        ],
    }
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# Inbox Tools
# ---------------------------------------------------------------------------

@tool
def list_inbox_emails(
    unread_only: bool = False,
    top: int = 20,
) -> str:
    """List emails from the invoice inbox.

    Returns email subjects, senders, dates, and AI classifications (bill, expense, vendor_credit, inquiry, statement, unknown).
    Use this to show pending or recent inbox items.

    Args:
        unread_only: If true, only return unread emails (default false).
        top: Maximum number of emails to return (default 20).
    """
    from entities.inbox.business.service import InboxService

    service = InboxService()
    result = service.list_inbox(top=top, unread_only=unread_only)

    messages = result.get("messages", [])
    data = {
        "count": len(messages),
        "emails": [
            {
                "id": e.get("message_id"),
                "subject": e.get("subject"),
                "sender": e.get("from_name") or e.get("from_email"),
                "received": e.get("received_datetime"),
                "is_read": e.get("is_read"),
                "classification": e.get("classification", {}).get("type"),
                "classification_confidence": e.get("classification", {}).get("confidence"),
                "has_attachments": e.get("has_attachments"),
            }
            for e in messages
        ],
    }
    return json.dumps(data, default=str)


@tool
def get_inbox_email_detail(message_id: str) -> str:
    """Get full details of a specific inbox email including body content, attachments, and AI classification.

    Use this after list_inbox_emails to show a user the details of a particular email before processing it.

    Args:
        message_id: The Graph API message ID of the email to retrieve.
    """
    from entities.inbox.business.service import InboxService

    service = InboxService()
    result = service.get_message_detail(message_id=message_id)

    if result.get("status_code") not in (200, 201):
        return json.dumps({"error": result.get("message", "Failed to retrieve email details.")})

    email = result.get("email", {})
    classification = result.get("classification", {})

    body = email.get("body_content") or email.get("body_preview") or ""
    if len(body) > 2000:
        body = body[:2000] + "\n... [truncated]"

    attachments = email.get("attachments") or []

    data = {
        "message_id": email.get("message_id"),
        "subject": email.get("subject"),
        "sender_name": email.get("from_name"),
        "sender_email": email.get("from_email"),
        "received": email.get("received_datetime"),
        "is_read": email.get("is_read"),
        "body": body,
        "attachments": [
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "content_type": a.get("content_type"),
                "size": a.get("size"),
            }
            for a in attachments
        ],
        "classification": classification.get("type"),
        "classification_confidence": classification.get("confidence"),
        "classification_label": classification.get("label"),
    }
    return json.dumps(data, default=str)


@tool
def extract_email_attachment(
    message_id: str,
    attachment_id: Optional[str] = None,
) -> str:
    """Extract structured data from an email attachment using Azure Document Intelligence (OCR).

    Returns vendor name, bill number, dates, amounts, line items, and matched entities
    (vendor, project, payment term) with confidence scores.
    Use this after get_inbox_email_detail to extract invoice/bill data from PDF or image attachments.

    Args:
        message_id: The Graph API message ID of the email containing the attachment.
        attachment_id: Optional specific attachment ID to extract. If omitted, the first extractable attachment is used.
    """
    from entities.inbox.business.service import InboxService

    service = InboxService()
    result = service.extract_from_message(
        message_id=message_id,
        attachment_id=attachment_id,
    )

    if result.get("status_code") not in (200, 201):
        return json.dumps({"error": result.get("message", "Extraction failed.")})

    extraction = result.get("extraction", {})
    attachment = result.get("attachment", {})

    data = {
        "attachment": {
            "filename": attachment.get("filename"),
            "content_type": attachment.get("content_type"),
            "size": attachment.get("size"),
        },
        "extraction": extraction,
    }
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# Record Creation Tools
# ---------------------------------------------------------------------------

@tool
def create_bill_from_extraction(
    message_id: str,
    vendor_public_id: str,
    bill_number: str,
    bill_date: str,
    due_date: str,
    total_amount: Optional[float] = None,
    memo: Optional[str] = None,
    payment_term_public_id: Optional[str] = None,
    line_items: Optional[list] = None,
) -> str:
    """Create a draft bill from previously extracted email attachment data.

    IMPORTANT: Always show the user the extracted data and get their explicit confirmation before calling this tool.
    Creates a draft bill with line items and marks the email as processed.

    Args:
        message_id: The Graph API message ID of the source email (to mark as processed).
        vendor_public_id: Public ID of the vendor to assign the bill to.
        bill_number: The bill/invoice number.
        bill_date: Bill date in YYYY-MM-DD format.
        due_date: Due date in YYYY-MM-DD format.
        total_amount: Total bill amount.
        memo: Optional memo or description for the bill.
        payment_term_public_id: Optional public ID of the payment term.
        line_items: Line items for the bill. Each item: {description, amount, quantity?, project_public_id?, sub_cost_code_id?}
    """
    from decimal import Decimal
    from entities.bill.business.service import BillService
    from entities.bill_line_item.business.service import BillLineItemService
    from entities.inbox.business.service import InboxService

    bill_service = BillService()
    line_item_service = BillLineItemService()
    inbox_service = InboxService()

    bill = bill_service.create(
        vendor_public_id=vendor_public_id,
        bill_number=bill_number,
        bill_date=bill_date,
        due_date=due_date,
        total_amount=Decimal(str(total_amount)) if total_amount else None,
        memo=memo,
        payment_term_public_id=payment_term_public_id,
        is_draft=True,
    )

    created_line_items = []
    for li in line_items or []:
        item = line_item_service.create(
            bill_public_id=bill.public_id,
            sub_cost_code_id=li.get("sub_cost_code_id"),
            description=li.get("description"),
            amount=Decimal(str(li["amount"])) if li.get("amount") else None,
            quantity=li.get("quantity"),
            project_public_id=li.get("project_public_id"),
            is_draft=True,
        )
        created_line_items.append({
            "public_id": item.public_id,
            "description": li.get("description"),
            "amount": str(li.get("amount")),
        })

    inbox_service.mark_processed(
        message_id,
        record_type="bill",
        record_public_id=bill.public_id,
        processed_via="copilot",
    )

    data = {
        "success": True,
        "bill_public_id": bill.public_id,
        "bill_number": bill.bill_number,
        "is_draft": True,
        "line_items_created": len(created_line_items),
        "edit_url": f"/bill/{bill.public_id}/edit",
        "message": f"Draft bill {bill.bill_number} created with {len(created_line_items)} line item(s). Review and finalize at the edit page.",
    }
    return json.dumps(data, default=str)


@tool
def create_expense_from_extraction(
    message_id: str,
    vendor_public_id: str,
    reference_number: str,
    expense_date: str,
    total_amount: Optional[float] = None,
    memo: Optional[str] = None,
    line_items: Optional[list] = None,
) -> str:
    """Create a draft expense from previously extracted email attachment data.

    IMPORTANT: Always show the user the extracted data and get their explicit confirmation before calling this tool.
    Creates a draft expense with line items and marks the email as processed.

    Args:
        message_id: The Graph API message ID of the source email (to mark as processed).
        vendor_public_id: Public ID of the vendor to assign the expense to.
        reference_number: The expense reference number.
        expense_date: Expense date in YYYY-MM-DD format.
        total_amount: Total expense amount.
        memo: Optional memo or description for the expense.
        line_items: Line items for the expense. Each item: {description, amount, quantity?, project_public_id?}
    """
    from decimal import Decimal
    from entities.expense.business.service import ExpenseService
    from entities.expense_line_item.business.service import ExpenseLineItemService
    from entities.inbox.business.service import InboxService

    expense_service = ExpenseService()
    line_item_service = ExpenseLineItemService()
    inbox_service = InboxService()

    expense = expense_service.create(
        vendor_public_id=vendor_public_id,
        reference_number=reference_number,
        expense_date=expense_date,
        total_amount=Decimal(str(total_amount)) if total_amount else None,
        memo=memo,
        is_draft=True,
    )

    created_line_items = []
    for li in line_items or []:
        item = line_item_service.create(
            expense_public_id=expense.public_id,
            description=li.get("description"),
            amount=Decimal(str(li["amount"])) if li.get("amount") else None,
            quantity=li.get("quantity"),
            project_public_id=li.get("project_public_id"),
            is_draft=True,
        )
        created_line_items.append({
            "public_id": item.public_id,
            "description": li.get("description"),
            "amount": str(li.get("amount")),
        })

    inbox_service.mark_processed(
        message_id,
        record_type="expense",
        record_public_id=expense.public_id,
        processed_via="copilot",
    )

    data = {
        "success": True,
        "expense_public_id": expense.public_id,
        "reference_number": expense.reference_number,
        "is_draft": True,
        "line_items_created": len(created_line_items),
        "edit_url": f"/expense/{expense.public_id}/edit",
        "message": f"Draft expense {expense.reference_number} created with {len(created_line_items)} line item(s). Review and finalize at the edit page.",
    }
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# Email Action Tools
# ---------------------------------------------------------------------------

@tool
def forward_email_to_pm(
    message_id: str,
    pm_email: str,
    note: Optional[str] = None,
) -> str:
    """Forward an inbox email to a project manager for review or approval.

    Marks the email as read after forwarding.

    Args:
        message_id: The Graph API message ID of the email to forward.
        pm_email: The email address of the project manager to forward to.
        note: Optional note to include with the forwarded email.
    """
    from entities.inbox.business.service import InboxService

    service = InboxService()
    result = service.forward_to_pm(
        message_id=message_id,
        pm_email=pm_email,
        note=note,
    )

    if result.get("status_code") in (200, 201, 202):
        data = {"success": True, "message": f"Email forwarded to {pm_email}."}
    else:
        data = {"success": False, "error": result.get("message", "Failed to forward email.")}
    return json.dumps(data, default=str)


@tool
def skip_inbox_email(message_id: str) -> str:
    """Skip/dismiss an inbox email by marking it as read/processed.

    Use this for emails that don't require any action (e.g., statements, marketing, non-actionable inquiries).

    Args:
        message_id: The Graph API message ID of the email to skip.
    """
    from entities.inbox.business.service import InboxService

    service = InboxService()
    service.mark_processed(message_id, processed_via="copilot")

    data = {
        "success": True,
        "message": "Email marked as processed and will no longer appear in the unread inbox.",
    }
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# Compliance / Document Tools
# ---------------------------------------------------------------------------

@tool
def check_vendor_compliance(vendor_name: str) -> str:
    """Check the compliance workflow status for a specific vendor.

    Returns the vendor's workflow state, pending actions count, W9 satisfaction status,
    and a link to the admin workflow page.

    Args:
        vendor_name: The name of the vendor to check compliance for.
    """
    from entities.vendor.business.service import VendorService
    from workflows.workflow.persistence.repo import WorkflowRepository
    from core.ai.agents.base import AgentToolContext

    vendor_name = vendor_name.strip()
    tenant_id = AgentToolContext.tenant_id or 1

    vendor = VendorService().read_by_name(vendor_name)
    if not vendor or not vendor.id:
        return json.dumps({"error": f'No vendor found named "{vendor_name}". Check the spelling or try the exact name.'})

    repo = WorkflowRepository()
    workflows = repo.read_by_tenant_and_type(tenant_id=tenant_id, workflow_type="vendor_compliance")
    vendor_workflows = [w for w in workflows if getattr(w, "vendor_id", None) == vendor.id]
    latest = vendor_workflows[0] if vendor_workflows else None

    if not latest:
        data = {
            "vendor_name": vendor.name,
            "vendor_public_id": str(vendor.public_id),
            "compliance_status": "No compliance workflow found for this vendor.",
        }
        return json.dumps(data, default=str)

    state = getattr(latest, "state", None) or "unknown"
    ctx = getattr(latest, "context", None) or {}
    pending = ctx.get("pending_actions") or []
    w9_info = ctx.get("w9", {})

    data = {
        "vendor_name": vendor.name,
        "vendor_public_id": str(vendor.public_id),
        "workflow_public_id": latest.public_id,
        "workflow_state": state,
        "pending_actions_count": len(pending),
        "w9_satisfied": w9_info.get("satisfied"),
        "admin_url": f"/admin/workflow/{latest.public_id}",
    }
    return json.dumps(data, default=str)


@tool
def categorize_document(document_id: str) -> str:
    """Categorize a document using AI.

    Returns the detected category, confidence score, and reasoning. Requires a document public ID.

    Args:
        document_id: The public ID (UUID) of the document/attachment to categorize.
    """
    from entities.categorization.business.service import get_categorization_service

    service = get_categorization_service()
    result = service.categorize_attachment_by_public_id(document_id)

    if result:
        data = {
            "category": result.category.value if hasattr(result.category, "value") else str(result.category),
            "confidence": result.confidence,
            "reasoning": result.reasoning,
        }
    else:
        data = {"error": "Could not categorize the document. Make sure it exists and has been extracted."}
    return json.dumps(data, default=str)


@tool
def check_duplicates(document_id: str) -> str:
    """Check a document for duplicates or anomalies.

    Returns whether anomalies were detected, related documents, and match reasons.

    Args:
        document_id: The public ID (UUID) of the document/attachment to check.
    """
    from entities.anomaly.business.service import get_anomaly_service

    service = get_anomaly_service()
    result = service.check_attachment_by_public_id(document_id)

    if result and result.has_anomaly:
        related = []
        if result.related_documents:
            related = [
                {"filename": doc.filename, "match_reason": doc.match_reason}
                for doc in result.related_documents
            ]
        data = {
            "has_anomaly": True,
            "message": result.message,
            "related_documents": related,
        }
    else:
        data = {
            "has_anomaly": False,
            "message": "No duplicates or anomalies detected.",
        }
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

COPILOT_TOOLS = [
    search_documents,
    answer_question,
    get_system_status,
    list_bills,
    list_vendors,
    list_projects,
    list_expenses,
    list_invoices,
    list_inbox_emails,
    get_inbox_email_detail,
    extract_email_attachment,
    create_bill_from_extraction,
    create_expense_from_extraction,
    forward_email_to_pm,
    skip_inbox_email,
    check_vendor_compliance,
    categorize_document,
    check_duplicates,
]

TOOLS_BY_NAME = {t.name: t for t in COPILOT_TOOLS}
