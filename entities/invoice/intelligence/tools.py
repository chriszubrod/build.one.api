"""Agent-facing tools for the Invoice entity.

Invoice is a customer-facing invoice tied to a Project (NOT a Vendor).
Line items are POLYMORPHIC — InvoiceLineItem can reference a
BillLineItem, ExpenseLineItem, or BillCreditLineItem via a SourceType
discriminator. Volume: ~900 invoices but ~28K line items.

V1 deliberately omits line-item CRUD — same scope as bill_specialist.
The 'billable items for project' surface (`GET /get/invoice/billable-
items/{project_public_id}`) is also out of v1 — it's a workflow tool
that selects which Bill / Expense / BillCredit lines roll into a new
invoice, and that's a v2 invoicing-workflow tool set.

Read tools (no approval):
  search_invoices                → GET /api/v1/get/invoices?search=...
  read_invoice_by_public_id      → GET /api/v1/get/invoice/{public_id}

Write tools (user approval required):
  create_invoice                 → POST   /api/v1/create/invoice
  update_invoice                 → PUT    /api/v1/update/invoice/{public_id}
  delete_invoice                 → DELETE /api/v1/delete/invoice/{public_id}
  complete_invoice               → POST   /api/v1/complete/invoice/{public_id}

Tools self-register on import.
"""
from typing import Optional
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shapes ──────────────────────────────────────────────────────────

class _PublicIdArgs(BaseModel):
    public_id: str = Field(description="The Invoice's public_id (UUID).")


class _SearchArgs(BaseModel):
    query: Optional[str] = Field(
        default=None,
        description=(
            "Optional substring match against invoice_number / memo. "
            "Combine with project_id and is_draft to narrow."
        ),
    )
    project_id: Optional[int] = Field(
        default=None,
        description=(
            "Optional Project internal id (BIGINT) — restricts results "
            "to invoices for that project. Get the id from a prior "
            "Project read; do not surface this id in user text."
        ),
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description=(
            "Optional filter: true returns only draft invoices; false "
            "returns only completed; omit for all."
        ),
    )
    limit: int = Field(default=10, description="Max results (1-100).")


# ─── Read tools ──────────────────────────────────────────────────────────

