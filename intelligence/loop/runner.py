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
import asyncio
import logging
from typing import AsyncIterator, Optional

from intelligence.loop import approval as approval_coordinator
from intelligence.loop.events import (
    ApprovalDecision,
    ApprovalRequest,
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
from intelligence.observability.pricing import compute_cost_usd
from intelligence.tools import registry as tool_registry
from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.transport.base import Transport, Usage


logger = logging.getLogger(__name__)


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
    provider: Optional[str] = None,
    session_public_id: Optional[str] = None,
    session_id: Optional[int] = None,
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
            yield Done(
                reason="max_turns",
                usage=total_usage,
                cost_usd=_cost(provider, model, total_usage),
            )
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
            yield Done(
                reason="error",
                usage=total_usage,
                cost_usd=_cost(provider, model, total_usage),
            )
            return

        yield TurnEnd(turn=turn, usage=turn_usage, stop_reason=stop_reason)

        total_usage = Usage(
            input_tokens=total_usage.input_tokens + turn_usage.input_tokens,
            output_tokens=total_usage.output_tokens + turn_usage.output_tokens,
            cache_creation_input_tokens=(
                total_usage.cache_creation_input_tokens
                + turn_usage.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                total_usage.cache_read_input_tokens
                + turn_usage.cache_read_input_tokens
            ),
        )

        if assistant_blocks:
            history.append(Message(role="assistant", content=assistant_blocks))

        if total_usage.input_tokens + total_usage.output_tokens > budget.max_tokens:
            yield Done(
                reason="max_tokens",
                usage=total_usage,
                cost_usd=_cost(provider, model, total_usage),
            )
            return

        if not pending_calls:
            yield Done(
                reason=stop_reason or "end_turn",
                usage=total_usage,
                cost_usd=_cost(provider, model, total_usage),
            )
            return

        # Dispatch tools concurrently. Each pending_call is handled by a
        # coroutine that pushes its events to a shared queue and writes
        # its final ToolResult into a slot indexed by the call's
        # position in pending_calls (preserving the order required by the
        # LLM's tool_result history block).
        result_blocks: list[ContentBlock] = []
        async for ev in _dispatch_tools_concurrently(
            pending_calls=pending_calls,
            by_name=by_name,
            ctx=ctx,
            session_public_id=session_public_id,
            session_id=session_id,
            result_blocks=result_blocks,
        ):
            yield ev

        history.append(Message(role="user", content=result_blocks))
        # fall through to next turn


async def _dispatch_tools_concurrently(
    *,
    pending_calls: list[ToolUse],
    by_name: dict[str, Tool],
    ctx: ToolContext,
    session_public_id: Optional[str],
    session_id: Optional[int],
    result_blocks: list[ContentBlock],
) -> AsyncIterator[LoopEvent]:
    """Run all tool handlers in parallel, yielding events as they happen.

    Approval requests, decisions, and tool_call_end events arrive on a
    shared queue and may interleave between concurrent dispatches —
    that's the whole point of running in parallel. Each event carries
    its own request_id / tool_use id so the UI can route it correctly
    even when interleaved.

    `result_blocks` is mutated in place (in pending_calls order) so the
    caller can append to history without depending on completion order.
    """
    if not pending_calls:
        return

    queue: asyncio.Queue = asyncio.Queue()
    sentinel = object()
    # Slot per call so we can place result_blocks in pending_calls order
    # regardless of dispatch completion order.
    results: list[Optional[ToolResult]] = [None] * len(pending_calls)

    async def _dispatch_one(idx: int, call: ToolUse) -> None:
        try:
            tool = by_name.get(call.name) or tool_registry.get(call.name)
            if tool is None:
                result = ToolResult(
                    content=f"Unknown tool: {call.name}", is_error=True
                )
                await queue.put(
                    ToolCallEnd(id=call.id, name=call.name, result=result)
                )
                results[idx] = result
                return

            final_input = call.input
            if tool.requires_approval:
                if session_public_id is None:
                    logger.warning(
                        "tool %s requires_approval but no session_public_id; "
                        "executing without approval",
                        tool.name,
                    )
                else:
                    summary = tool.describe_for_approval(call.input)
                    await queue.put(ApprovalRequest(
                        request_id=call.id,
                        tool_name=tool.name,
                        summary=summary,
                        proposed_input=call.input,
                        input_schema=tool.input_schema,
                        session_public_id=session_public_id,
                    ))
                    decision = await approval_coordinator.await_decision(
                        session_public_id=session_public_id,
                        request_id=call.id,
                        session_id=session_id,
                    )
                    await queue.put(ApprovalDecision(
                        request_id=call.id,
                        decision=decision.decision,
                        final_input=decision.final_input,
                        decided_by=decision.decided_by,
                    ))
                    if decision.decision == "approved":
                        final_input = (
                            decision.final_input
                            if decision.final_input is not None
                            else call.input
                        )
                    else:
                        reason = (
                            "The user rejected this action."
                            if decision.decision == "rejected"
                            else "The approval request timed out with no decision."
                        )
                        result = ToolResult(content=reason, is_error=True)
                        await queue.put(ToolCallEnd(
                            id=call.id, name=call.name, result=result
                        ))
                        results[idx] = result
                        return

            try:
                result = await tool.handler(final_input, ctx)
            except Exception as exc:  # noqa: BLE001 — surface tool crash as a tool error
                result = ToolResult(
                    content=f"Tool raised {type(exc).__name__}: {exc}",
                    is_error=True,
                )
            await queue.put(
                ToolCallEnd(id=call.id, name=call.name, result=result)
            )
            results[idx] = result
        finally:
            # Always signal completion so the drainer doesn't hang on
            # cancellation / unexpected exception paths.
            await queue.put(sentinel)

    tasks = [
        asyncio.create_task(_dispatch_one(i, c))
        for i, c in enumerate(pending_calls)
    ]

    completed = 0
    try:
        while completed < len(pending_calls):
            item = await queue.get()
            if item is sentinel:
                completed += 1
                continue
            yield item
    finally:
        # If the consumer aborted (e.g. parent run cancelled), make sure
        # the in-flight dispatch tasks don't leak. They'll finish their
        # cleanup quickly because every branch pushes sentinel via the
        # finally above.
        for t in tasks:
            if not t.done():
                t.cancel()

    # Build result_blocks in pending_calls order. None slots can only
    # happen if a dispatch task crashed before writing — in that case
    # synthesize an error block so the LLM sees something for that id.
    for call, result in zip(pending_calls, results):
        if result is None:
            result = ToolResult(
                content="Tool dispatch did not complete.", is_error=True
            )
        result_blocks.append(
            ToolResultBlock(
                tool_use_id=call.id,
                content=result.content,
                is_error=result.is_error,
            )
        )


def _cost(provider: Optional[str], model: str, usage: Usage) -> Optional[float]:
    if not provider:
        return None
    return compute_cost_usd(provider=provider, model=model, usage=usage)
