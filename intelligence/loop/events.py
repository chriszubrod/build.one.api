"""LoopEvent — what the think→act→observe loop emits upward.

LoopEvents are the public contract of the loop. Transport events are an
implementation detail that the loop translates into these. Downstream
consumers (the session_runner for persistence, the SSE API surface, the
dry-run script, observability) only ever see LoopEvents.
"""
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from intelligence.tools.base import ToolResult
from intelligence.transport.base import Usage  # noqa: F401 — re-exported for consumers


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


class ApprovalRequest(BaseModel):
    """Emitted when the loop pauses on a requires_approval tool.

    The consumer (UI, SSE subscriber, or persistence layer) presents
    this to the user, who responds via POST /runs/{id}/approve. The
    runner awaits a future keyed by request_id until the decision
    arrives (or the timeout fires).

    `session_public_id` identifies which session owns this request — so
    sub-agent approvals (forwarded onto a parent's stream) POST to the
    correct URL. Optional for backwards compatibility; runner populates
    it when known.
    """
    type: Literal["approval_request"] = "approval_request"
    request_id: str
    tool_name: str
    summary: str
    proposed_input: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    session_public_id: Optional[str] = None


class ApprovalDecision(BaseModel):
    """Emitted after the user (or timeout) resolves an ApprovalRequest.

    `decision` is "approved", "rejected", or "timed_out". When
    "approved", final_input holds the values that were actually
    executed — typically equal to proposed_input, but may differ when
    the user edited values before approving.
    """
    type: Literal["approval_decision"] = "approval_decision"
    request_id: str
    decision: Literal["approved", "rejected", "timed_out"]
    final_input: Optional[dict[str, Any]] = None
    decided_by: Optional[str] = None


class Done(BaseModel):
    type: Literal["done"] = "done"
    reason: str
    usage: Usage
    # Optional dollar cost computed by the loop runner using the agent's
    # provider + model. Null when pricing is unknown for that combo so
    # consumers can degrade gracefully to "tokens only" display.
    cost_usd: Optional[float] = None


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
    ApprovalRequest,
    ApprovalDecision,
    Done,
    LoopError,
]
