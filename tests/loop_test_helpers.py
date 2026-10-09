"""Shared fakes for driving the intelligence loop and transports without a network.

Imported by the U-641 and U-642 test modules (the same way tests/sproc_text.py is
shared): one scripted transport, one tool fixture, one runner harness.
"""

import asyncio
from typing import Any, AsyncIterator

import intelligence.transport.base as tb
from intelligence.loop.runner import run
from intelligence.messages.types import Message, Text
from intelligence.observability.pricing import compute_cost_usd
from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.transport.anthropic import _build_request_body
from intelligence.transport.base import Usage


HAIKU = "claude-haiku-5-5"
SONNET_55 = "claude-sonnet-5-5"
LEGACY_SONNET = "claude-sonnet-4-6"
LEGACY_HAIKU = "claude-haiku-4-5-20251001"
SAMPLING = {"temperature": 0, "top_p": 0.5, "top_k": 10}
HINT = {"thinking": "off", "reasoning_effort": "minimal"}
LOOKUP_TOOL = {"name": "lookup", "description": "d", "input_schema": {"type": "object", "properties": {}}}


class ScriptedTransport:
    """Yields one scripted event list per call and records every request.

    The runner appends to its history list in place, so each request's messages
    are snapshotted (a shallow copy: the runner never edits a Message)."""

    def __init__(self, turns: list[list[Any]]):
        self._turns = turns
        self.calls = 0
        self.seen_messages: list[list[Message]] = []
        self.seen_extra_body: list[Any] = []

    async def stream(
        self,
        messages,
        model,
        system=None,
        max_tokens=4096,
        tools=None,
        extra_body=None,
    ) -> AsyncIterator[Any]:
        self.seen_messages.append(list(messages))
        self.seen_extra_body.append(extra_body)
        script = self._turns[self.calls]
        self.calls += 1
        for event in script:
            yield event


async def agen(items):
    for item in items:
        yield item


async def alist(agen_) -> list[Any]:
    return [ev async for ev in agen_]


async def _lookup(args, ctx):
    return ToolResult(content="42")


LOOKUP = Tool(
    name="lookup",
    description="look a value up",
    input_schema={"type": "object", "properties": {}},
    handler=_lookup,
)


def tool_turn(model: str, input_tokens: int) -> list[Any]:
    return [
        tb.TurnStart(model=model),
        tb.ToolUseComplete(id="toolu_1", name="lookup", input={"q": "x"}),
        tb.TurnEnd(stop_reason="tool_use"),
        tb.Done(usage=Usage(input_tokens=input_tokens)),
    ]


def final_turn(model: str, input_tokens: int) -> list[Any]:
    return [
        tb.TurnStart(model=model),
        tb.TextDelta(text="all done"),
        tb.TurnEnd(stop_reason="end_turn"),
        tb.Done(usage=Usage(input_tokens=input_tokens)),
    ]


def error_turn(model: str) -> list[Any]:
    return [
        tb.TurnStart(model=model),
        tb.TransportError(message="upstream exploded", code="upstream"),
    ]


def drive(transport: ScriptedTransport, model: str = HAIKU, **run_kwargs) -> list[Any]:
    """Run the real loop against a scripted transport and collect its events."""
    return asyncio.run(alist(run(
        transport=transport,
        model=model,
        user_message="go",
        tools=[LOOKUP],
        ctx=ToolContext(),
        **run_kwargs,
    )))


def user_text(text: str) -> Message:
    return Message(role="user", content=[Text(text=text)])


def build(model, extra_body, messages=None, system=None, tools=None):
    """The Anthropic request body for a one-message conversation."""
    return _build_request_body(
        messages or [user_text("hi")],
        model=model, system=system, max_tokens=1024, tools=tools, extra_body=extra_body,
    )


def cost(model, provider="anthropic", **usage):
    """Dollar cost of one request's usage at the table rate for `model`."""
    return compute_cost_usd(provider=provider, model=model, usage=Usage(**usage))
