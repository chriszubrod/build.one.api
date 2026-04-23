"""Agent-facing tools for the SubCostCode entity.

Read tools (no approval):
  list_sub_cost_codes                  → GET /api/v1/get/sub-cost-codes
  search_sub_cost_codes                → GET /api/v1/get/sub-cost-code/search?q=...&limit=...
  read_sub_cost_code_by_public_id      → GET /api/v1/get/sub-cost-code/{public_id}
  read_sub_cost_code_by_number         → GET /api/v1/get/sub-cost-code/by-number/{number}
  read_sub_cost_code_by_alias          → GET /api/v1/get/sub-cost-code/by-alias/{alias}

Write tools (user approval required):
  create_sub_cost_code                 → POST   /api/v1/create/sub-cost-code
  update_sub_cost_code                 → PUT    /api/v1/update/sub-cost-code/{public_id}
  delete_sub_cost_code                 → DELETE /api/v1/delete/sub-cost-code/{public_id}

Each tool calls ctx.call_api(), which means every invocation goes through
the same FastAPI stack a human request does: RBAC, JSON envelope, HTTP
access log. The agent's bearer token on ctx auths the call.

Tools self-register on import. Agents pick them from the registry by name.
"""
from urllib.parse import quote

from typing import Optional

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


# ─── Arg shapes ──────────────────────────────────────────────────────────

class NoArgs(BaseModel):
    pass


class PublicIdArgs(BaseModel):
    public_id: str = Field(description="The sub-cost-code's public_id (UUID string)")


class NumberArgs(BaseModel):
    number: str = Field(
        description=(
            "Sub-cost-code number in the canonical format `X.YY` "
            "(e.g. `10.01`, `9.01`, `11.10`). If the user writes a hyphen "
            "(`10-01`) or spells it out (`ten point oh one`), normalize to "
            "the dotted format before calling."
        ),
    )


class AliasArgs(BaseModel):
    alias: str = Field(
        description=(
            "A sub-cost-code alias string as registered in SubCostCodeAlias "
            "(e.g. `SitePrep`). Pass the alias verbatim; the server does the lookup."
        ),
    )


class SearchArgs(BaseModel):
    query: str = Field(
        description=(
            "Case-insensitive substring to match against sub-cost-code "
            "name, number, or alias. Examples: `concrete`, `footers`, "
            "`10.0`, `SitePrep`. Exact-prefix matches rank above substring "
            "matches. Partial matches are fine — do not wrap in wildcards."
        ),
    )
    limit: int = Field(
        default=10,
        description=(
            "Maximum number of matches to return (1-100). Start small; "
            "re-search with a larger limit only if needed."
        ),
    )


# ─── Tools ───────────────────────────────────────────────────────────────

async def _list_sub_cost_codes(args: dict, ctx: ToolContext) -> ToolResult:
    return await ctx.call_api("GET", "/api/v1/get/sub-cost-codes")


list_sub_cost_codes = Tool(
    name="list_sub_cost_codes",
    description=(
        "List ALL sub-cost-codes (roughly 500 rows, full details). This is "
        "an expensive tool — every row lands in the conversation context. "
        "Use only when the user truly needs the complete catalog (e.g. "
        "'how many are there?', 'show me all of them'). For name-based "
        "lookups, use `search_sub_cost_codes` instead. For a known "
        "identifier, use the matching `read_sub_cost_code_by_*` tool."
    ),
    input_schema=input_schema_from(NoArgs),
    handler=_list_sub_cost_codes,
)


