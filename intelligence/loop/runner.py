"""Single-agent think→act→observe loop.

Orchestrates one turn at a time:
  1. Call transport.stream() with current history + tool schemas.
  2. Relay text deltas upward as LoopEvents.
  3. When the assistant produces tool_use blocks, dispatch the corresponding
     handlers, then append a user message of tool_result blocks.
  4. If the turn ended with stop_reason == "tool_use", go back to step 1.
  5. Otherwise emit Done and exit.

Termination caps (BudgetPolicy) are checked after each turn.
"""
from typing import AsyncIterator, Optional

from intelligence.loop.events import (
    Done,
    LoopError,
    LoopEvent,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
    TurnStart,
)
from intelligence.loop.termination import BudgetPolicy
from intelligence.messages.types import (
    ContentBlock,
    Message,
    Text,
    ToolResult as ToolResultBlock,
    ToolUse,
)
from intelligence.tools import registry as tool_registry
from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.transport.base import Transport, Usage


async def run(
    *,
    transport: Transport,
    model: str,
    user_message: str,
    tools: list[Tool],
    ctx: ToolContext,
    system: Optional[str] = None,
    budget: Optional[BudgetPolicy] = None,
    max_tokens_per_turn: int = 4096,
    prior_history: Optional[list[Message]] = None,
) -> AsyncIterator[LoopEvent]:
    """Drive one agent run end-to-end. Yields LoopEvents as they happen.

    prior_history (optional) is prepended before the new user_message so the
    LLM sees a continuing conversation. Typically populated by
    session_runner when continuing a threaded session.
    """
    budget = budget or BudgetPolicy()
    tool_schemas = [t.to_anthropic_schema() for t in tools]
    by_name: dict[str, Tool] = {t.name: t for t in tools}

    history: list[Message] = list(prior_history or []) + [
        Message(role="user", content=[Text(text=user_message)]),
    ]
    total_usage = Usage()
    turn = 0

    while True:
        turn += 1
        if turn > budget.max_turns:
            yield Done(reason="max_turns", usage=total_usage)
            return

        yield TurnStart(turn=turn, model=model)

        assistant_blocks: list[ContentBlock] = []
        text_buf = ""
        pending_calls: list[ToolUse] = []
        stop_reason: Optional[str] = None
        turn_usage = Usage()
        errored = False

        async for ev in transport.stream(
            messages=history,
            model=model,
            system=system,
            max_tokens=max_tokens_per_turn,
            tools=tool_schemas or None,
        ):
            t = ev.type
            if t == "text_delta":
                text_buf += ev.text
                yield TextDelta(text=ev.text)
            elif t == "tool_use_start":
                # Flush any buffered text into a text block before the tool_use
                # block is recorded in assistant history. We defer emitting
                # ToolCallStart until tool_use_complete arrives with the input.
                if text_buf:
                    assistant_blocks.append(Text(text=text_buf))
                    text_buf = ""
            elif t == "tool_use_complete":
                block = ToolUse(id=ev.id, name=ev.name, input=ev.input)
                assistant_blocks.append(block)
                pending_calls.append(block)
                yield ToolCallStart(id=ev.id, name=ev.name, input=ev.input)
            elif t == "turn_end":
                stop_reason = ev.stop_reason
                if text_buf:
                    assistant_blocks.append(Text(text=text_buf))
                    text_buf = ""
            elif t == "done":
                turn_usage = ev.usage
            elif t == "error":
                yield LoopError(message=ev.message, code=ev.code)
                errored = True
                break
            # turn_start from transport is swallowed; loop emits its own TurnStart

        if errored:
            yield Done(reason="error", usage=total_usage)
            return

        yield TurnEnd(turn=turn, usage=turn_usage, stop_reason=stop_reason)

        total_usage = Usage(
            input_tokens=total_usage.input_tokens + turn_usage.input_tokens,
            output_tokens=total_usage.output_tokens + turn_usage.output_tokens,
        )

        if assistant_blocks:
            history.append(Message(role="assistant", content=assistant_blocks))

        if total_usage.input_tokens + total_usage.output_tokens > budget.max_tokens:
            yield Done(reason="max_tokens", usage=total_usage)
            return

        if not pending_calls:
            yield Done(reason=stop_reason or "end_turn", usage=total_usage)
            return

        # Dispatch tools and collect results into a single user message.
        result_blocks: list[ContentBlock] = []
        for call in pending_calls:
            tool = by_name.get(call.name) or tool_registry.get(call.name)
            if tool is None:
                result = ToolResult(
                    content=f"Unknown tool: {call.name}", is_error=True
                )
            else:
                try:
                    result = await tool.handler(call.input, ctx)
                except Exception as exc:  # noqa: BLE001 — surface any tool crash as a tool error
                    result = ToolResult(
                        content=f"Tool raised {type(exc).__name__}: {exc}",
                        is_error=True,
                    )
            yield ToolCallEnd(id=call.id, name=call.name, result=result)
            result_blocks.append(
                ToolResultBlock(
                    tool_use_id=call.id,
                    content=result.content,
                    is_error=result.is_error,
                )
            )
        history.append(Message(role="user", content=result_blocks))
        # fall through to next turn
