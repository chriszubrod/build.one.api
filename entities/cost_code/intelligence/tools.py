"""Agent-facing tools for the CostCode entity.

CostCode is the BROAD parent category. SubCostCode is the fine-grained
child applied to line items. Keep these distinct when talking to users —
never refer to a SubCostCode as a "cost code" in a user-facing answer.

Scope today: a single read tool that resolves a CostCode by its internal
id. The reason it takes an internal id rather than a public_id: the
SubCostCode response surfaces the parent as `cost_code_id` (BIGINT FK).
Using that directly avoids having to expose a second UUID in SubCostCode
responses. The internal id is an implementation detail for agent-server
communication only — do not present it in user-facing text.
"""
from typing import Optional

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


class ReadCostCodeByIdArgs(BaseModel):
    id: int = Field(
        description=(
            "The CostCode's internal id (BIGINT). Obtain this from the "
            "`cost_code_id` field on a SubCostCode response. This is the "
            "internal identifier, not a public id — do not surface it in "
            "your answer to the user; refer to the CostCode by its number "
            "and name instead."
        ),
    )


async def _read_cost_code_by_id(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = ReadCostCodeByIdArgs(**args)
    return await ctx.call_api("GET", f"/api/v1/get/cost-code/by-id/{parsed.id}")


read_cost_code_by_id = Tool(
    name="read_cost_code_by_id",
    description=(
        "Resolve a parent CostCode from a SubCostCode's `cost_code_id`. "
        "Use this when you need the CostCode's number or name to give the "
        "user a complete answer about a SubCostCode. Example: after "
        "fetching sub-cost-code 10.01, call this with its `cost_code_id` "
        "to learn the parent category. Do NOT conflate CostCode (broad) "
        "with SubCostCode (fine-grained, applied to line items)."
    ),
    input_schema=input_schema_from(ReadCostCodeByIdArgs),
    handler=_read_cost_code_by_id,
)


class _NoArgs(BaseModel):
    pass


async def _list_cost_codes(args: dict, ctx: ToolContext) -> ToolResult:
    return await ctx.call_api("GET", "/api/v1/get/cost-codes")


list_cost_codes = Tool(
    name="list_cost_codes",
    description=(
        "List ALL CostCodes (the broad parent categories). The catalog "
        "is small (roughly 20-40 rows) so this is cheap. Use when the "
        "user asks about the catalog as a whole ('what CostCodes do "
        "we have?', 'how many categories?') or when you need to find a "
        "CostCode by name but don't have a public_id. Returns each row's "
        "number, name, public_id, and description."
    ),
    input_schema=input_schema_from(_NoArgs),
    handler=_list_cost_codes,
)


class ReadCostCodeByPublicIdArgs(BaseModel):
    public_id: str = Field(
        description="The CostCode's public_id (UUID string).",
    )


async def _read_cost_code_by_public_id(
    args: dict, ctx: ToolContext
) -> ToolResult:
    parsed = ReadCostCodeByPublicIdArgs(**args)
    return await ctx.call_api(
        "GET", f"/api/v1/get/cost-code/{parsed.public_id}"
    )


read_cost_code_by_public_id = Tool(
    name="read_cost_code_by_public_id",
    description=(
        "Fetch one CostCode by its public_id (UUID). Use when you "
        "already have the public_id — typically from an earlier "
        "`list_cost_codes` call or surfaced by another tool."
    ),
    input_schema=input_schema_from(ReadCostCodeByPublicIdArgs),
    handler=_read_cost_code_by_public_id,
)


# ─── Write tools (require user approval) ─────────────────────────────────

class CreateCostCodeArgs(BaseModel):
    number: str = Field(
        description=(
            "The CostCode's number (string, 1-50 chars). Typically short "
            "and numeric (e.g. `10`, `99`) but accepts any string the "
            "user chooses. Must not collide with an existing number."
        ),
    )
    name: str = Field(
        description="Human-readable name (e.g. `Block Walls`, 1-255 chars).",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional description (<=255 chars).",
    )


async def _create_cost_code(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = CreateCostCodeArgs(**args)
    return await ctx.call_api(
        "POST",
        "/api/v1/create/cost-code",
        body=parsed.model_dump(exclude_none=False),
    )


def _summarize_create_cost_code(args: dict) -> str:
    number = args.get("number") or "?"
    name = args.get("name") or "?"
    return f"Create cost code {number} — {name}"


create_cost_code = Tool(
    name="create_cost_code",
    description=(
        "Create a new CostCode (parent category). THIS TOOL REQUIRES "
        "USER APPROVAL — the user sees your proposed values in a card "
        "and can approve, edit, or reject before the row is created. "
        "Propose the tool call with your best-effort values; do not "
        "negotiate every field in prose first. If the user rejects or "
        "edits, you'll get a tool result you can reason about."
    ),
    input_schema=input_schema_from(CreateCostCodeArgs),
    handler=_create_cost_code,
    requires_approval=True,
    approval_summary=_summarize_create_cost_code,
)


class UpdateCostCodeArgs(BaseModel):
    public_id: str = Field(
        description="UUID of the CostCode to update.",
    )
    row_version: str = Field(
        description=(
            "Base64 row version from the CURRENT record — pass the "
            "`row_version` value from your most recent read. Used for "
            "optimistic concurrency; the update fails if another "
            "writer changed the row since you read it."
        ),
    )
    number: str = Field(description="The record's number.")
    name: str = Field(description="The record's name.")
    description: Optional[str] = Field(default=None)


async def _update_cost_code(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = UpdateCostCodeArgs(**args)
    body = parsed.model_dump(exclude={"public_id"}, exclude_none=False)
    return await ctx.call_api(
        "PUT",
        f"/api/v1/update/cost-code/{parsed.public_id}",
        body=body,
    )


def _summarize_update_cost_code(args: dict) -> str:
    number = args.get("number") or "?"
    name = args.get("name") or "?"
    return f"Update cost code {number} — {name}"


update_cost_code = Tool(
    name="update_cost_code",
    description=(
        "Modify an existing CostCode. REQUIRES USER APPROVAL. Workflow: "
        "(1) read the current record to get all fields and `row_version`; "
        "(2) propose `update_cost_code` with the full field set, "
        "changing only what the user asked for. The approval card "
        "shows the NEW proposed state; in your prose, be explicit "
        "about what's changing (e.g. 'I'll change the name from X to Y') "
        "so the user can evaluate the diff."
    ),
    input_schema=input_schema_from(UpdateCostCodeArgs),
    handler=_update_cost_code,
    requires_approval=True,
    approval_summary=_summarize_update_cost_code,
)


class DeleteCostCodeArgs(BaseModel):
    public_id: str = Field(
        description=(
            "The target CostCode's public_id (UUID). Obtain it by "
            "reading the record first."
        ),
    )
    # Display hints for the approval card. NOT sent to the server.
    number: Optional[str] = Field(
        default=None,
        description=(
            "The record's number — shown on the approval card for "
            "context. Populate from the record you already fetched."
        ),
    )
    name: Optional[str] = Field(
        default=None,
        description=(
            "The record's name — shown on the approval card for "
            "context. Populate from the record you already fetched."
        ),
    )


async def _delete_cost_code(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = DeleteCostCodeArgs(**args)
    return await ctx.call_api(
        "DELETE",
        f"/api/v1/delete/cost-code/{parsed.public_id}",
    )


def _summarize_delete_cost_code(args: dict) -> str:
    number = args.get("number")
    name = args.get("name")
    public_id = args.get("public_id") or "?"
    if number and name:
        return f"Delete cost code {number} — {name}"
    if number:
        return f"Delete cost code {number}"
    return f"Delete cost code {public_id}"


delete_cost_code = Tool(
    name="delete_cost_code",
    description=(
        "Permanently delete an existing CostCode. REQUIRES USER "
        "APPROVAL. Before proposing the call, look up the record and "
        "pass its `number` and `name` as display hints so the approval "
        "card shows a clear description. Only `public_id` is sent to "
        "the server; `number` and `name` are display hints for the user. "
        "Warn the user plainly if the CostCode has child SubCostCodes "
        "— deleting the parent may orphan them or be blocked by the "
        "server."
    ),
    input_schema=input_schema_from(DeleteCostCodeArgs),
    handler=_delete_cost_code,
    requires_approval=True,
    approval_summary=_summarize_delete_cost_code,
)


# ─── Self-register ───────────────────────────────────────────────────────

for _tool in (
    read_cost_code_by_id,
    list_cost_codes,
    read_cost_code_by_public_id,
    create_cost_code,
    update_cost_code,
    delete_cost_code,
):
    register(_tool)
