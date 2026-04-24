"""Agent-facing tools for the Customer entity.

Read tools (no approval):
  list_customers                  → GET  /api/v1/get/customers
  search_customers                → GET  /api/v1/get/customer/search?q=...
  read_customer_by_public_id      → GET  /api/v1/get/customer/{public_id}
  read_customer_by_id             → GET  /api/v1/get/customer/by-id/{id}

Write tools (user approval required):
  create_customer                 → POST   /api/v1/create/customer
  update_customer                 → PUT    /api/v1/update/customer/{public_id}
  delete_customer                 → DELETE /api/v1/delete/customer/{public_id}

Tools self-register on import.
"""
from typing import Optional
from urllib.parse import quote

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shapes ──────────────────────────────────────────────────────────

class _NoArgs(BaseModel):
    pass


class _PublicIdArgs(BaseModel):
    public_id: str = Field(description="The Customer's public_id (UUID).")


class _IdArgs(BaseModel):
    id: int = Field(
        description=(
            "The Customer's internal id (BIGINT). Obtain from a "
            "Project's `customer_id` field. Internal identifier; do "
            "not surface in user-facing text — refer to the customer "
            "by name."
        ),
    )


class _SearchArgs(BaseModel):
    query: str = Field(
        description=(
            "Case-insensitive substring matched against name, email, "
            "and phone. Examples: `acme`, `smith`, `info@`. Prefix "
            "matches rank above substring matches."
        ),
    )
    limit: int = Field(
        default=10,
        description="Max matches (1-100). Start small.",
    )


# ─── Read tools ──────────────────────────────────────────────────────────

async def _list_customers(args: dict, ctx: ToolContext) -> ToolResult:
    return await ctx.call_api("GET", "/api/v1/get/customers")


list_customers = Tool(
    name="list_customers",
    description=(
        "List ALL customers (~70 rows). Cheap by virtue of small size, "
        "but `search_customers` is preferred for any name-based lookup."
    ),
    input_schema=input_schema_from(_NoArgs),
    handler=_list_customers,
)


async def _search_customers(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _SearchArgs(**args)
    return await ctx.call_api(
        "GET",
        f"/api/v1/get/customer/search?q={quote(parsed.query)}&limit={parsed.limit}",
    )


search_customers = Tool(
    name="search_customers",
    description=(
        "Find customers by partial match against name, email, or phone. "
        "Default tool for any name-based lookup — prefer it over "
        "`list_customers` whenever the user gives a hint."
    ),
    input_schema=input_schema_from(_SearchArgs),
    handler=_search_customers,
)


async def _read_customer_by_public_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _PublicIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/customer/{parsed.public_id}"
    )


read_customer_by_public_id = Tool(
    name="read_customer_by_public_id",
    description=(
        "Fetch one customer by its public_id (UUID). Use when you "
        "already have the public_id from an earlier tool result."
    ),
    input_schema=input_schema_from(_PublicIdArgs),
    handler=_read_customer_by_public_id,
)


async def _read_customer_by_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = _IdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/customer/by-id/{parsed.id}"
    )


read_customer_by_id = Tool(
    name="read_customer_by_id",
    description=(
        "Resolve a parent Customer from a Project's `customer_id` "
        "(BIGINT FK). Use this after fetching a Project to learn its "
        "customer's name. Refer to the customer by name in answers, "
        "never by `customer_id`."
    ),
    input_schema=input_schema_from(_IdArgs),
    handler=_read_customer_by_id,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateCustomerArgs(BaseModel):
    name: str = Field(description="Customer name.")
    email: Optional[str] = Field(default=None, description="Optional email.")
    phone: Optional[str] = Field(default=None, description="Optional phone.")


async def _create_customer(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CreateCustomerArgs(**args)
    body = {
        "name": parsed.name,
        "email": parsed.email or "",
        "phone": parsed.phone or "",
    }
    return await ctx.call_api("POST", "/api/v1/create/customer", body=body)


def _summarize_create_customer(args: dict) -> str:
    name = args.get("name") or "?"
    return f"Create customer — {name}"


create_customer = Tool(
    name="create_customer",
    description=(
        "Create a new customer. REQUIRES USER APPROVAL. Propose with "
        "best-effort values; the user can approve, edit, or reject."
    ),
    input_schema=input_schema_from(CreateCustomerArgs),
    handler=_create_customer,
    requires_approval=True,
    approval_summary=_summarize_create_customer,
)


class UpdateCustomerArgs(BaseModel):
    public_id: str = Field(description="UUID of the customer to update.")
    row_version: str = Field(
        description=(
            "Base64 row version from your most recent read. Optimistic "
            "concurrency token — pass verbatim."
        ),
    )
    name: str = Field(description="The customer's name.")
    email: Optional[str] = Field(default=None)
    phone: Optional[str] = Field(default=None)


async def _update_customer(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateCustomerArgs(**args)
    body = {
        "row_version": parsed.row_version,
        "name": parsed.name,
        "email": parsed.email or "",
        "phone": parsed.phone or "",
    }
    return await ctx.call_api(
        "PUT", f"/api/v1/update/customer/{parsed.public_id}", body=body
    )


def _summarize_update_customer(args: dict) -> str:
    name = args.get("name") or "?"
    return f"Update customer — {name}"


update_customer = Tool(
    name="update_customer",
    description=(
        "Modify an existing customer. REQUIRES USER APPROVAL. Read the "
        "record first to get all fields and `row_version`, then propose "
        "with the FULL field set, applying only what the user asked to "
        "change. Be explicit in prose about what's changing — the "
        "approval card shows only the new state."
    ),
    input_schema=input_schema_from(UpdateCustomerArgs),
    handler=_update_customer,
    requires_approval=True,
    approval_summary=_summarize_update_customer,
)


class DeleteCustomerArgs(BaseModel):
    public_id: str = Field(description="UUID of the customer to delete.")
    name: Optional[str] = Field(
        default=None,
        description="Customer's name — display hint for the approval card.",
    )


async def _delete_customer(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteCustomerArgs(**args)
    return await ctx.call_api(
        "DELETE", f"/api/v1/delete/customer/{parsed.public_id}"
    )


def _summarize_delete_customer(args: dict) -> str:
    name = args.get("name")
    public_id = args.get("public_id") or "?"
    return f"Delete customer — {name}" if name else f"Delete customer {public_id}"


delete_customer = Tool(
    name="delete_customer",
    description=(
        "Permanently delete a customer. REQUIRES USER APPROVAL. Look "
        "up the record first and pass its `name` as a display hint. "
        "Warn the user plainly if the customer has associated Projects "
        "— deleting may orphan them."
    ),
    input_schema=input_schema_from(DeleteCustomerArgs),
    handler=_delete_customer,
    requires_approval=True,
    approval_summary=_summarize_delete_customer,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    list_customers,
    search_customers,
    read_customer_by_public_id,
    read_customer_by_id,
    create_customer,
    update_customer,
    delete_customer,
):
    register(_tool)
