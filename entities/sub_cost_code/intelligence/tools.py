"""Agent-facing tools for the SubCostCode entity.

Four read-only tools wrapping existing GET endpoints:

  list_sub_cost_codes                  → GET /api/v1/get/sub-cost-codes
  read_sub_cost_code_by_public_id      → GET /api/v1/get/sub-cost-code/{public_id}
  read_sub_cost_code_by_number         → GET /api/v1/get/sub-cost-code/by-number/{number}
  read_sub_cost_code_by_alias          → GET /api/v1/get/sub-cost-code/by-alias/{alias}

Each tool calls ctx.call_api(), which means every invocation goes through
the same FastAPI stack a human request does: RBAC, JSON envelope, HTTP
access log. The agent's bearer token on ctx auths the call.

Tools self-register on import. Agents pick them from the registry by name.
"""
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


# ─── Tools ───────────────────────────────────────────────────────────────

async def _list_sub_cost_codes(args: dict, ctx: ToolContext) -> ToolResult:
    return await ctx.call_api("GET", "/api/v1/get/sub-cost-codes")


list_sub_cost_codes = Tool(
    name="list_sub_cost_codes",
    description=(
        "List all sub-cost-codes. Returns every row with full details "
        "(public_id, number, name, parent cost_code reference). Use when "
        "the user wants the complete catalog, or when you need to browse "
        "to find one whose identifier you do not know. Prefer the "
        "`read_sub_cost_code_by_*` variants when the user has already "
        "supplied a specific identifier."
    ),
    input_schema=input_schema_from(NoArgs),
    handler=_list_sub_cost_codes,
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


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    list_sub_cost_codes,
    read_sub_cost_code_by_public_id,
    read_sub_cost_code_by_number,
    read_sub_cost_code_by_alias,
):
    register(_tool)
