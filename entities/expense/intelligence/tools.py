"""Agent-facing tools for the Expense entity.

Expense is a vendor expense (typically credit-card or cash purchase).
Same parent as Bill (Vendor), same workflow (draft → complete), same
outbox-backed external sync. Larger than BillCredit (~10K rows).

The `IsCredit` boolean on Expense doubles as the "ExpenseRefund"
concept the user asked about — when IsCredit=true, the row represents
a refund / credit-card credit. There's no separate ExpenseRefund table.
The agent surfaces both via the same tools and can filter results
client-side when the user asks specifically for refunds.

V1 deliberately omits line-item CRUD — same scope as bill_specialist.

Read tools (no approval):
  search_expenses                       → GET /api/v1/get/expenses?search=...
  read_expense_by_public_id             → GET /api/v1/get/expense/{public_id}
  read_expense_by_reference_and_vendor  → GET /api/v1/get/expense/by-reference-number-and-vendor

Write tools (user approval required):
  create_expense                        → POST   /api/v1/create/expense
  update_expense                        → PUT    /api/v1/update/expense/{public_id}
  delete_expense                        → DELETE /api/v1/delete/expense/{public_id}
  complete_expense                      → POST   /api/v1/complete/expense/{public_id}

Tools self-register on import.
"""
from typing import Optional
from urllib.parse import quote, urlencode

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shapes ──────────────────────────────────────────────────────────

class _PublicIdArgs(BaseModel):
    public_id: str = Field(description="The Expense's public_id (UUID).")


class _SearchArgs(BaseModel):
    query: Optional[str] = Field(
        default=None,
        description=(
            "Optional substring match against reference_number / memo. "
            "Combine with vendor_id and is_draft to narrow."
        ),
    )
    vendor_id: Optional[int] = Field(
        default=None,
        description=(
            "Optional Vendor internal id (BIGINT) — restricts results "
            "to expenses from that vendor. Get the id from a prior "
            "Vendor read; do not surface this id in user text."
        ),
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description=(
            "Optional filter: true returns only draft expenses; false "
            "returns only completed; omit for all."
        ),
    )
    limit: int = Field(default=10, description="Max results (1-100).")


class _ByReferenceArgs(BaseModel):
    reference_number: str = Field(
        description="Reference / receipt number — e.g. `RCT-1234`.",
    )
    vendor_public_id: str = Field(
        description="UUID of the parent vendor — required to disambiguate.",
    )


# ─── Read tools ──────────────────────────────────────────────────────────

