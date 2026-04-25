"""Agent-facing tools for the Bill entity.

Bill is one of the "transactional" entities — large catalog (~18K rows),
line items, status workflow (draft → completed), QBO push hooks. The
agent specialist focuses on the parent record: search, read, update of
parent fields, delete, and the workflow `complete` action that pushes
to QBO / SharePoint / Excel.

V1 deliberately omits:
  - `create_bill` — needs line items to be useful, and approval-card
    rendering of variable-length line-item arrays is a v2 design problem.
  - Line-item CRUD — same reason.

Read tools (no approval):
  search_bills                    → GET  /api/v1/get/bills?search=...
  read_bill_by_public_id          → GET  /api/v1/get/bill/{public_id}
  read_bill_by_number_and_vendor  → GET  /api/v1/get/bill/by-bill-number-and-vendor

Write tools (user approval required):
  create_bill                     → POST   /api/v1/create/bill
                                    (Creates a DRAFT bill with no line
                                    items. Line items are added via a
                                    separate workflow / future tool.)
  update_bill                     → PUT    /api/v1/update/bill/{public_id}
  delete_bill                     → DELETE /api/v1/delete/bill/{public_id}
  complete_bill                   → POST   /api/v1/complete/bill/{public_id}
                                    (Server-side: locks IsDraft=false,
                                    then enqueues SharePoint upload +
                                    Excel sync + QBO push via the
                                    outbox.)

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
    public_id: str = Field(description="The Bill's public_id (UUID).")


class _SearchArgs(BaseModel):
    query: Optional[str] = Field(
        default=None,
        description=(
            "Optional case-insensitive substring matched against the "
            "bill_number / memo (server-side). Use to narrow when the "
            "user names a number or describes the bill."
        ),
    )
    vendor_id: Optional[int] = Field(
        default=None,
        description=(
            "Optional Vendor internal id (BIGINT) — restricts results "
            "to bills from that vendor. Get the id from a prior "
            "Vendor read; do not surface this id in user text."
        ),
    )
    is_draft: Optional[bool] = Field(
        default=None,
        description=(
            "Optional filter: true returns only draft (uncommitted) "
            "bills; false returns only completed bills; omit for all."
        ),
    )
    limit: int = Field(
        default=10,
        description="Max bills to return (1-100). Start small.",
    )


class _ByBillNumberArgs(BaseModel):
    bill_number: str = Field(description="The bill number, e.g. `INV-1234`.")
    vendor_public_id: str = Field(
        description="UUID of the parent vendor — required to disambiguate.",
    )


# ─── Read tools ──────────────────────────────────────────────────────────

async def _search_bills(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _SearchArgs(**args)
    qs: dict = {"page": 1, "page_size": parsed.limit}
    if parsed.query:
        qs["search"] = parsed.query
    if parsed.vendor_id is not None:
        qs["vendor_id"] = parsed.vendor_id
    if parsed.is_draft is not None:
        qs["is_draft"] = "true" if parsed.is_draft else "false"
    return await ctx.call_api("GET", f"/api/v1/get/bills?{urlencode(qs)}")


search_bills = Tool(
    name="search_bills",
    description=(
        "Find bills via server-side search + filters. Bill is too "
        "large (~18K rows) to ever list in full — this is the only "
        "read-many tool. Combine `query` (substring on bill_number / "
        "memo), `vendor_id` (specific vendor's bills), and `is_draft` "
        "to narrow as needed. Use `vendor_id` from a prior Vendor "
        "read for 'all bills from X' queries; use `query` for 'bill "
        "containing 1234' queries; use both for precision."
    ),
    input_schema=input_schema_from(_SearchArgs),
    handler=_search_bills,
)


async def _read_bill_by_public_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api("GET", f"/api/v1/get/bill/{parsed.public_id}")


read_bill_by_public_id = Tool(
    name="read_bill_by_public_id",
    description=(
        "Fetch one bill by its public_id (UUID). Use after `search_bills` "
        "surfaces it, or when the user pastes a UUID directly. The "
        "response includes the bill's parent vendor reference and its "
        "line items."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_read_bill_by_public_id,
)


async def _read_bill_by_number_and_vendor(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = _ByBillNumberArgs(**args)
    return await ctx.call_api(
        "GET",
        "/api/v1/get/bill/by-bill-number-and-vendor"
        f"?bill_number={quote(parsed.bill_number)}"
        f"&vendor_public_id={quote(parsed.vendor_public_id)}",
    )


read_bill_by_number_and_vendor = Tool(
    name="read_bill_by_number_and_vendor",
    description=(
        "Look up a bill by its human-facing number plus the vendor's "
        "public_id. Use when the user says something like 'bill #1234 "
        "from Home Depot' — search the vendor first to get its UUID, "
        "then call this. Bill numbers aren't unique on their own."
    ),
    input_schema=input_schema_from(_ByBillNumberArgs),
    handler=_read_bill_by_number_and_vendor,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateBillArgs(BaseModel):
    vendor_public_id: str = Field(
        description=(
            "UUID of the vendor on the bill. If the user names a "
            "vendor, search_vendors first to resolve the UUID."
        ),
    )
    bill_date: str = Field(
        description="Bill date — ISO `YYYY-MM-DD`.",
    )
    due_date: str = Field(
        description="Due date — ISO `YYYY-MM-DD`.",
    )
    bill_number: str = Field(
        description=(
            "The vendor's bill / invoice number (<=50 chars). Must be "
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
    payment_term_public_id: Optional[str] = Field(
        default=None,
        description="Optional UUID of a payment term.",
    )
    is_draft: Optional[bool] = Field(
        default=True,
        description=(
            "Defaults to true. The agent layer should rarely need to "
            "set this — bills get finalized later via `complete_bill` "
            "after line items are added."
        ),
    )


async def _create_bill(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CreateBillArgs(**args)
    return await ctx.call_api(
        "POST",
        "/api/v1/create/bill",
        body=parsed.model_dump(exclude_none=False),
    )


def _summarize_create_bill(args: dict) -> str:
    number = args.get("bill_number") or "?"
    return f"Create draft bill {number}"


create_bill = Tool(
    name="create_bill",
    description=(
        "Create a NEW DRAFT BILL with no line items. REQUIRES USER "
        "APPROVAL. The bill becomes a draft (IsDraft=true) — line items "
        "are added separately (via the UI today; via dedicated tools in "
        "a future iteration). Once the lines are in, use `complete_bill` "
        "to finalize and push to QBO + SharePoint + Excel. If the user "
        "names a vendor, resolve via `search_vendors` first to get the "
        "vendor's `public_id`. The server enforces (vendor, bill_number) "
        "uniqueness — surface that error plainly if it fires."
    ),
    input_schema=input_schema_from(CreateBillArgs),
    handler=_create_bill,
    requires_approval=True,
    approval_summary=_summarize_create_bill,
)


class UpdateBillArgs(BaseModel):
    public_id: str = Field(description="UUID of the bill to update.")
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
    bill_date: str = Field(description="Bill date (ISO `YYYY-MM-DD`).")
    due_date: str = Field(description="Due date (ISO `YYYY-MM-DD`).")
    bill_number: str = Field(description="Bill number (<=50 chars).")
    payment_term_public_id: Optional[str] = Field(default=None)
    total_amount: Optional[float] = Field(
        default=None,
        description=(
            "Total amount (decimal). Often left as the existing value — "
            "pass through what the read returned unless the user is "
            "explicitly changing the total."
        ),
    )
    memo: Optional[str] = Field(default=None)
    is_draft: Optional[bool] = Field(
        default=None,
        description=(
            "Pass `false` to mark the bill committed (NOT the same as "
            "completing — `complete_bill` is the proper workflow for "
            "finalizing + pushing to QBO/SharePoint/Excel). Leave "
            "unset to preserve the current draft state."
        ),
    )


async def _update_bill(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateBillArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT", f"/api/v1/update/bill/{parsed.public_id}", body=body
    )


def _summarize_update_bill(args: dict) -> str:
    number = args.get("bill_number") or "?"
    return f"Update bill {number}"


update_bill = Tool(
    name="update_bill",
    description=(
        "Modify an existing bill's PARENT fields (vendor, dates, "
        "number, memo, draft state). Line items are NOT changed by "
        "this tool — that's a separate v2 workflow. REQUIRES USER "
        "APPROVAL. Read the record first to get all required fields "
        "and `row_version`. Be explicit in prose about what's "
        "changing — the approval card shows only the new state."
    ),
    input_schema=input_schema_from(UpdateBillArgs),
    handler=_update_bill,
    requires_approval=True,
    approval_summary=_summarize_update_bill,
)


class DeleteBillArgs(BaseModel):
    public_id: str = Field(description="UUID of the bill to delete.")
    bill_number: Optional[str] = Field(
        default=None,
        description="Bill number — display hint for the approval card.",
    )
    vendor_name: Optional[str] = Field(
        default=None,
        description="Vendor name — display hint for the approval card.",
    )


async def _delete_bill(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteBillArgs(**args)
    return await ctx.call_api(
        "DELETE", f"/api/v1/delete/bill/{parsed.public_id}"
    )


def _summarize_delete_bill(args: dict) -> str:
    number = args.get("bill_number")
    vendor = args.get("vendor_name")
    if number and vendor:
        return f"Delete bill {number} ({vendor})"
    if number:
        return f"Delete bill {number}"
    return f"Delete bill {args.get('public_id') or '?'}"


delete_bill = Tool(
    name="delete_bill",
    description=(
        "Delete a bill. REQUIRES USER APPROVAL. Look up the record "
        "first and pass `bill_number` + `vendor_name` as display hints "
        "so the approval card reads clearly. WARN the user plainly if "
        "the bill is not a draft — completed bills may have been pushed "
        "to QBO and deletion locally won't reverse that."
    ),
    input_schema=input_schema_from(DeleteBillArgs),
    handler=_delete_bill,
    requires_approval=True,
    approval_summary=_summarize_delete_bill,
)


class CompleteBillArgs(BaseModel):
    public_id: str = Field(description="UUID of the bill to complete.")
    bill_number: Optional[str] = Field(
        default=None,
        description="Bill number — display hint for the approval card.",
    )
    vendor_name: Optional[str] = Field(
        default=None,
        description="Vendor name — display hint for the approval card.",
    )


async def _complete_bill(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CompleteBillArgs(**args)
    return await ctx.call_api(
        "POST", f"/api/v1/complete/bill/{parsed.public_id}"
    )


def _summarize_complete_bill(args: dict) -> str:
    number = args.get("bill_number")
    vendor = args.get("vendor_name")
    if number and vendor:
        return f"Complete bill {number} ({vendor}) — push to QBO + SharePoint + Excel"
    if number:
        return f"Complete bill {number} — push to QBO + SharePoint + Excel"
    return f"Complete bill {args.get('public_id') or '?'}"


complete_bill = Tool(
    name="complete_bill",
    description=(
        "Finalize a draft bill: locks IsDraft=false locally, then "
        "enqueues SharePoint attachment upload + Excel workbook sync "
        "+ QBO push via the outbox. REQUIRES USER APPROVAL. Use this "
        "for the 'mark this bill ready / push it to QBO' workflow — "
        "do NOT just flip `is_draft` via update_bill, that skips the "
        "external sync side effects. The tool returns immediately "
        "(202-style); the actual external pushes drain asynchronously "
        "within ~5-30s."
    ),
    input_schema=input_schema_from(CompleteBillArgs),
    handler=_complete_bill,
    requires_approval=True,
    approval_summary=_summarize_complete_bill,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    search_bills,
    read_bill_by_public_id,
    read_bill_by_number_and_vendor,
    create_bill,
    update_bill,
    delete_bill,
    complete_bill,
):
    register(_tool)
