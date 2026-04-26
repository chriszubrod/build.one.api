"""Agent-facing tools for the Invoice entity.

Invoice is a customer-facing invoice tied to a Project (NOT a Vendor).
Line items are POLYMORPHIC — InvoiceLineItem can reference a
BillLineItem, ExpenseLineItem, or BillCreditLineItem via a SourceType
discriminator. Volume: ~900 invoices but ~28K line items.

V2 (this revision) wires the full invoice-packet workflow:

Read tools (no approval):
  search_invoices                → GET /api/v1/get/invoices?search=...
  read_invoice_by_public_id      → GET /api/v1/get/invoice/{public_id}
  get_billable_items_for_invoice → GET /api/v1/get/invoice/billable-items/{project_public_id}
  get_next_invoice_number        → GET /api/v1/get/invoice/next-number/{project_public_id}
  reconcile_invoice              → GET /api/v1/get/invoice/{public_id}/reconcile

Write tools (user approval required):
  create_invoice                 → POST   /api/v1/create/invoice
  update_invoice                 → PUT    /api/v1/update/invoice/{public_id}
  delete_invoice                 → DELETE /api/v1/delete/invoice/{public_id}
  add_invoice_line_items         → POST   /api/v1/create/invoice_line_item (one per line, batched in handler)
  update_invoice_line_item       → PUT    /api/v1/update/invoice_line_item/{public_id}
  remove_invoice_line_item       → DELETE /api/v1/delete/invoice_line_item/{public_id}
  generate_invoice_packet        → POST   /api/v1/generate/invoice/{public_id}/packet
  complete_invoice               → POST   /api/v1/complete/invoice/{public_id}
                                   (Server-side: regenerates packet,
                                   uploads to SharePoint with overwrite,
                                   syncs Excel DRAW REQUEST column.)

Tools self-register on import.
"""
import json

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
        "invoice packet, uploads to SharePoint with overwrite, and "
        "syncs the Excel DRAW REQUEST column. REQUIRES USER APPROVAL. "
        "Use this for the 'mark this invoice ready / push to QBO' "
        "workflow — do NOT just flip `is_draft` via update_invoice, "
        "that bypasses all the side effects. Re-runs are idempotent: "
        "the packet is regenerated (old packet attachment + blob "
        "deleted first), supporting PDFs overwrite, Excel cells "
        "rewrite. QBO push is currently disabled."
    ),
    input_schema=input_schema_from(CompleteInvoiceArgs),
    handler=_complete_invoice,
    requires_approval=True,
    approval_summary=_summarize_complete_invoice,
)


# ─── Workflow tools (V2) ─────────────────────────────────────────────────

class _BillableItemsArgs(BaseModel):
    project_public_id: str = Field(
        description=(
            "UUID of the Project whose unbilled, billable line items "
            "we want to roll up."
        ),
    )
    invoice_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional UUID of an in-progress invoice. When set, lines "
            "already attached to that invoice are excluded from the "
            "candidates so the user only picks from net-new items."
        ),
    )


