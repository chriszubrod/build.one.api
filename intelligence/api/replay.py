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
from intelligence.persistence.approval_repo import AgentApprovalRequestRepo
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
    approval_repo = AgentApprovalRequestRepo()

    session = await asyncio.to_thread(session_repo.read_by_public_id, session_public_id)
    if session is None or session.id is None:
        yield LoopError(
            message=f"session {session_public_id} not found",
            code="not_found",
        )
        return

    # Pre-fetch approvals so we can inject their events at the right places.
    approvals = await asyncio.to_thread(
        approval_repo.read_by_session_id, session.id
    )
    approvals_by_request_id = {a.request_id: a for a in approvals if a.request_id}

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

            # If this tool call had an approval request, synthesize those
            # events here so replay reflects the pause/decision flow.
            approval = approvals_by_request_id.get(call.tool_use_id)
            if approval is not None:
                try:
                    proposed = (
                        json.loads(approval.proposed_input)
                        if approval.proposed_input
                        else {}
                    )
                except json.JSONDecodeError:
                    proposed = {}
                yield ApprovalRequest(
                    request_id=approval.request_id or "",
                    tool_name=approval.tool_name or "",
                    summary=approval.summary or f"Run {approval.tool_name}",
                    proposed_input=proposed,
                    input_schema={},  # schema not persisted; client degrades gracefully
                )
                final_input = None
                if approval.final_input:
                    try:
                        final_input = json.loads(approval.final_input)
                    except json.JSONDecodeError:
                        final_input = None
                yield ApprovalDecision(
                    request_id=approval.request_id or "",
                    decision=(
                        approval.status
                        if approval.status in ("approved", "rejected", "timed_out")
                        else "rejected"
                    ),
                    final_input=final_input,
                    decided_by=None,  # we store user id, not public_id; skip in replay
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
        replay_usage = Usage(
            input_tokens=session.total_input_tokens or 0,
            output_tokens=session.total_output_tokens or 0,
            # Cache token breakdown isn't persisted on AgentSession yet,
            # so a replay can't reproduce the exact warm-cache cost. The
            # cost surfaced here is an upper bound (treats everything as
            # uncached input).
        )
        from intelligence.observability.pricing import compute_cost_usd
        cost_usd = (
            compute_cost_usd(
                provider=session.provider, model=session.model, usage=replay_usage,
            )
            if session.provider and session.model
            else None
        )
        yield Done(
            reason=session.termination_reason or "end_turn",
            usage=replay_usage,
            cost_usd=cost_usd,
        )
