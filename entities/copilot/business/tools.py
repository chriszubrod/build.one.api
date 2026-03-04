# Python Standard Library Imports
import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool Definitions (Anthropic tool_use format)
# ---------------------------------------------------------------------------

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": "Search indexed documents using hybrid keyword and semantic search. Use this to find documents by content, vendor name, project name, or any other terms.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Can be a vendor name, keyword, phrase, or natural language description of what to find.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional filter by document category (bill, invoice, receipt, purchase_order, quote, etc.)",
                },
                "top": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "answer_question",
        "description": "Answer a natural language question by searching relevant documents and synthesizing an answer with source citations. Best for questions about document content, project costs, or vendor details found in uploaded documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to answer based on indexed documents.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter to narrow document search.",
                },
                "max_documents": {
                    "type": "integer",
                    "description": "Maximum number of documents to search (default 5, max 10).",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "get_system_status",
        "description": "Get the current system status including counts of documents pending extraction and categorization. Use this when users ask about system health, pending work, or what needs attention.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_bills",
        "description": "Query bills (accounts payable invoices FROM vendors). Returns bill records with bill number, vendor, dates, amounts, and status. Supports filtering by search term, date range, and draft status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Search term to filter bills (searches bill number, vendor name, memo).",
                },
                "start_date": {
                    "type": "string",
                    "description": "Filter bills on or after this date (YYYY-MM-DD format).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Filter bills on or before this date (YYYY-MM-DD format).",
                },
                "is_draft": {
                    "type": "boolean",
                    "description": "Filter by draft status. True for drafts only, false for finalized only, omit for all.",
                },
                "page": {
                    "type": "integer",
                    "description": "Page number for pagination (default 1).",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results per page (default 10, max 50).",
                },
            },
        },
    },
    {
        "name": "list_vendors",
        "description": "List all vendors. Returns vendor names, public IDs, and draft status.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_projects",
        "description": "List all projects. Returns project names, abbreviations, descriptions, and statuses.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_expenses",
        "description": "Query expenses (direct purchases, not billed through vendors). Returns expense records with reference numbers, vendor, dates, amounts, and status. Supports filtering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Search term to filter expenses.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Filter expenses on or after this date (YYYY-MM-DD format).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Filter expenses on or before this date (YYYY-MM-DD format).",
                },
                "is_draft": {
                    "type": "boolean",
                    "description": "Filter by draft status.",
                },
                "page": {
                    "type": "integer",
                    "description": "Page number for pagination (default 1).",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results per page (default 10, max 50).",
                },
            },
        },
    },
    {
        "name": "list_invoices",
        "description": "Query invoices (accounts receivable documents TO customers). Returns invoice records with invoice number, project, dates, amounts, and status. Supports filtering.",
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Search term to filter invoices.",
                },
                "start_date": {
                    "type": "string",
                    "description": "Filter invoices on or after this date (YYYY-MM-DD format).",
                },
                "end_date": {
                    "type": "string",
                    "description": "Filter invoices on or before this date (YYYY-MM-DD format).",
                },
                "is_draft": {
                    "type": "boolean",
                    "description": "Filter by draft status.",
                },
                "page": {
                    "type": "integer",
                    "description": "Page number for pagination (default 1).",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Number of results per page (default 10, max 50).",
                },
            },
        },
    },
    {
        "name": "list_inbox_emails",
        "description": "List emails from the invoice inbox. Returns email subjects, senders, dates, and AI classifications (bill, expense, vendor_credit, inquiry, statement, unknown). Use this to show pending or recent inbox items.",
        "input_schema": {
            "type": "object",
            "properties": {
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, only return unread emails (default false).",
                },
                "top": {
                    "type": "integer",
                    "description": "Maximum number of emails to return (default 20).",
                },
            },
        },
    },
    {
        "name": "get_inbox_email_detail",
        "description": "Get full details of a specific inbox email including body content, attachments, and AI classification. Use this after list_inbox_emails to show a user the details of a particular email before processing it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The Graph API message ID of the email to retrieve.",
                },
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "extract_email_attachment",
        "description": "Extract structured data from an email attachment using Azure Document Intelligence (OCR). Returns vendor name, bill number, dates, amounts, line items, and matched entities (vendor, project, payment term) with confidence scores. Use this after get_inbox_email_detail to extract invoice/bill data from PDF or image attachments.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The Graph API message ID of the email containing the attachment.",
                },
                "attachment_id": {
                    "type": "string",
                    "description": "Optional specific attachment ID to extract. If omitted, the first extractable attachment (PDF/image) is used.",
                },
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "create_bill_from_extraction",
        "description": "Create a draft bill from previously extracted email attachment data. IMPORTANT: Always show the user the extracted data and get their confirmation before calling this tool. Creates a draft bill with line items and marks the email as processed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The Graph API message ID of the source email (to mark as processed).",
                },
                "vendor_public_id": {
                    "type": "string",
                    "description": "Public ID of the vendor to assign the bill to.",
                },
                "bill_number": {
                    "type": "string",
                    "description": "The bill/invoice number.",
                },
                "bill_date": {
                    "type": "string",
                    "description": "Bill date in YYYY-MM-DD format.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Due date in YYYY-MM-DD format.",
                },
                "total_amount": {
                    "type": "number",
                    "description": "Total bill amount.",
                },
                "memo": {
                    "type": "string",
                    "description": "Optional memo or description for the bill.",
                },
                "payment_term_public_id": {
                    "type": "string",
                    "description": "Optional public ID of the payment term.",
                },
                "line_items": {
                    "type": "array",
                    "description": "Line items for the bill.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {
                                "type": "string",
                                "description": "Line item description.",
                            },
                            "amount": {
                                "type": "number",
                                "description": "Line item amount.",
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "Quantity (optional).",
                            },
                            "project_public_id": {
                                "type": "string",
                                "description": "Optional project public ID for this line item.",
                            },
                        },
                        "required": ["description", "amount"],
                    },
                },
            },
            "required": ["message_id", "vendor_public_id", "bill_number", "bill_date", "due_date"],
        },
    },
    {
        "name": "create_expense_from_extraction",
        "description": "Create a draft expense from previously extracted email attachment data. IMPORTANT: Always show the user the extracted data and get their confirmation before calling this tool. Creates a draft expense with line items and marks the email as processed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The Graph API message ID of the source email (to mark as processed).",
                },
                "vendor_public_id": {
                    "type": "string",
                    "description": "Public ID of the vendor to assign the expense to.",
                },
                "reference_number": {
                    "type": "string",
                    "description": "The expense reference number.",
                },
                "expense_date": {
                    "type": "string",
                    "description": "Expense date in YYYY-MM-DD format.",
                },
                "total_amount": {
                    "type": "number",
                    "description": "Total expense amount.",
                },
                "memo": {
                    "type": "string",
                    "description": "Optional memo or description for the expense.",
                },
                "line_items": {
                    "type": "array",
                    "description": "Line items for the expense.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {
                                "type": "string",
                                "description": "Line item description.",
                            },
                            "amount": {
                                "type": "number",
                                "description": "Line item amount.",
                            },
                            "quantity": {
                                "type": "integer",
                                "description": "Quantity (optional).",
                            },
                            "project_public_id": {
                                "type": "string",
                                "description": "Optional project public ID for this line item.",
                            },
                        },
                        "required": ["description", "amount"],
                    },
                },
            },
            "required": ["message_id", "vendor_public_id", "reference_number", "expense_date"],
        },
    },
    {
        "name": "forward_email_to_pm",
        "description": "Forward an inbox email to a project manager for review or approval. Marks the email as read after forwarding.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The Graph API message ID of the email to forward.",
                },
                "pm_email": {
                    "type": "string",
                    "description": "The email address of the project manager to forward to.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional note to include with the forwarded email. Defaults to a polite review request.",
                },
            },
            "required": ["message_id", "pm_email"],
        },
    },
    {
        "name": "skip_inbox_email",
        "description": "Skip/dismiss an inbox email by marking it as read/processed. Use this for emails that don't require any action (e.g., statements, marketing, non-actionable inquiries).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The Graph API message ID of the email to skip.",
                },
            },
            "required": ["message_id"],
        },
    },
    {
        "name": "check_vendor_compliance",
        "description": "Check the compliance workflow status for a specific vendor. Returns the vendor's workflow state, pending actions count, W9 satisfaction status, and a link to the admin workflow page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vendor_name": {
                    "type": "string",
                    "description": "The name of the vendor to check compliance for.",
                },
            },
            "required": ["vendor_name"],
        },
    },
    {
        "name": "categorize_document",
        "description": "Categorize a document using AI. Returns the detected category, confidence score, and reasoning. Requires a document public ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The public ID (UUID) of the document/attachment to categorize.",
                },
            },
            "required": ["document_id"],
        },
    },
    {
        "name": "check_duplicates",
        "description": "Check a document for duplicates or anomalies. Returns whether anomalies were detected, related documents, and match reasons. Requires a document public ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {
                    "type": "string",
                    "description": "The public ID (UUID) of the document/attachment to check.",
                },
            },
            "required": ["document_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool Executor Dispatch
# ---------------------------------------------------------------------------

def execute_tool(tool_name: str, tool_input: dict, context: dict) -> str:
    """Execute a tool by name and return the JSON-serialized result string."""
    handlers = {
        "search_documents": _exec_search_documents,
        "answer_question": _exec_answer_question,
        "get_system_status": _exec_get_system_status,
        "list_bills": _exec_list_bills,
        "list_vendors": _exec_list_vendors,
        "list_projects": _exec_list_projects,
        "list_expenses": _exec_list_expenses,
        "list_invoices": _exec_list_invoices,
        "list_inbox_emails": _exec_list_inbox_emails,
        "get_inbox_email_detail": _exec_get_inbox_email_detail,
        "extract_email_attachment": _exec_extract_email_attachment,
        "create_bill_from_extraction": _exec_create_bill_from_extraction,
        "create_expense_from_extraction": _exec_create_expense_from_extraction,
        "forward_email_to_pm": _exec_forward_email_to_pm,
        "skip_inbox_email": _exec_skip_inbox_email,
        "check_vendor_compliance": _exec_check_vendor_compliance,
        "categorize_document": _exec_categorize_document,
        "check_duplicates": _exec_check_duplicates,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    try:
        result = handler(tool_input, context)
        return json.dumps(result, default=str)
    except Exception as e:
        logger.error(f"Tool execution error ({tool_name}): {e}", exc_info=True)
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Individual Tool Executors
# ---------------------------------------------------------------------------

def _exec_search_documents(tool_input: dict, context: dict) -> dict:
    from entities.search.business.service import get_search_service

    service = get_search_service()
    results = service.hybrid_search(
        query=tool_input["query"],
        category=tool_input.get("category"),
        top=tool_input.get("top", 5),
    )

    return {
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


def _exec_answer_question(tool_input: dict, context: dict) -> dict:
    from entities.qa.business.service import get_qa_service

    service = get_qa_service()
    result = service.ask(
        question=tool_input["question"],
        category=tool_input.get("category"),
        max_documents=tool_input.get("max_documents", 5),
    )

    sources = []
    if result.get("sources"):
        sources = [
            {"filename": s.get("filename"), "public_id": s.get("public_id")}
            for s in result["sources"][:3]
        ]

    return {
        "answer": result.get("answer", "No answer found."),
        "sources": sources,
    }


def _exec_get_system_status(tool_input: dict, context: dict) -> dict:
    from entities.attachment.persistence.repo import AttachmentRepository

    repo = AttachmentRepository()
    pending_ext = repo.read_pending_extraction()
    pending_cat = repo.read_pending_categorization(limit=10)

    return {
        "pending_extraction": len(pending_ext),
        "pending_categorization": len(pending_cat),
        "recent_uncategorized": [
            {"filename": doc.original_filename or doc.filename}
            for doc in pending_cat[:5]
        ],
    }


def _exec_list_bills(tool_input: dict, context: dict) -> dict:
    from entities.bill.business.service import BillService

    service = BillService()
    page = tool_input.get("page", 1)
    page_size = min(tool_input.get("page_size", 10), 50)

    bills = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=tool_input.get("search_term"),
        start_date=tool_input.get("start_date"),
        end_date=tool_input.get("end_date"),
        is_draft=tool_input.get("is_draft"),
    )
    total = service.count(
        search_term=tool_input.get("search_term"),
        start_date=tool_input.get("start_date"),
        end_date=tool_input.get("end_date"),
        is_draft=tool_input.get("is_draft"),
    )

    return {
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


def _exec_list_vendors(tool_input: dict, context: dict) -> dict:
    from entities.vendor.business.service import VendorService

    service = VendorService()
    vendors = service.read_all()

    return {
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


def _exec_list_projects(tool_input: dict, context: dict) -> dict:
    from entities.project.business.service import ProjectService

    service = ProjectService()
    projects = service.read_all()

    return {
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


def _exec_list_expenses(tool_input: dict, context: dict) -> dict:
    from entities.expense.business.service import ExpenseService

    service = ExpenseService()
    page = tool_input.get("page", 1)
    page_size = min(tool_input.get("page_size", 10), 50)

    expenses = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=tool_input.get("search_term"),
        start_date=tool_input.get("start_date"),
        end_date=tool_input.get("end_date"),
        is_draft=tool_input.get("is_draft"),
    )
    total = service.count(
        search_term=tool_input.get("search_term"),
        start_date=tool_input.get("start_date"),
        end_date=tool_input.get("end_date"),
        is_draft=tool_input.get("is_draft"),
    )

    return {
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


def _exec_list_invoices(tool_input: dict, context: dict) -> dict:
    from entities.invoice.business.service import InvoiceService

    service = InvoiceService()
    page = tool_input.get("page", 1)
    page_size = min(tool_input.get("page_size", 10), 50)

    invoices = service.read_paginated(
        page_number=page,
        page_size=page_size,
        search_term=tool_input.get("search_term"),
        start_date=tool_input.get("start_date"),
        end_date=tool_input.get("end_date"),
        is_draft=tool_input.get("is_draft"),
    )
    total = service.count(
        search_term=tool_input.get("search_term"),
        start_date=tool_input.get("start_date"),
        end_date=tool_input.get("end_date"),
        is_draft=tool_input.get("is_draft"),
    )

    return {
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


def _exec_list_inbox_emails(tool_input: dict, context: dict) -> dict:
    from entities.inbox.business.service import InboxService

    service = InboxService()
    result = service.list_inbox(
        top=tool_input.get("top", 20),
        unread_only=tool_input.get("unread_only", False),
    )

    messages = result.get("messages", [])
    return {
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


def _exec_get_inbox_email_detail(tool_input: dict, context: dict) -> dict:
    from entities.inbox.business.service import InboxService

    service = InboxService()
    result = service.get_message_detail(message_id=tool_input["message_id"])

    if result.get("status_code") not in (200, 201):
        return {"error": result.get("message", "Failed to retrieve email details.")}

    email = result.get("email", {})
    classification = result.get("classification", {})

    # Truncate body to avoid blowing up context
    body = email.get("body_content") or email.get("body_preview") or ""
    if len(body) > 2000:
        body = body[:2000] + "\n... [truncated]"

    attachments = email.get("attachments") or []

    return {
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


def _exec_extract_email_attachment(tool_input: dict, context: dict) -> dict:
    from entities.inbox.business.service import InboxService

    service = InboxService()
    result = service.extract_from_message(
        message_id=tool_input["message_id"],
        attachment_id=tool_input.get("attachment_id"),
    )

    if result.get("status_code") not in (200, 201):
        return {"error": result.get("message", "Extraction failed.")}

    extraction = result.get("extraction", {})
    attachment = result.get("attachment", {})

    return {
        "attachment": {
            "filename": attachment.get("filename"),
            "content_type": attachment.get("content_type"),
            "size": attachment.get("size"),
        },
        "extraction": extraction,
    }


def _exec_create_bill_from_extraction(tool_input: dict, context: dict) -> dict:
    from decimal import Decimal
    from entities.bill.business.service import BillService
    from entities.bill_line_item.business.service import BillLineItemService
    from entities.inbox.business.service import InboxService

    bill_service = BillService()
    line_item_service = BillLineItemService()
    inbox_service = InboxService()

    bill = bill_service.create(
        vendor_public_id=tool_input["vendor_public_id"],
        bill_number=tool_input["bill_number"],
        bill_date=tool_input["bill_date"],
        due_date=tool_input["due_date"],
        total_amount=Decimal(str(tool_input["total_amount"])) if tool_input.get("total_amount") else None,
        memo=tool_input.get("memo"),
        payment_term_public_id=tool_input.get("payment_term_public_id"),
        is_draft=True,
    )

    created_line_items = []
    for li in tool_input.get("line_items") or []:
        item = line_item_service.create(
            bill_public_id=bill.public_id,
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

    # Mark the source email as processed and persist outcome for ML training
    inbox_service.mark_processed(
        tool_input["message_id"],
        record_type="bill",
        record_public_id=bill.public_id,
        processed_via="copilot",
    )

    return {
        "success": True,
        "bill_public_id": bill.public_id,
        "bill_number": bill.bill_number,
        "is_draft": True,
        "line_items_created": len(created_line_items),
        "edit_url": f"/bill/{bill.public_id}/edit",
        "message": f"Draft bill {bill.bill_number} created with {len(created_line_items)} line item(s). Review and finalize at the edit page.",
    }


def _exec_create_expense_from_extraction(tool_input: dict, context: dict) -> dict:
    from decimal import Decimal
    from entities.expense.business.service import ExpenseService
    from entities.expense_line_item.business.service import ExpenseLineItemService
    from entities.inbox.business.service import InboxService

    expense_service = ExpenseService()
    line_item_service = ExpenseLineItemService()
    inbox_service = InboxService()

    expense = expense_service.create(
        vendor_public_id=tool_input["vendor_public_id"],
        reference_number=tool_input["reference_number"],
        expense_date=tool_input["expense_date"],
        total_amount=Decimal(str(tool_input["total_amount"])) if tool_input.get("total_amount") else None,
        memo=tool_input.get("memo"),
        is_draft=True,
    )

    created_line_items = []
    for li in tool_input.get("line_items") or []:
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

    # Mark the source email as processed and persist outcome for ML training
    inbox_service.mark_processed(
        tool_input["message_id"],
        record_type="expense",
        record_public_id=expense.public_id,
        processed_via="copilot",
    )

    return {
        "success": True,
        "expense_public_id": expense.public_id,
        "reference_number": expense.reference_number,
        "is_draft": True,
        "line_items_created": len(created_line_items),
        "edit_url": f"/expense/{expense.public_id}/edit",
        "message": f"Draft expense {expense.reference_number} created with {len(created_line_items)} line item(s). Review and finalize at the edit page.",
    }


def _exec_forward_email_to_pm(tool_input: dict, context: dict) -> dict:
    from entities.inbox.business.service import InboxService

    service = InboxService()
    result = service.forward_to_pm(
        message_id=tool_input["message_id"],
        pm_email=tool_input["pm_email"],
        note=tool_input.get("note"),
    )

    if result.get("status_code") in (200, 201, 202):
        return {
            "success": True,
            "message": f"Email forwarded to {tool_input['pm_email']}.",
        }
    return {
        "success": False,
        "error": result.get("message", "Failed to forward email."),
    }


def _exec_skip_inbox_email(tool_input: dict, context: dict) -> dict:
    from entities.inbox.business.service import InboxService

    service = InboxService()
    service.mark_processed(
        tool_input["message_id"],
        processed_via="copilot",
    )

    return {
        "success": True,
        "message": "Email marked as processed and will no longer appear in the unread inbox.",
    }


def _exec_check_vendor_compliance(tool_input: dict, context: dict) -> dict:
    from entities.vendor.business.service import VendorService
    from workflows.workflow.persistence.repo import WorkflowRepository

    vendor_name = tool_input["vendor_name"].strip()
    tenant_id = context.get("tenant_id", 1)

    vendor = VendorService().read_by_name(vendor_name)
    if not vendor or not vendor.id:
        return {"error": f"No vendor found named \"{vendor_name}\". Check the spelling or try the exact name."}

    repo = WorkflowRepository()
    workflows = repo.read_by_tenant_and_type(tenant_id=tenant_id, workflow_type="vendor_compliance")
    vendor_workflows = [w for w in workflows if getattr(w, "vendor_id", None) == vendor.id]
    latest = vendor_workflows[0] if vendor_workflows else None

    if not latest:
        return {
            "vendor_name": vendor.name,
            "vendor_public_id": str(vendor.public_id),
            "compliance_status": "No compliance workflow found for this vendor.",
        }

    state = getattr(latest, "state", None) or "unknown"
    ctx = getattr(latest, "context", None) or {}
    pending = ctx.get("pending_actions") or []
    w9_info = ctx.get("w9", {})

    return {
        "vendor_name": vendor.name,
        "vendor_public_id": str(vendor.public_id),
        "workflow_public_id": latest.public_id,
        "workflow_state": state,
        "pending_actions_count": len(pending),
        "w9_satisfied": w9_info.get("satisfied"),
        "admin_url": f"/admin/workflow/{latest.public_id}",
    }


def _exec_categorize_document(tool_input: dict, context: dict) -> dict:
    from entities.categorization.business.service import get_categorization_service

    service = get_categorization_service()
    result = service.categorize_attachment_by_public_id(tool_input["document_id"])

    if result:
        return {
            "category": result.category.value if hasattr(result.category, "value") else str(result.category),
            "confidence": result.confidence,
            "reasoning": result.reasoning,
        }
    return {"error": "Could not categorize the document. Make sure it exists and has been extracted."}


def _exec_check_duplicates(tool_input: dict, context: dict) -> dict:
    from entities.anomaly.business.service import get_anomaly_service

    service = get_anomaly_service()
    result = service.check_attachment_by_public_id(tool_input["document_id"])

    if result and result.has_anomaly:
        related = []
        if result.related_documents:
            related = [
                {"filename": doc.filename, "match_reason": doc.match_reason}
                for doc in result.related_documents
            ]
        return {
            "has_anomaly": True,
            "message": result.message,
            "related_documents": related,
        }
    return {
        "has_anomaly": False,
        "message": "No duplicates or anomalies detected.",
    }
