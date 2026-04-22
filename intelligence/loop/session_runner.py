"""Persistent wrapper around the core loop runner.

Creates an AgentSession row at start (Status='running'), records turns and
tool calls as events flow, and finalizes the session on Done or Error. The
underlying loop stays untouched and testable without a DB.

Events are yielded downstream unchanged so the SSE surface, dry-run script,
and any other consumer see the same stream as the non-persistent `run()`.

Note on sync DB calls inside an async generator: each repo method is wrapped
with `asyncio.to_thread`. Pyodbc is sync and the loop is async, so we bounce
DB work to a worker thread to keep the event stream flowing smoothly.
"""
import asyncio
import json
import logging
from typing import AsyncIterator, Optional

from intelligence.loop.events import LoopEvent
from intelligence.loop.runner import run
from intelligence.loop.termination import BudgetPolicy
from intelligence.persistence.history import load_chain_history
from intelligence.persistence.session_repo import (
    AgentSession,
    AgentSessionRepo,
    AgentToolCallRepo,
    AgentTurnRepo,
)
from intelligence.tools.base import Tool, ToolContext
from intelligence.transport.base import Transport


logger = logging.getLogger(__name__)


async def run_session(
    *,
    transport: Transport,
    provider: str,
    agent_name: str,
    model: str,
    user_message: str,
    tools: list[Tool],
    ctx: ToolContext,
    system: Optional[str] = None,
    budget: Optional[BudgetPolicy] = None,
    max_tokens_per_turn: int = 4096,
    agent_user_id: Optional[int] = None,
    requesting_user_id: Optional[int] = None,
    parent_session_id: Optional[int] = None,
    previous_session_id: Optional[int] = None,
    on_session_created: Optional[callable] = None,
) -> AsyncIterator[LoopEvent]:
    """Run an agent and durably record every turn and tool call.

    Yields the same LoopEvents as run() so downstream consumers are unaffected.
    On unhandled exceptions, the session row is marked failed before the
    exception propagates. An optional on_session_created callback fires once
    the session row exists — useful for surfacing session.public_id to the
    caller before any events flow.
    """
    session_repo = AgentSessionRepo()
    turn_repo = AgentTurnRepo()
    tool_call_repo = AgentToolCallRepo()

    # Create the session row synchronously (but off the event loop thread).
    session: AgentSession = await asyncio.to_thread(
        session_repo.create,
        agent_name=agent_name,
        model=model,
        provider=provider,
        user_message=user_message,
        agent_user_id=agent_user_id,
        requesting_user_id=requesting_user_id,
        parent_session_id=parent_session_id,
        previous_session_id=previous_session_id,
        system_prompt=system,
    )

    # If this is a continuation, synthesize prior conversation history from
    # the chain so the LLM sees a continuing dialogue rather than a fresh
    # single-message session.
    prior_history = None
    if previous_session_id is not None:
        prior_history = await load_chain_history(previous_session_id)
    if on_session_created is not None:
        try:
            on_session_created(session)
        except Exception:
            logger.exception("on_session_created callback raised")

    current_turn_id: Optional[int] = None
    current_turn_text_buf: str = ""
    # Map tool_use_id (LLM-generated) → AgentToolCall.Id (DB pk) so the
    # follow-up complete() call can find the right row.
    in_flight_tool_calls: dict[str, int] = {}

    finalized = False

    try:
        async for ev in run(
            transport=transport,
            model=model,
            user_message=user_message,
            tools=tools,
            ctx=ctx,
            system=system,
            budget=budget,
            max_tokens_per_turn=max_tokens_per_turn,
            prior_history=prior_history,
        ):
            t = ev.type

            if t == "turn_start":
                turn_row = await asyncio.to_thread(
                    turn_repo.create,
                    session_id=session.id,
                    turn_number=ev.turn,
                    model=ev.model,
                )
                current_turn_id = turn_row.id
                current_turn_text_buf = ""

            elif t == "text_delta":
                current_turn_text_buf += ev.text

            elif t == "tool_call_start":
                if current_turn_id is None:
                    logger.warning(
                        "tool_call_start before any turn row; skipping persistence"
                    )
                else:
                    row = await asyncio.to_thread(
                        tool_call_repo.create,
                        turn_id=current_turn_id,
                        tool_use_id=ev.id,
                        tool_name=ev.name,
                        tool_input=json.dumps(ev.input),
                    )
                    in_flight_tool_calls[ev.id] = row.id

            elif t == "tool_call_end":
                row_id = in_flight_tool_calls.pop(ev.id, None)
                if row_id is None:
                    logger.warning(
                        "tool_call_end with no matching create; tool_use_id=%s", ev.id
                    )
                else:
                    # Stringify the result content for storage.
                    if isinstance(ev.result.content, str):
                        output_text = ev.result.content
                    else:
                        try:
                            output_text = json.dumps(
                                [b.model_dump() for b in ev.result.content]
                            )
                        except Exception:
                            output_text = repr(ev.result.content)
                    await asyncio.to_thread(
                        tool_call_repo.complete,
                        id=row_id,
                        tool_output=output_text,
                        is_error=ev.result.is_error,
                    )

            elif t == "turn_end":
                if current_turn_id is not None:
                    await asyncio.to_thread(
                        turn_repo.complete,
                        id=current_turn_id,
                        input_tokens=ev.usage.input_tokens,
                        output_tokens=ev.usage.output_tokens,
                        stop_reason=ev.stop_reason,
                        assistant_text=current_turn_text_buf or None,
                    )
                    current_turn_id = None

            elif t == "done":
                await asyncio.to_thread(
                    session_repo.complete,
                    id=session.id,
                    termination_reason=ev.reason,
                    total_input_tokens=ev.usage.input_tokens,
                    total_output_tokens=ev.usage.output_tokens,
                )
                finalized = True

            elif t == "error":
                await asyncio.to_thread(
                    session_repo.fail,
                    id=session.id,
                    error_message=f"{ev.code or 'error'}: {ev.message}",
                )
                finalized = True

            yield ev

    except Exception as exc:
        if not finalized:
            try:
                await asyncio.to_thread(
                    session_repo.fail,
                    id=session.id,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
            except Exception:
                logger.exception("Failed to mark session as failed during exception handling")
        raise