async def _search_sub_cost_codes(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = SearchArgs(**args)
    return await ctx.call_api(
        "GET",
        f"/api/v1/get/sub-cost-code/search?q={quote(parsed.query)}&limit={parsed.limit}",
    )


search_sub_cost_codes = Tool(
    name="search_sub_cost_codes",
    description=(
        "Find sub-cost-codes by partial match against name, number, or "
        "alias. This is the default tool for name-based lookup — prefer "
        "it over `list_sub_cost_codes` whenever the user gives you a "
        "descriptive hint ('concrete', 'footers', 'SitePrep', '10.0', "
        "etc.). Exact-prefix matches rank above substring matches. "
        "Returns up to `limit` matching rows with full details."
    ),
    input_schema=input_schema_from(SearchArgs),
    handler=_search_sub_cost_codes,
)


async def _read_sub_cost_code_by_public_id(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = PublicIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/sub-cost-code/{parsed.public_id}"
    )


read_sub_cost_code_by_public_id = Tool(
    name="read_sub_cost_code_by_public_id",
    description=(
        "Fetch one sub-cost-code by its public_id (UUID). Use when you "
        "already have the public_id — typically from an earlier tool "
        "result, not from the end user."
    ),
    input_schema=input_schema_from(PublicIdArgs),
    handler=_read_sub_cost_code_by_public_id,
)


async def _read_sub_cost_code_by_number(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = NumberArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/sub-cost-code/by-number/{parsed.number}"
    )


read_sub_cost_code_by_number = Tool(
    name="read_sub_cost_code_by_number",
    description=(
        "Fetch one sub-cost-code by its number (e.g. `10.01`, `9.01`). "
        "This is the natural lookup when a user asks about a sub-cost-code "
        "by its human-facing code. Numbers are dotted `X.YY` — normalize "
        "`10-01` or `ten oh one` to `10.01` before calling."
    ),
    input_schema=input_schema_from(NumberArgs),
    handler=_read_sub_cost_code_by_number,
)


async def _read_sub_cost_code_by_alias(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = AliasArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/sub-cost-code/by-alias/{parsed.alias}"
    )


read_sub_cost_code_by_alias = Tool(
    name="read_sub_cost_code_by_alias",
    description=(
        "Fetch one sub-cost-code by a registered alias string (e.g. "
        "`SitePrep`). Use when the user refers to a sub-cost-code by a "
        "friendly/shorthand name that is not its number. Returns 404 if "
        "the alias is not registered — fall back to `list_sub_cost_codes` "
        "or ask the user to clarify."
    ),
    input_schema=input_schema_from(AliasArgs),
    handler=_read_sub_cost_code_by_alias,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateSubCostCodeArgs(BaseModel):
    number: str = Field(
        description=(
            "The new sub-cost-code's number in canonical `X.YY` format "
            "(e.g. `10.01`). Must not collide with an existing number."
        ),
    )
    name: str = Field(
        description="Human-readable name (e.g. `8\" Block`).",
    )
    cost_code_public_id: str = Field(
        description=(
            "Public UUID of the parent CostCode. Obtain it via "
            "`read_cost_code_by_id` on an existing SubCostCode's "
            "`cost_code_id`, or ask the user for it if unclear."
        ),
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional description (<=255 chars).",
    )
    aliases: Optional[str] = Field(
        default=None,
        description=(
            "Optional pipe-delimited alias values (e.g. `01.1|1.1`) that "
            "the search and lookup tools will match against."
        ),
    )


async def _create_sub_cost_code(args: dict, ctx: ToolContext) -> ToolResult:
    # Validate args shape before sending — pydantic will raise on missing
    # required fields, which surfaces as a ToolError (is_error=True) via
    # the loop's exception wrapping.
    parsed = CreateSubCostCodeArgs(**args)
    return await ctx.call_api(
        "POST",
        "/api/v1/create/sub-cost-code",
        body=parsed.model_dump(exclude_none=False),
    )


def _summarize_create_sub_cost_code(args: dict) -> str:
    number = args.get("number") or "?"
    name = args.get("name") or "?"
    return f"Create sub-cost-code {number} — {name}"


create_sub_cost_code = Tool(
    name="create_sub_cost_code",
    description=(
        "Create a new sub-cost-code. THIS TOOL REQUIRES USER APPROVAL — "
        "the user sees your proposed values in a card and can approve, "
        "edit, or reject before the row is actually created. Propose the "
        "tool call with your best-effort values; do not try to negotiate "
        "every field with the user before calling. If the user rejects or "
        "edits, you'll get a tool result you can reason about."
    ),
    input_schema=input_schema_from(CreateSubCostCodeArgs),
    handler=_create_sub_cost_code,
    requires_approval=True,
    approval_summary=_summarize_create_sub_cost_code,
)


class UpdateSubCostCodeArgs(BaseModel):
    public_id: str = Field(
        description="UUID of the sub-cost-code to update.",
    )
    row_version: str = Field(
        description=(
            "Base64 row version from the CURRENT record — pass the "
            "`row_version` value you got from the read. Used for "
            "optimistic concurrency; the update fails if another "
            "writer changed the row since you read it."
        ),
    )
    number: str = Field(
        description="The record's number in canonical `X.YY` format.",
    )
    name: str = Field(description="The record's name.")
    cost_code_public_id: str = Field(
        description=(
            "UUID of the parent CostCode. Obtain via "
            "`read_cost_code_by_id` using the current record's "
            "`cost_code_id`. Required even if you're not changing the "
            "parent — the endpoint needs the full field set."
        ),
    )
    description: Optional[str] = Field(default=None)
    aliases: Optional[str] = Field(
        default=None,
        description="Pipe-delimited aliases (`01.1|1.1`), or null.",
    )


async def _update_sub_cost_code(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateSubCostCodeArgs(**args)
    # public_id is in the URL; everything else goes in the body.
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT",
        f"/api/v1/update/sub-cost-code/{parsed.public_id}",
        body=body,
    )


def _summarize_update_sub_cost_code(args: dict) -> str:
    number = args.get("number") or "?"
    name = args.get("name") or "?"
    return f"Update sub-cost-code {number} — {name}"


update_sub_cost_code = Tool(
    name="update_sub_cost_code",
    description=(
        "Modify an existing sub-cost-code. REQUIRES USER APPROVAL. "
        "Workflow: (1) read the current record to get all fields and "
        "`row_version`; (2) call `read_cost_code_by_id` with the "
        "record's `cost_code_id` to obtain the parent's public_id; "
        "(3) propose `update_sub_cost_code` with the full field set, "
        "changing only what the user asked for. The approval card "
        "renders the NEW proposed state; in your prose response, be "
        "explicit about what's changing (e.g. 'I'll change the name "
        "from X to Y') so the user can evaluate the diff."
    ),
    input_schema=input_schema_from(UpdateSubCostCodeArgs),
    handler=_update_sub_cost_code,
    requires_approval=True,
    approval_summary=_summarize_update_sub_cost_code,
)


class DeleteSubCostCodeArgs(BaseModel):
    public_id: str = Field(
        description=(
            "The target sub-cost-code's public_id (UUID). Obtain it by "
            "reading the record first (e.g. search_sub_cost_codes or "
            "read_sub_cost_code_by_number)."
        ),
    )
    # Display hints for the approval card. NOT sent to the server —
    # server identifies the record by public_id only. Populate these
    # from the record you looked up so the user sees what's being
    # deleted without having to decode a UUID.
    number: Optional[str] = Field(
        default=None,
        description=(
            "The record's number (e.g. `99.99`) — shown on the approval "
            "card for context. Populate from the record you already "
            "fetched; this is NOT how the server identifies the row."
        ),
    )
    name: Optional[str] = Field(
        default=None,
        description=(
            "The record's name — shown on the approval card for "
            "context. Populate from the record you already fetched."
        ),
    )


async def _delete_sub_cost_code(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteSubCostCodeArgs(**args)
    return await ctx.call_api(
        "DELETE",
        f"/api/v1/delete/sub-cost-code/{parsed.public_id}",
    )


def _summarize_delete_sub_cost_code(args: dict) -> str:
    number = args.get("number")
    name = args.get("name")
    public_id = args.get("public_id") or "?"
    if number and name:
        return f"Delete sub-cost-code {number} — {name}"
    if number:
        return f"Delete sub-cost-code {number}"
    return f"Delete sub-cost-code {public_id}"


delete_sub_cost_code = Tool(
    name="delete_sub_cost_code",
    description=(
        "Permanently delete an existing sub-cost-code. REQUIRES USER "
        "APPROVAL. Before proposing the call, look up the record (via "
        "search_sub_cost_codes or read_sub_cost_code_by_number) and pass "
        "its `number` and `name` as args so the approval card shows a "
        "clear description. Only `public_id` is sent to the server; "
        "`number` and `name` are display hints for the user."
    ),
    input_schema=input_schema_from(DeleteSubCostCodeArgs),
    handler=_delete_sub_cost_code,
    requires_approval=True,
    approval_summary=_summarize_delete_sub_cost_code,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    list_sub_cost_codes,
    search_sub_cost_codes,
    read_sub_cost_code_by_public_id,
    read_sub_cost_code_by_number,
    read_sub_cost_code_by_alias,
    create_sub_cost_code,
    update_sub_cost_code,
    delete_sub_cost_code,
):
    register(_tool)
