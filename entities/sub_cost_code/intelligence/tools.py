"""Agent-facing tools for the SubCostCode entity.

Read tools (no approval):
  list_sub_cost_codes                  → GET /api/v1/get/sub-cost-codes
  search_sub_cost_codes                → GET /api/v1/get/sub-cost-code/search?q=...&limit=...
  read_sub_cost_code_by_public_id      → GET /api/v1/get/sub-cost-code/{public_id}
  read_sub_cost_code_by_number         → GET /api/v1/get/sub-cost-code/by-number/{number}
  read_sub_cost_code_by_alias          → GET /api/v1/get/sub-cost-code/by-alias/{alias}

Write tools (user approval required):
  create_sub_cost_code                 → POST /api/v1/create/sub-cost-code

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


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    list_sub_cost_codes,
    search_sub_cost_codes,
    read_sub_cost_code_by_public_id,
    read_sub_cost_code_by_number,
    read_sub_cost_code_by_alias,
    create_sub_cost_code,
):
    register(_tool)