async def _get_billable_items_for_invoice(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _BillableItemsArgs(**args)
    path = f"/api/v1/get/invoice/billable-items/{parsed.project_public_id}"
    if parsed.invoice_public_id:
        path += f"?invoice_public_id={parsed.invoice_public_id}"
    return await ctx.call_api("GET", path)


get_billable_items_for_invoice = Tool(
    name="get_billable_items_for_invoice",
    description=(
        "List unbilled, billable line items (Bill / Expense / "
        "BillCredit) for a project — these are the candidates that can "
        "roll into a new invoice. Returns `{ready: [...], draft: [...]}`. "
        "Each item carries `source_type`, `source_id` (BIGINT), "
        "`description`, `amount`, `markup`, `price`, plus parent metadata "
        "(vendor name, parent number, source date) for display. Pass "
        "`invoice_public_id` if you already have a draft invoice in "
        "progress — items already on that invoice will be excluded."
    ),
    input_schema=input_schema_from(_BillableItemsArgs),
    handler=_get_billable_items_for_invoice,
)


class _NextInvoiceNumberArgs(BaseModel):
    project_public_id: str = Field(
        description="UUID of the Project to get the next sequential invoice number for.",
    )


async def _get_next_invoice_number(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _NextInvoiceNumberArgs(**args)
    return await ctx.call_api(
        "GET",
        f"/api/v1/get/invoice/next-number/{parsed.project_public_id}",
    )


get_next_invoice_number = Tool(
    name="get_next_invoice_number",
    description=(
        "Suggest the next sequential invoice number for a project. "
        "Use before proposing `create_invoice` so the new invoice "
        "follows the project's numbering convention without the user "
        "having to invent one."
    ),
    input_schema=input_schema_from(_NextInvoiceNumberArgs),
    handler=_get_next_invoice_number,
)


async def _reconcile_invoice(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/invoice/{parsed.public_id}/reconcile"
    )


reconcile_invoice = Tool(
    name="reconcile_invoice",
    description=(
        "Compare invoice line items against the project's Budget "
        "Tracker worksheet — surfaces unbilled rows in the worksheet "
        "that AREN'T on this invoice (likely missed) and lines on "
        "this invoice that don't match a worksheet row (manual lines "
        "or mismatches). Use as a final check before `complete_invoice`."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_reconcile_invoice,
)


class _AddInvoiceLineItemSpec(BaseModel):
    """One line to add. Matches a single billable-items row's identity
    + verbatim source values; the agent should pull these directly from
    `get_billable_items_for_invoice` rather than inventing them."""
    source_type: str = Field(
        description=(
            "One of `BillLineItem`, `ExpenseLineItem`, `BillCreditLineItem`. "
            "Manual lines aren't supported via this tool — those need "
            "the existing UI."
        ),
    )
    source_id: int = Field(
        description=(
            "Internal BIGINT id of the source line. Get from the "
            "`source_id` field on a billable-items row."
        ),
    )
    description: Optional[str] = Field(default=None)
    amount: Optional[float] = Field(default=None)
    markup: Optional[float] = Field(default=None)
    price: Optional[float] = Field(default=None)


class AddInvoiceLineItemsArgs(BaseModel):
    invoice_public_id: str = Field(
        description="UUID of the (typically draft) invoice to add lines to.",
    )
    items: list[_AddInvoiceLineItemSpec] = Field(
        description=(
            "List of source line specs to roll into this invoice. "
            "Pull each spec verbatim from `get_billable_items_for_invoice`. "
            "Do NOT override the source values — if the user wants "
            "different numbers, they should edit the source line first "
            "(via the Bill/Expense/BillCredit specialist) and re-run."
        ),
    )


_SOURCE_TYPE_TO_FK_FIELD = {
    "BillLineItem": "bill_line_item_id",
    "ExpenseLineItem": "expense_line_item_id",
    "BillCreditLineItem": "bill_credit_line_item_id",
}


async def _add_invoice_line_items(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = AddInvoiceLineItemsArgs(**args)
    if not parsed.items:
        return ToolResult(
            content="No items provided — nothing to add.",
            is_error=True,
        )
    created: list[dict] = []
    errors: list[dict] = []
    for idx, spec in enumerate(parsed.items):
        fk_field = _SOURCE_TYPE_TO_FK_FIELD.get(spec.source_type)
        if not fk_field:
            errors.append({
                "index": idx,
                "source_type": spec.source_type,
                "error": f"Unsupported source_type {spec.source_type!r}.",
            })
            continue
        body = {
            "invoice_public_id": parsed.invoice_public_id,
            "source_type": spec.source_type,
            fk_field: spec.source_id,
            "description": spec.description,
            "amount": spec.amount,
            "markup": spec.markup,
            "price": spec.price,
            "is_draft": True,
        }
        result = await ctx.call_api(
            "POST",
            "/api/v1/create/invoice_line_item",
            body=body,
        )
        if result.is_error:
            errors.append({
                "index": idx,
                "source_type": spec.source_type,
                "source_id": spec.source_id,
                "error": str(result.content)[:300],
            })
        else:
            created.append({
                "index": idx,
                "source_type": spec.source_type,
                "source_id": spec.source_id,
                "result": result.content,
            })
    summary = {
        "added": len(created),
        "failed": len(errors),
        "created": created,
        "errors": errors,
    }
    return ToolResult(
        content=json.dumps(summary),
        is_error=bool(errors and not created),
    )


def _summarize_add_invoice_line_items(args: dict) -> str:
    items = args.get("items") or []
    return f"Add {len(items)} line item(s) to invoice"


add_invoice_line_items = Tool(
    name="add_invoice_line_items",
    description=(
        "Roll selected billable items into an existing draft invoice. "
        "REQUIRES USER APPROVAL (one card per batch, the user approves "
        "the whole set together). Each item is created as an "
        "InvoiceLineItem referencing its source via SourceType + the "
        "matching FK; values are copied verbatim from the source line. "
        "**No overrides** — if the user wants different description / "
        "amount / markup / price, the SOURCE line needs to be edited "
        "first (via the appropriate specialist), then re-run this. "
        "Returns a summary of added vs. failed; if some succeeded and "
        "some failed (partial), retry with just the failed items rather "
        "than re-running the whole batch."
    ),
    input_schema=input_schema_from(AddInvoiceLineItemsArgs),
    handler=_add_invoice_line_items,
    requires_approval=True,
    approval_summary=_summarize_add_invoice_line_items,
)


class UpdateInvoiceLineItemArgs(BaseModel):
    public_id: str = Field(description="UUID of the invoice line item to update.")
    row_version: str = Field(
        description="Base64 row version from your most recent read. Pass verbatim.",
    )
    invoice_public_id: str = Field(
        description="UUID of the parent invoice — required by the API even when not changing it.",
    )
    description: Optional[str] = Field(default=None)
    amount: Optional[float] = Field(default=None)
    markup: Optional[float] = Field(default=None)
    price: Optional[float] = Field(default=None)


async def _update_invoice_line_item(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateInvoiceLineItemArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT",
        f"/api/v1/update/invoice_line_item/{parsed.public_id}",
        body=body,
    )


def _summarize_update_invoice_line_item(args: dict) -> str:
    return f"Update invoice line item {args.get('public_id') or '?'}"


update_invoice_line_item = Tool(
    name="update_invoice_line_item",
    description=(
        "Edit a single InvoiceLineItem (description / amount / markup "
        "/ price). REQUIRES USER APPROVAL. Read the line item first "
        "to get `row_version`. Use sparingly — the canonical pattern "
        "is to edit the SOURCE line via its specialist and re-roll. "
        "This tool is for cases where the user wants the invoice copy "
        "different from the source on purpose (e.g. one-off discount)."
    ),
    input_schema=input_schema_from(UpdateInvoiceLineItemArgs),
    handler=_update_invoice_line_item,
    requires_approval=True,
    approval_summary=_summarize_update_invoice_line_item,
)


class RemoveInvoiceLineItemArgs(BaseModel):
    public_id: str = Field(description="UUID of the invoice line item to remove.")
    description: Optional[str] = Field(
        default=None,
        description="Description — display hint for the approval card.",
    )


async def _remove_invoice_line_item(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = RemoveInvoiceLineItemArgs(**args)
    return await ctx.call_api(
        "DELETE",
        f"/api/v1/delete/invoice_line_item/{parsed.public_id}",
    )


def _summarize_remove_invoice_line_item(args: dict) -> str:
    desc = args.get("description")
    return f"Remove invoice line — {desc}" if desc else f"Remove invoice line {args.get('public_id') or '?'}"


remove_invoice_line_item = Tool(
    name="remove_invoice_line_item",
    description=(
        "Drop a single line from an invoice. REQUIRES USER APPROVAL. "
        "Removing the InvoiceLineItem unlinks the source — the source "
        "line item itself isn't deleted; it just becomes billable "
        "again. Pass `description` as a display hint for the card."
    ),
    input_schema=input_schema_from(RemoveInvoiceLineItemArgs),
    handler=_remove_invoice_line_item,
    requires_approval=True,
    approval_summary=_summarize_remove_invoice_line_item,
)


async def _generate_invoice_packet(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "POST", f"/api/v1/generate/invoice/{parsed.public_id}/packet"
    )


generate_invoice_packet = Tool(
    name="generate_invoice_packet",
    description=(
        "Generate the invoice's PDF packet (TOC pages + merged line "
        "item attachments). REQUIRES USER APPROVAL. Server replaces "
        "the previous packet attachment cleanly (deletes old blob + "
        "Attachment row first). Useful for 'preview the packet "
        "before completing' workflows; `complete_invoice` regenerates "
        "the packet itself, so you don't need to call both back-to-back."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_generate_invoice_packet,
    requires_approval=True,
    approval_summary=lambda args: f"Generate packet for invoice {args.get('public_id') or '?'}",
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    search_invoices,
    read_invoice_by_public_id,
    get_billable_items_for_invoice,
    get_next_invoice_number,
    reconcile_invoice,
    create_invoice,
    update_invoice,
    delete_invoice,
    add_invoice_line_items,
    update_invoice_line_item,
    remove_invoice_line_item,
    generate_invoice_packet,
    complete_invoice,
):
    register(_tool)
