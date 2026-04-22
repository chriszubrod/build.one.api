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


register(read_cost_code_by_id)
