"""Trivial tools for wiring validation. NOT scout's real tool set.

`now` — no arguments, validates the zero-arg path.
`add` — two numeric arguments, validates the argument-parsing path.

These self-register at import time. Scripts that want a clean registry
can import `intelligence.tools.registry` and call `clear()` first.
"""
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.registry import register
from intelligence.tools.schema import input_schema_from


class NowArgs(BaseModel):
    pass


async def _now_handler(args: dict, ctx: ToolContext) -> ToolResult:
    return ToolResult(content=datetime.now(timezone.utc).isoformat())


now = Tool(
    name="now",
    description="Return the current UTC time in ISO 8601 format. Takes no arguments.",
    input_schema=input_schema_from(NowArgs),
    handler=_now_handler,
)


class AddArgs(BaseModel):
    a: float = Field(description="First addend")
    b: float = Field(description="Second addend")


async def _add_handler(args: dict, ctx: ToolContext) -> ToolResult:
    parsed = AddArgs(**args)
    return ToolResult(content=str(parsed.a + parsed.b))


add = Tool(
    name="add",
    description="Add two numbers. Returns the sum as a string.",
    input_schema=input_schema_from(AddArgs),
    handler=_add_handler,
)


register(now)
register(add)
