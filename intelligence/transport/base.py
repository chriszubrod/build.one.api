"""Transport abstraction — the provider-SDK boundary.

A Transport streams canonical TransportEvents for a given list of canonical
Messages. Provider-specific wire formats (Anthropic SSE, OpenAI SSE, etc.)
are translated inside each adapter and never leak above this layer.
"""
from typing import Any, AsyncIterator, Literal, Optional, Protocol, Union

from pydantic import BaseModel, Field

from intelligence.messages.types import Message


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class TurnStart(BaseModel):
    type: Literal["turn_start"] = "turn_start"
    model: str


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolUseStart(BaseModel):
    type: Literal["tool_use_start"] = "tool_use_start"
    id: str
    name: str


class ToolUseComplete(BaseModel):
    type: Literal["tool_use_complete"] = "tool_use_complete"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class TurnEnd(BaseModel):
    type: Literal["turn_end"] = "turn_end"
    stop_reason: Optional[str] = None


class Done(BaseModel):
    type: Literal["done"] = "done"
    usage: Usage


class TransportError(BaseModel):
    type: Literal["error"] = "error"
    message: str
    code: Optional[str] = None


TransportEvent = Union[
    TurnStart,
    TextDelta,
    ToolUseStart,
    ToolUseComplete,
    TurnEnd,
    Done,
    TransportError,
]


class Transport(Protocol):
    def stream(
        self,
        messages: list[Message],
        model: str,
        system: Optional[str] = None,
        max_tokens: int = 4096,
        tools: Optional[list[dict[str, Any]]] = None,
    ) -> AsyncIterator[TransportEvent]: ...