async def _search_expenses(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _SearchArgs(**args)
    qs: dict = {"page": 1, "page_size": parsed.limit}
    if parsed.query:
        qs["search"] = parsed.query
    if parsed.vendor_id is not None:
        qs["vendor_id"] = parsed.vendor_id
    if parsed.is_draft is not None:
        qs["is_draft"] = "true" if parsed.is_draft else "false"
    return await ctx.call_api("GET", f"/api/v1/get/expenses?{urlencode(qs)}")


search_expenses = Tool(
    name="search_expenses",
    description=(
        "Find expenses via server-side search + filters. The catalog "
        "is large (~10K rows) so this is the only read-many tool. "
        "Combine `query` (substring on reference_number / memo), "
        "`vendor_id` (specific vendor's expenses), and `is_draft` to "
        "narrow. NOTE: there's no server-side filter for `is_credit` "
        "(refunds vs charges); each result row carries the field, so "
        "if the user asks for refunds you can filter the returned "
        "list yourself."
    ),
    input_schema=input_schema_from(_SearchArgs),
    handler=_search_expenses,
)


async def _read_expense_by_public_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api("GET", f"/api/v1/get/expense/{parsed.public_id}")


read_expense_by_public_id = Tool(
    name="read_expense_by_public_id",
    description=(
        "Fetch one expense by its public_id (UUID). Use after "
        "`search_expenses` surfaces it, or when the user pastes a "
        "UUID directly. Response includes the parent vendor reference "
        "and any line items."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_read_expense_by_public_id,
)


async def _read_expense_by_reference_and_vendor(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = _ByReferenceArgs(**args)
    return await ctx.call_api(
        "GET",
        "/api/v1/get/expense/by-reference-number-and-vendor"
        f"?reference_number={quote(parsed.reference_number)}"
        f"&vendor_public_id={quote(parsed.vendor_public_id)}",
    )


read_expense_by_reference_and_vendor = Tool(
    name="read_expense_by_reference_and_vendor",
    description=(
        "Look up an expense by its reference number plus the vendor's "
        "public_id. Use when the user names both — search the vendor "
        "first to get its UUID, then call this. Reference numbers "
        "aren't unique on their own."
    ),
    input_schema=input_schema_from(_ByReferenceArgs),
    handler=_read_expense_by_reference_and_vendor,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateExpenseArgs(BaseModel):
    vendor_public_id: str = Field(
        description=(
            "UUID of the vendor. If the user names a vendor, "
            "search_vendors first to resolve the UUID."
        ),
    )
    expense_date: str = Field(
        description="Expense date — ISO `YYYY-MM-DD`.",
    )
    reference_number: str = Field(
        description=(
            "Reference / receipt number (<=50 chars). Must be unique "
            "within this vendor."
        ),
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
            "Defaults to true. Expenses get finalized later via "
            "`complete_expense`."
        ),
    )
    is_credit: Optional[bool] = Field(
        default=False,
        description=(
            "Set true to record a credit-card credit / refund (i.e. an "
            "ExpenseRefund). Defaults to false (charge / expense). "
            "Stored as Expense.IsCredit; there is no separate "
            "ExpenseRefund entity."
        ),
    )


async def _create_expense(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CreateExpenseArgs(**args)
    return await ctx.call_api(
        "POST",
        "/api/v1/create/expense",
        body=parsed.model_dump(exclude_none=False),
    )


def _summarize_create_expense(args: dict) -> str:
    ref = args.get("reference_number") or "?"
    kind = "refund" if args.get("is_credit") else "expense"
    return f"Create draft {kind} {ref}"


create_expense = Tool(
    name="create_expense",
    description=(
        "Create a NEW DRAFT EXPENSE with no line items. REQUIRES USER "
        "APPROVAL. The expense becomes a draft (IsDraft=true). To "
        "record a refund (credit-card credit), pass `is_credit=true` "
        "— same tool, same shape, just a different IsCredit value. "
        "If the user names a vendor, resolve via `search_vendors` "
        "first. Server enforces (vendor, reference_number) "
        "uniqueness."
    ),
    input_schema=input_schema_from(CreateExpenseArgs),
    handler=_create_expense,
    requires_approval=True,
    approval_summary=_summarize_create_expense,
)


class UpdateExpenseArgs(BaseModel):
    public_id: str = Field(description="UUID of the expense to update.")
    row_version: str = Field(
        description=(
            "Base64 row version from your most recent read. Optimistic "
            "concurrency token — pass verbatim."
        ),
    )
    vendor_public_id: str = Field(
        description=(
            "UUID of the parent vendor. Required even when not "
            "changing vendor — pass the existing one verbatim."
        ),
    )
    expense_date: str = Field(description="Expense date (ISO `YYYY-MM-DD`).")
    reference_number: str = Field(description="Reference number (<=50 chars).")
    total_amount: Optional[float] = Field(default=None)
    memo: Optional[str] = Field(default=None)
    is_draft: Optional[bool] = Field(
        default=None,
        description=(
            "Pass `false` to mark committed (NOT the same as completing "
            "— `complete_expense` is the proper workflow). Leave unset "
            "to preserve."
        ),
    )
    is_credit: Optional[bool] = Field(
        default=None,
        description=(
            "Toggle between expense (false) and refund (true). Leave "
            "unset to preserve."
        ),
    )


async def _update_expense(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateExpenseArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT", f"/api/v1/update/expense/{parsed.public_id}", body=body
    )


def _summarize_update_expense(args: dict) -> str:
    ref = args.get("reference_number") or "?"
    return f"Update expense {ref}"


update_expense = Tool(
    name="update_expense",
    description=(
        "Modify an existing expense's PARENT fields. Line items are "
        "NOT changed by this tool. REQUIRES USER APPROVAL. Read the "
        "record first to get all required fields and `row_version`. "
        "Be explicit in prose about what's changing."
    ),
    input_schema=input_schema_from(UpdateExpenseArgs),
    handler=_update_expense,
    requires_approval=True,
    approval_summary=_summarize_update_expense,
)


class DeleteExpenseArgs(BaseModel):
    public_id: str = Field(description="UUID of the expense to delete.")
    reference_number: Optional[str] = Field(
        default=None,
        description="Reference number — display hint for the approval card.",
    )
    vendor_name: Optional[str] = Field(
        default=None,
        description="Vendor name — display hint for the approval card.",
    )


async def _delete_expense(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteExpenseArgs(**args)
    return await ctx.call_api(
        "DELETE", f"/api/v1/delete/expense/{parsed.public_id}"
    )


def _summarize_delete_expense(args: dict) -> str:
    ref = args.get("reference_number")
    vendor = args.get("vendor_name")
    if ref and vendor:
        return f"Delete expense {ref} ({vendor})"
    if ref:
        return f"Delete expense {ref}"
    return f"Delete expense {args.get('public_id') or '?'}"


delete_expense = Tool(
    name="delete_expense",
    description=(
        "Delete an expense. REQUIRES USER APPROVAL. Look up the "
        "record first and pass `reference_number` + `vendor_name` as "
        "display hints. WARN the user plainly if the expense is not "
        "a draft."
    ),
    input_schema=input_schema_from(DeleteExpenseArgs),
    handler=_delete_expense,
    requires_approval=True,
    approval_summary=_summarize_delete_expense,
)


class CompleteExpenseArgs(BaseModel):
    public_id: str = Field(description="UUID of the expense to complete.")
    reference_number: Optional[str] = Field(
        default=None,
        description="Reference number — display hint for the approval card.",
    )
    vendor_name: Optional[str] = Field(
        default=None,
        description="Vendor name — display hint for the approval card.",
    )


async def _complete_expense(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CompleteExpenseArgs(**args)
    return await ctx.call_api(
        "POST", f"/api/v1/complete/expense/{parsed.public_id}"
    )


def _summarize_complete_expense(args: dict) -> str:
    ref = args.get("reference_number")
    vendor = args.get("vendor_name")
    if ref and vendor:
        return f"Complete expense {ref} ({vendor}) — push to QBO + Excel"
    if ref:
        return f"Complete expense {ref} — push to QBO + Excel"
    return f"Complete expense {args.get('public_id') or '?'}"


complete_expense = Tool(
    name="complete_expense",
    description=(
        "Finalize a draft expense: locks IsDraft=false, then enqueues "
        "Excel sync + QBO push via the outbox. REQUIRES USER APPROVAL. "
        "Use this for the 'mark this expense ready / push to QBO' "
        "workflow — do NOT just flip `is_draft` via update_expense, "
        "that bypasses the external sync side effects."
    ),
    input_schema=input_schema_from(CompleteExpenseArgs),
    handler=_complete_expense,
    requires_approval=True,
    approval_summary=_summarize_complete_expense,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    search_expenses,
    read_expense_by_public_id,
    read_expense_by_reference_and_vendor,
    create_expense,
    update_expense,
    delete_expense,
    complete_expense,
):
    register(_tool)