async def _search_invoices(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _SearchArgs(**args)
    qs: dict = {"page": 1, "page_size": parsed.limit}
    if parsed.query:
        qs["search"] = parsed.query
    if parsed.project_id is not None:
        qs["project_id"] = parsed.project_id
    if parsed.is_draft is not None:
        qs["is_draft"] = "true" if parsed.is_draft else "false"
    return await ctx.call_api("GET", f"/api/v1/get/invoices?{urlencode(qs)}")


search_invoices = Tool(
    name="search_invoices",
    description=(
        "Find invoices via server-side search + filters. Catalog is "
        "small (~900 rows) but search-first keeps context tight. "
        "Combine `query` (substring on invoice_number / memo), "
        "`project_id` (specific project's invoices), and `is_draft` "
        "to narrow."
    ),
    input_schema=input_schema_from(_SearchArgs),
    handler=_search_invoices,
)


async def _read_invoice_by_public_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api("GET", f"/api/v1/get/invoice/{parsed.public_id}")


read_invoice_by_public_id = Tool(
    name="read_invoice_by_public_id",
    description=(
        "Fetch one invoice by its public_id (UUID). Use after "
        "`search_invoices` surfaces it, or when the user pastes a "
        "UUID directly. Response includes the parent project reference "
        "and any line items (which are polymorphic — they reference "
        "Bill / Expense / BillCredit line items via SourceType)."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_read_invoice_by_public_id,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateInvoiceArgs(BaseModel):
    project_public_id: str = Field(
        description=(
            "UUID of the parent Project. If the user names a project, "
            "search_projects first to resolve the UUID."
        ),
    )
    invoice_date: str = Field(
        description="Invoice date — ISO `YYYY-MM-DD`.",
    )
    due_date: str = Field(
        description="Due date — ISO `YYYY-MM-DD`.",
    )
    invoice_number: str = Field(
        description=(
            "Invoice number (<=50 chars). Often follows a per-project "
            "sequence; the existing `/get/invoice/next-number/{project_"
            "public_id}` endpoint can suggest one (not currently wired "
            "as an agent tool)."
        ),
    )
    payment_term_public_id: Optional[str] = Field(
        default=None,
        description="Optional UUID of a payment term.",
    )
    total_amount: Optional[float] = Field(
        default=None,
        description=(
            "Optional total amount. Often left blank at draft time — "
            "totals come from line items, added separately."
        ),
    )
    memo: Optional[str] = Field(default=None, description="Optional memo.")
    is_draft: Optional[bool] = Field(
        default=True,
        description=(
            "Defaults to true. Invoices get finalized later via "
            "`complete_invoice`."
        ),
    )


async def _create_invoice(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CreateInvoiceArgs(**args)
    return await ctx.call_api(
        "POST",
        "/api/v1/create/invoice",
        body=parsed.model_dump(exclude_none=False),
    )


def _summarize_create_invoice(args: dict) -> str:
    number = args.get("invoice_number") or "?"
    return f"Create draft invoice {number}"


create_invoice = Tool(
    name="create_invoice",
    description=(
        "Create a NEW DRAFT INVOICE with no line items. REQUIRES USER "
        "APPROVAL. The invoice becomes a draft (IsDraft=true) — line "
        "items are added separately (via the UI today; the project's "
        "billable bill/expense/credit lines roll into invoice lines "
        "via a workflow that's a v2 tool set). Once lines are in, use "
        "`complete_invoice` to finalize. If the user names a project, "
        "resolve via `search_projects` first."
    ),
    input_schema=input_schema_from(CreateInvoiceArgs),
    handler=_create_invoice,
    requires_approval=True,
    approval_summary=_summarize_create_invoice,
)


class UpdateInvoiceArgs(BaseModel):
    public_id: str = Field(description="UUID of the invoice to update.")
    row_version: str = Field(
        description=(
            "Base64 row version from your most recent read. Optimistic "
            "concurrency token — pass verbatim."
        ),
    )
    project_public_id: str = Field(
        description=(
            "UUID of the parent project. Required even when not "
            "changing project — pass the existing one verbatim."
        ),
    )
    invoice_date: str = Field(description="Invoice date (ISO `YYYY-MM-DD`).")
    due_date: str = Field(description="Due date (ISO `YYYY-MM-DD`).")
    invoice_number: str = Field(description="Invoice number (<=50 chars).")
    payment_term_public_id: Optional[str] = Field(default=None)
    total_amount: Optional[float] = Field(default=None)
    memo: Optional[str] = Field(default=None)
    is_draft: Optional[bool] = Field(
        default=None,
        description=(
            "Pass `false` to mark committed (NOT the same as completing "
            "— `complete_invoice` is the proper workflow). Leave unset "
            "to preserve."
        ),
    )


async def _update_invoice(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateInvoiceArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT", f"/api/v1/update/invoice/{parsed.public_id}", body=body
    )


def _summarize_update_invoice(args: dict) -> str:
    number = args.get("invoice_number") or "?"
    return f"Update invoice {number}"


update_invoice = Tool(
    name="update_invoice",
    description=(
        "Modify an existing invoice's PARENT fields. Line items are "
        "NOT changed by this tool. REQUIRES USER APPROVAL. Read the "
        "record first to get all required fields and `row_version`. "
        "Be explicit in prose about what's changing."
    ),
    input_schema=input_schema_from(UpdateInvoiceArgs),
    handler=_update_invoice,
    requires_approval=True,
    approval_summary=_summarize_update_invoice,
)


class DeleteInvoiceArgs(BaseModel):
    public_id: str = Field(description="UUID of the invoice to delete.")
    invoice_number: Optional[str] = Field(
        default=None,
        description="Invoice number — display hint for the approval card.",
    )
    project_name: Optional[str] = Field(
        default=None,
        description="Project name — display hint for the approval card.",
    )


async def _delete_invoice(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteInvoiceArgs(**args)
    return await ctx.call_api(
        "DELETE", f"/api/v1/delete/invoice/{parsed.public_id}"
    )


def _summarize_delete_invoice(args: dict) -> str:
    number = args.get("invoice_number")
    project = args.get("project_name")
    if number and project:
        return f"Delete invoice {number} ({project})"
    if number:
        return f"Delete invoice {number}"
    return f"Delete invoice {args.get('public_id') or '?'}"


delete_invoice = Tool(
    name="delete_invoice",
    description=(
        "Delete an invoice. REQUIRES USER APPROVAL. Look up the "
        "record first and pass `invoice_number` + `project_name` as "
        "display hints. WARN the user plainly if the invoice is not "
        "a draft — completed invoices may have been pushed to QBO + "
        "SharePoint already."
    ),
    input_schema=input_schema_from(DeleteInvoiceArgs),
    handler=_delete_invoice,
    requires_approval=True,
    approval_summary=_summarize_delete_invoice,
)


class CompleteInvoiceArgs(BaseModel):
    public_id: str = Field(description="UUID of the invoice to complete.")
    invoice_number: Optional[str] = Field(
        default=None,
        description="Invoice number — display hint for the approval card.",
    )
    project_name: Optional[str] = Field(
        default=None,
        description="Project name — display hint for the approval card.",
    )


async def _complete_invoice(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CompleteInvoiceArgs(**args)
    return await ctx.call_api(
        "POST", f"/api/v1/complete/invoice/{parsed.public_id}"
    )


def _summarize_complete_invoice(args: dict) -> str:
    number = args.get("invoice_number")
    project = args.get("project_name")
    if number and project:
        return f"Complete invoice {number} ({project}) — push to SharePoint + QBO"
    if number:
        return f"Complete invoice {number} — push to SharePoint + QBO"
    return f"Complete invoice {args.get('public_id') or '?'}"


complete_invoice = Tool(
    name="complete_invoice",
    description=(
        "Finalize a draft invoice: locks IsDraft=false, generates the "
        "invoice packet, uploads to SharePoint, and pushes to QBO + "
        "syncs the source bill/expense/credit lines' billed status. "
        "REQUIRES USER APPROVAL. Use this for the 'mark this invoice "
        "ready / push to QBO' workflow — do NOT just flip `is_draft` "
        "via update_invoice, that bypasses all the side effects."
    ),
    input_schema=input_schema_from(CompleteInvoiceArgs),
    handler=_complete_invoice,
    requires_approval=True,
    approval_summary=_summarize_complete_invoice,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    search_invoices,
    read_invoice_by_public_id,
    create_invoice,
    update_invoice,
    delete_invoice,
    complete_invoice,
):
    register(_tool)
