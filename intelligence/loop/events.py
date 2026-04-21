"""LoopEvent — what the think→act→observe loop emits upward.

LoopEvents are the public contract of the loop. Transport events are an
implementation detail that the loop translates into these. Downstream
consumers (the session_runner for persistence, the SSE API surface, the
dry-run script, observability) only ever see LoopEvents.
"""
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from intelligence.tools.base import ToolResult
from intelligence.transport.base import Usage


class TurnStart(BaseModel):
    type: Literal["turn_start"] = "turn_start"
    turn: int
    model: str


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolCallStart(BaseModel):
    type: Literal["tool_call_start"] = "tool_call_start"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolCallEnd(BaseModel):
    type: Literal["tool_call_end"] = "tool_call_end"
    id: str
    name: str
    result: ToolResult


class TurnEnd(BaseModel):
    type: Literal["turn_end"] = "turn_end"
    turn: int
    usage: Usage
    stop_reason: Optional[str] = None


class Done(BaseModel):
    type: Literal["done"] = "done"
    reason: str
    usage: Usage


class LoopError(BaseModel):
    type: Literal["error"] = "error"
    message: str
    code: Optional[str] = None


LoopEvent = Union[
    TurnStart,
    TextDelta,
    ToolCallStart,
    ToolCallEnd,
    TurnEnd,
    Done,
    LoopError,
]
