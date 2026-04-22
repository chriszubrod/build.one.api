"""DB replay for completed sessions.

When a session has already finished and its in-memory channel has been
GC'd, a late SSE subscriber still needs the story. This module
synthesizes a LoopEvent sequence from the persisted AgentSession,
AgentTurn, and AgentToolCall rows.

Replay is best-effort — aggregates don't preserve per-delta timing or
partial JSON accumulation, so a replayed stream shows:
  TurnStart → TextDelta(full assistant text as one chunk) →
  ToolCallStart → ToolCallEnd (for each call) → TurnEnd
  → ... → Done
"""
import json
import logging
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
from intelligence.persistence.session_repo import (
    AgentSessionRepo,
    AgentToolCallRepo,
    AgentTurnRepo,
)
from intelligence.tools.base import ToolResult
from intelligence.transport.base import Usage


logger = logging.getLogger(__name__)


async def replay_session(session_public_id: str) -> AsyncIterator[LoopEvent]:
    """Yield a synthesized LoopEvent sequence for a completed session."""
    import asyncio

    session_repo = AgentSessionRepo()
    turn_repo = AgentTurnRepo()
    tool_repo = AgentToolCallRepo()

    session = await asyncio.to_thread(session_repo.read_by_public_id, session_public_id)
    if session is None or session.id is None:
        yield LoopError(
            message=f"session {session_public_id} not found",
            code="not_found",
        )
        return

    turns = await asyncio.to_thread(turn_repo.read_by_session_id, session.id)
    for turn in turns:
        yield TurnStart(turn=turn.turn_number or 0, model=turn.model or "")

        if turn.assistant_text:
            yield TextDelta(text=turn.assistant_text)

        calls = await asyncio.to_thread(tool_repo.read_by_turn_id, turn.id)
        for call in calls:
            try:
                tool_input = json.loads(call.tool_input) if call.tool_input else {}
            except json.JSONDecodeError:
                tool_input = {}
            yield ToolCallStart(
                id=call.tool_use_id or "",
                name=call.tool_name or "",
                input=tool_input,
            )
            yield ToolCallEnd(
                id=call.tool_use_id or "",
                name=call.tool_name or "",
                result=ToolResult(
                    content=call.tool_output or "",
                    is_error=bool(call.is_error),
                ),
            )

        yield TurnEnd(
            turn=turn.turn_number or 0,
            usage=Usage(
                input_tokens=turn.input_tokens or 0,
                output_tokens=turn.output_tokens or 0,
            ),
            stop_reason=turn.stop_reason,
        )

    # Final terminal event.
    if session.status == "failed":
        yield LoopError(
            message=session.error_message or "session failed",
            code="error",
        )
    else:
        yield Done(
            reason=session.termination_reason or "end_turn",
            usage=Usage(
                input_tokens=session.total_input_tokens or 0,
                output_tokens=session.total_output_tokens or 0,
            ),
        )
