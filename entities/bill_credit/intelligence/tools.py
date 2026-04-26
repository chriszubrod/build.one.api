"""Agent-facing tools for the BillCredit entity.

BillCredit is a vendor credit memo — like a bill in reverse. Same
parent (Vendor), same workflow (draft → complete), same outbox-backed
external sync. Catalog is small (~400 rows) but the agent uses
`search_bill_credits` first regardless to keep context tight, mirroring
the Bill specialist's discipline.

V1 deliberately omits line-item CRUD (variable-length-array approval
cards are a separate design problem). Parent-only create + parent-
field update + workflow `complete` is the v1 sweet spot.

Read tools (no approval):
  search_bill_credits                  → GET /api/v1/get/bill-credits?search=...
  read_bill_credit_by_public_id        → GET /api/v1/get/bill-credit/{public_id}
  read_bill_credit_by_number_and_vendor→ GET /api/v1/get/bill-credit/by-credit-number-and-vendor

Write tools (user approval required):
  create_bill_credit                   → POST   /api/v1/create/bill-credit
                                         (Creates a DRAFT credit with no
                                         line items.)
  update_bill_credit                   → PUT    /api/v1/update/bill-credit/{public_id}
  delete_bill_credit                   → DELETE /api/v1/delete/bill-credit/{public_id}
  complete_bill_credit                 → POST   /api/v1/complete/bill-credit/{public_id}
                                         (Server: locks IsDraft=false,
                                         finalizes attachments to module
                                         folders.)

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
    public_id: str = Field(description="The BillCredit's public_id (UUID).")


class _SearchArgs(BaseModel):
    query: Optional[str] = Field(
        default=None,
        description=(
            "Optional substring match against credit_number / memo "
            "(server-side). Combine with vendor_id and is_draft to "
            "narrow."
        ),
    )
    vendor_id: Optional[int] = Field(
        default=None,
        description=(
            "Optional Vendor internal id (BIGINT) — restricts results "
            "to credits from that vendor. Get the id from a prior "
            "Vendor read; do not surface this id in user text."
        ),
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description=(
            "Optional filter: true returns only draft credits; false "
            "returns only completed; omit for all."
        ),
    )
    limit: int = Field(
        default=10, description="Max results to return (1-100)."
    )


class _ByCreditNumberArgs(BaseModel):
    credit_number: str = Field(
        description="The credit number — e.g. `CR-1234`.",
    )
    vendor_public_id: str = Field(
        description="UUID of the parent vendor — required to disambiguate.",
    )


# ─── Read tools ──────────────────────────────────────────────────────────

async def _search_bill_credits(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _SearchArgs(**args)
    qs: dict = {"page": 1, "page_size": parsed.limit}
    if parsed.query:
        qs["search"] = parsed.query
    if parsed.vendor_id is not None:
        qs["vendor_id"] = parsed.vendor_id
    if parsed.is_draft is not None:
        qs["is_draft"] = "true" if parsed.is_draft else "false"
    return await ctx.call_api("GET", f"/api/v1/get/bill-credits?{urlencode(qs)}")


search_bill_credits = Tool(
    name="search_bill_credits",
    description=(
        "Find bill credits via server-side search + filters. The "
        "catalog is small (~400 rows) but search-first keeps context "
        "tight. Combine `query` (substring on credit_number / memo), "
        "`vendor_id` (specific vendor's credits), and `is_draft` to "
        "narrow."
    ),
    input_schema=input_schema_from(_SearchArgs),
    handler=_search_bill_credits,
)


async def _read_bill_credit_by_public_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/bill-credit/{parsed.public_id}"
    )


read_bill_credit_by_public_id = Tool(
    name="read_bill_credit_by_public_id",
    description=(
        "Fetch one bill credit by its public_id (UUID). Use after "
        "`search_bill_credits` surfaces it, or when the user pastes a "
        "UUID directly. Response includes the parent vendor reference "
        "and any line items."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_read_bill_credit_by_public_id,
)


async def _read_bill_credit_by_number_and_vendor(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = _ByCreditNumberArgs(**args)
    return await ctx.call_api(
        "GET",
        "/api/v1/get/bill-credit/by-credit-number-and-vendor"
        f"?credit_number={quote(parsed.credit_number)}"
        f"&vendor_public_id={quote(parsed.vendor_public_id)}",
    )


read_bill_credit_by_number_and_vendor = Tool(
    name="read_bill_credit_by_number_and_vendor",
    description=(
        "Look up a credit by its human-facing number plus the vendor's "
        "public_id. Use when the user says something like 'credit "
        "#CR-1234 from Home Depot' — search the vendor first to get "
        "its UUID, then call this. Credit numbers aren't unique on "
        "their own."
    ),
    input_schema=input_schema_from(_ByCreditNumberArgs),
    handler=_read_bill_credit_by_number_and_vendor,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateBillCreditArgs(BaseModel):
    vendor_public_id: str = Field(
        description=(
            "UUID of the vendor on the credit. If the user names a "
            "vendor, search_vendors first to resolve the UUID."
        ),
    )
    credit_date: str = Field(
        description="Credit date — ISO `YYYY-MM-DD`.",
    )
    credit_number: str = Field(
        description=(
            "The vendor's credit / memo number (<=50 chars). Must be "
            "unique within this vendor."
        ),
    )
    total_amount: Optional[float] = Field(
        default=None,
        description=(
            "Optional total amount. Often left blank at draft time — "
            "totals come from line items, which are added separately "
            "after creation."
        ),
    )
    memo: Optional[str] = Field(default=None, description="Optional memo.")
    is_draft: Optional[bool] = Field(
        default=True,
        description=(
            "Defaults to true. The agent should rarely override — "
            "credits get finalized later via `complete_bill_credit`."
        ),
    )


async def _create_bill_credit(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CreateBillCreditArgs(**args)
    return await ctx.call_api(
        "POST",
        "/api/v1/create/bill-credit",
        body=parsed.model_dump(exclude_none=False),
    )


def _summarize_create_bill_credit(args: dict) -> str:
    number = args.get("credit_number") or "?"
    return f"Create draft bill credit {number}"


create_bill_credit = Tool(
    name="create_bill_credit",
    description=(
        "Create a NEW DRAFT BILL CREDIT with no line items. REQUIRES "
        "USER APPROVAL. The credit becomes a draft (IsDraft=true) — "
        "line items are added separately (via the UI today; via "
        "dedicated tools in a future iteration). Once the lines are "
        "in, use `complete_bill_credit` to finalize. If the user "
        "names a vendor, resolve via `search_vendors` first to get "
        "the vendor's `public_id`. Server enforces (vendor, credit_"
        "number) uniqueness — surface that error plainly if it fires."
    ),
    input_schema=input_schema_from(CreateBillCreditArgs),
    handler=_create_bill_credit,
    requires_approval=True,
    approval_summary=_summarize_create_bill_credit,
)


class UpdateBillCreditArgs(BaseModel):
    public_id: str = Field(description="UUID of the credit to update.")
    row_version: str = Field(
        description=(
            "Base64 row version from your most recent read. Optimistic "
            "concurrency token — pass verbatim."
        ),
    )
    vendor_public_id: str = Field(
        description=(
            "UUID of the parent vendor. Required even when not changing "
            "vendor — pass the existing one verbatim. To change the "
            "vendor, look the new one up via search_vendors first."
        ),
    )
    credit_date: str = Field(description="Credit date (ISO `YYYY-MM-DD`).")
    credit_number: str = Field(description="Credit number (<=50 chars).")
    total_amount: Optional[float] = Field(
        default=None,
        description=(
            "Total amount. Often left as the existing value — pass "
            "through what the read returned unless explicitly changing."
        ),
    )
    memo: Optional[str] = Field(default=None)
    is_draft: Optional[bool] = Field(
        default=None,
        description=(
            "Pass `false` to mark the credit committed (NOT the same "
            "as completing — `complete_bill_credit` is the proper "
            "workflow for finalizing). Leave unset to preserve."
        ),
    )


async def _update_bill_credit(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateBillCreditArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT", f"/api/v1/update/bill-credit/{parsed.public_id}", body=body
    )


def _summarize_update_bill_credit(args: dict) -> str:
    number = args.get("credit_number") or "?"
    return f"Update bill credit {number}"


update_bill_credit = Tool(
    name="update_bill_credit",
    description=(
        "Modify an existing bill credit's PARENT fields (vendor, "
        "credit_date, credit_number, total, memo, draft state). Line "
        "items are NOT changed by this tool. REQUIRES USER APPROVAL. "
        "Read the record first to get all required fields and "
        "`row_version`. Be explicit in prose about what's changing."
    ),
    input_schema=input_schema_from(UpdateBillCreditArgs),
    handler=_update_bill_credit,
    requires_approval=True,
    approval_summary=_summarize_update_bill_credit,
)


class DeleteBillCreditArgs(BaseModel):
    public_id: str = Field(description="UUID of the credit to delete.")
    credit_number: Optional[str] = Field(
        default=None,
        description="Credit number — display hint for the approval card.",
    )
    vendor_name: Optional[str] = Field(
        default=None,
        description="Vendor name — display hint for the approval card.",
    )


async def _delete_bill_credit(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteBillCreditArgs(**args)
    return await ctx.call_api(
        "DELETE", f"/api/v1/delete/bill-credit/{parsed.public_id}"
    )


def _summarize_delete_bill_credit(args: dict) -> str:
    number = args.get("credit_number")
    vendor = args.get("vendor_name")
    if number and vendor:
        return f"Delete bill credit {number} ({vendor})"
    if number:
        return f"Delete bill credit {number}"
    return f"Delete bill credit {args.get('public_id') or '?'}"


delete_bill_credit = Tool(
    name="delete_bill_credit",
    description=(
        "Delete a bill credit. REQUIRES USER APPROVAL. Look up the "
        "record first and pass `credit_number` + `vendor_name` as "
        "display hints so the approval card reads clearly. WARN the "
        "user plainly if the credit is not a draft — completed "
        "credits may have been pushed externally."
    ),
    input_schema=input_schema_from(DeleteBillCreditArgs),
    handler=_delete_bill_credit,
    requires_approval=True,
    approval_summary=_summarize_delete_bill_credit,
)


class CompleteBillCreditArgs(BaseModel):
    public_id: str = Field(description="UUID of the credit to complete.")
    credit_number: Optional[str] = Field(
        default=None,
        description="Credit number — display hint for the approval card.",
    )
    vendor_name: Optional[str] = Field(
        default=None,
        description="Vendor name — display hint for the approval card.",
    )


async def _complete_bill_credit(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CompleteBillCreditArgs(**args)
    return await ctx.call_api(
        "POST", f"/api/v1/complete/bill-credit/{parsed.public_id}"
    )


def _summarize_complete_bill_credit(args: dict) -> str:
    number = args.get("credit_number")
    vendor = args.get("vendor_name")
    if number and vendor:
        return f"Complete bill credit {number} ({vendor})"
    if number:
        return f"Complete bill credit {number}"
    return f"Complete bill credit {args.get('public_id') or '?'}"


complete_bill_credit = Tool(
    name="complete_bill_credit",
    description=(
        "Finalize a draft bill credit: locks IsDraft=false locally "
        "and uploads attachments to module folders. REQUIRES USER "
        "APPROVAL. Use this for the 'mark this credit ready' "
        "workflow — do NOT just flip `is_draft` via update_bill_credit, "
        "that skips the attachment-upload side effects."
    ),
    input_schema=input_schema_from(CompleteBillCreditArgs),
    handler=_complete_bill_credit,
    requires_approval=True,
    approval_summary=_summarize_complete_bill_credit,
)


# ─── Line-item tools (V2) ─────────────────────────────────────────────────

import json as _json


class _BillCreditLineItemSpec(BaseModel):
    """One bill credit line item to create. Mirrors BillCreditLineItemCreate."""
    sub_cost_code_id: Optional[int] = Field(
        default=None,
        description=(
            "Internal BIGINT id of the SubCostCode to credit. Resolve "
            "via `read_sub_cost_code_by_number` or `search_sub_cost_codes` "
            "first to get the `id`. Optional — can be filled in later."
        ),
    )
    project_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional UUID of the Project. If the user names a project, "
            "search_projects first to resolve."
        ),
    )
    description: Optional[str] = Field(default=None)
    quantity: Optional[float] = Field(default=None)
    unit_price: Optional[float] = Field(default=None)
    amount: Optional[float] = Field(default=None)
    is_billable: Optional[bool] = Field(
        default=None,
        description=(
            "Whether this credit line is billable to the customer "
            "(default: true at the DB level)."
        ),
    )
    billable_amount: Optional[float] = Field(default=None)


class AddBillCreditLineItemsArgs(BaseModel):
    bill_credit_public_id: str = Field(
        description="UUID of the bill credit to add lines to.",
    )
    items: list[_BillCreditLineItemSpec] = Field(
        description=(
            "List of line item specs. Each is created via "
            "POST /api/v1/create/bill-credit-line-item with is_draft=true. "
            "The bill credit itself stays a draft until completed via "
            "complete_bill_credit."
        ),
    )


async def _add_bill_credit_line_items(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = AddBillCreditLineItemsArgs(**args)
    if not parsed.items:
        return ToolResult(content="No items provided — nothing to add.", is_error=True)
    created: list[dict] = []
    errors: list[dict] = []
    for idx, spec in enumerate(parsed.items):
        body = {
            "bill_credit_public_id": parsed.bill_credit_public_id,
            "sub_cost_code_id": spec.sub_cost_code_id,
            "project_public_id": spec.project_public_id,
            "description": spec.description,
            "quantity": spec.quantity,
            "unit_price": spec.unit_price,
            "amount": spec.amount,
            "is_billable": spec.is_billable,
            "billable_amount": spec.billable_amount,
            "is_draft": True,
        }
        result = await ctx.call_api(
            "POST", "/api/v1/create/bill-credit-line-item", body=body
        )
        if result.is_error:
            errors.append({"index": idx, "error": str(result.content)[:300]})
        else:
            created.append({"index": idx, "result": result.content})
    return ToolResult(
        content=_json.dumps({
            "added": len(created),
            "failed": len(errors),
            "created": created,
            "errors": errors,
        }),
        is_error=bool(errors and not created),
    )


def _summarize_add_bill_credit_line_items(args: dict) -> str:
    items = args.get("items") or []
    return f"Add {len(items)} line item(s) to bill credit"


add_bill_credit_line_items = Tool(
    name="add_bill_credit_line_items",
    description=(
        "Add line items to an existing bill credit in batch. REQUIRES "
        "USER APPROVAL (one card per batch). Each line needs a "
        "`sub_cost_code_id` (BIGINT — resolve via "
        "`read_sub_cost_code_by_number` or `search_sub_cost_codes` "
        "first) and an optional `project_public_id`. Server creates "
        "each line as IsDraft=true; the bill credit itself remains a "
        "draft until `complete_bill_credit`. If some succeed and some "
        "fail, the result includes `created` and `errors` arrays — "
        "retry only the failed indices, do not re-submit the whole batch."
    ),
    input_schema=input_schema_from(AddBillCreditLineItemsArgs),
    handler=_add_bill_credit_line_items,
    requires_approval=True,
    approval_summary=_summarize_add_bill_credit_line_items,
)


class UpdateBillCreditLineItemArgs(BaseModel):
    public_id: str = Field(
        description="UUID of the bill credit line item to update.",
    )
    row_version: str = Field(
        description="Base64 row version from your most recent read. Pass verbatim.",
    )
    bill_credit_public_id: str = Field(
        description=(
            "UUID of the parent bill credit — required by the API even "
            "when not changing it."
        ),
    )
    sub_cost_code_id: Optional[int] = Field(default=None)
    project_public_id: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    quantity: Optional[float] = Field(default=None)
    unit_price: Optional[float] = Field(default=None)
    amount: Optional[float] = Field(default=None)
    is_billable: Optional[bool] = Field(default=None)
    billable_amount: Optional[float] = Field(default=None)


async def _update_bill_credit_line_item(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateBillCreditLineItemArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT", f"/api/v1/update/bill-credit-line-item/{parsed.public_id}", body=body
    )


def _summarize_update_bill_credit_line_item(args: dict) -> str:
    return f"Update bill credit line item {args.get('public_id') or '?'}"


update_bill_credit_line_item = Tool(
    name="update_bill_credit_line_item",
    description=(
        "Edit a single BillCreditLineItem (sub-cost-code / project / "
        "description / numeric fields). REQUIRES USER APPROVAL. Read "
        "the line item first for `row_version`. Pass through fields "
        "you're not changing as their existing values."
    ),
    input_schema=input_schema_from(UpdateBillCreditLineItemArgs),
    handler=_update_bill_credit_line_item,
    requires_approval=True,
    approval_summary=_summarize_update_bill_credit_line_item,
)


class RemoveBillCreditLineItemArgs(BaseModel):
    public_id: str = Field(description="UUID of the line item to remove.")
    description: Optional[str] = Field(
        default=None,
        description="Description — display hint for the approval card.",
    )


async def _remove_bill_credit_line_item(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = RemoveBillCreditLineItemArgs(**args)
    return await ctx.call_api(
        "DELETE", f"/api/v1/delete/bill-credit-line-item/{parsed.public_id}"
    )


def _summarize_remove_bill_credit_line_item(args: dict) -> str:
    desc = args.get("description")
    return (
        f"Remove bill credit line — {desc}"
        if desc
        else f"Remove bill credit line {args.get('public_id') or '?'}"
    )


remove_bill_credit_line_item = Tool(
    name="remove_bill_credit_line_item",
    description=(
        "Drop a single line from a bill credit. REQUIRES USER APPROVAL. "
        "Pass `description` as a display hint for the card."
    ),
    input_schema=input_schema_from(RemoveBillCreditLineItemArgs),
    handler=_remove_bill_credit_line_item,
    requires_approval=True,
    approval_summary=_summarize_remove_bill_credit_line_item,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    search_bill_credits,
    read_bill_credit_by_public_id,
    read_bill_credit_by_number_and_vendor,
    create_bill_credit,
    update_bill_credit,
    delete_bill_credit,
    complete_bill_credit,
    add_bill_credit_line_items,
    update_bill_credit_line_item,
    remove_bill_credit_line_item,
):
    register(_tool)
