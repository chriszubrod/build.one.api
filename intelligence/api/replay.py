"""DB-polled tail stream for sessions whose channel isn't on this worker.

When a subscriber lands on a different gunicorn worker than the one
running the loop, the in-memory `SessionChannel` registry has nothing
for that session. `tail_session` polls the AgentSession + AgentTurn +
AgentToolCall + AgentApprovalRequest tables and synthesizes a
LoopEvent stream from what's been persisted, yielding new pieces as
they appear and closing with Done/LoopError when the session reaches
a terminal state.

Also serves as the replay path for fully completed sessions: the loop
yields everything currently in the DB on the first cycle, sees that
the session status is terminal, and exits.

Streaming fidelity vs. the live channel:
  - TextDelta is emitted once per turn (the full assistant_text), not
    per-token, because we only persist the final text at turn_end.
  - Tool I/O, approval pause/decision, and turn boundaries appear as
    soon as they're persisted (~1.5s polling lag).
"""
import asyncio
import json
import logging
import time
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
from intelligence.observability.pricing import compute_cost_usd
from intelligence.persistence.approval_repo import AgentApprovalRequestRepo
from intelligence.persistence.session_repo import (
    AgentSessionRepo,
    AgentToolCallRepo,
    AgentTurnRepo,
)
from intelligence.tools.base import ToolResult
from intelligence.transport.base import Usage


logger = logging.getLogger(__name__)


POLL_INTERVAL_SECONDS = 1.5
# Hard cap on tail duration. Sessions that haven't reached a terminal
# state by then are considered orphaned (e.g. process died mid-run).
MAX_TAIL_SECONDS = 600
TERMINAL_STATUSES = ("completed", "failed")


async def tail_session(session_public_id: str) -> AsyncIterator[LoopEvent]:
    """Yield LoopEvents from DB state until the session reaches a terminal status.

    Idempotent on retries — every event is keyed by a stable id (turn id,
    tool_use_id, approval request_id) and yielded exactly once per stream.
    """
    session_repo = AgentSessionRepo()
    turn_repo = AgentTurnRepo()
    tool_repo = AgentToolCallRepo()
    approval_repo = AgentApprovalRequestRepo()

    # Bookkeeping — guarantees each synthetic event yields exactly once.
    yielded_turn_starts: set[int] = set()
    yielded_turn_text: set[int] = set()
    yielded_turn_ends: set[int] = set()
    yielded_tool_starts: set[str] = set()
    yielded_tool_ends: set[str] = set()
    yielded_approval_requests: set[str] = set()
    yielded_approval_decisions: set[str] = set()

    started_monotonic = time.monotonic()

    while True:
        session = await asyncio.to_thread(
            session_repo.read_by_public_id, session_public_id
        )
        if session is None or session.id is None:
            yield LoopError(
                message=f"session {session_public_id} not found",
                code="not_found",
            )
            return

        approvals = await asyncio.to_thread(
            approval_repo.read_by_session_id, session.id
        )
        approvals_by_request_id = {
            a.request_id: a for a in approvals if a.request_id
        }

        turns = await asyncio.to_thread(
            turn_repo.read_by_session_id, session.id
        )
        for turn in turns:
            if turn.id is None:
                continue

            if turn.id not in yielded_turn_starts:
                yield TurnStart(
                    turn=turn.turn_number or 0, model=turn.model or ""
                )
                yielded_turn_starts.add(turn.id)

            calls = await asyncio.to_thread(
                tool_repo.read_by_turn_id, turn.id
            )
            for call in calls:
                tool_use_id = call.tool_use_id or ""
                if not tool_use_id:
                    continue

                if tool_use_id not in yielded_tool_starts:
                    try:
                        tool_input = (
                            json.loads(call.tool_input)
                            if call.tool_input
                            else {}
                        )
                    except json.JSONDecodeError:
                        tool_input = {}
                    yield ToolCallStart(
                        id=tool_use_id,
                        name=call.tool_name or "",
                        input=tool_input,
                    )
                    yielded_tool_starts.add(tool_use_id)

                approval = approvals_by_request_id.get(tool_use_id)
                if approval is not None:
                    if approval.request_id not in yielded_approval_requests:
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
                            summary=(
                                approval.summary
                                or f"Run {approval.tool_name}"
                            ),
                            proposed_input=proposed,
                            input_schema={},  # not persisted; client degrades
                        )
                        yielded_approval_requests.add(approval.request_id)

                    if (
                        approval.status
                        and approval.status != "pending"
                        and approval.request_id
                        not in yielded_approval_decisions
                    ):
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
                                if approval.status
                                in ("approved", "rejected", "timed_out")
                                else "rejected"
                            ),
                            final_input=final_input,
                            decided_by=None,
                        )
                        yielded_approval_decisions.add(approval.request_id)

                # ToolCallEnd: only when the row is finalized.
                if (
                    call.completed_at is not None
                    and tool_use_id not in yielded_tool_ends
                ):
                    yield ToolCallEnd(
                        id=tool_use_id,
                        name=call.tool_name or "",
                        result=ToolResult(
                            content=call.tool_output or "",
                            is_error=bool(call.is_error),
                        ),
                    )
                    yielded_tool_ends.add(tool_use_id)

            # Turn-level closure events fire only when the turn has
            # actually completed (stop_reason set at turn_end time).
            if (
                turn.stop_reason is not None
                and turn.id not in yielded_turn_text
            ):
                if turn.assistant_text:
                    yield TextDelta(text=turn.assistant_text)
                yielded_turn_text.add(turn.id)

            if (
                turn.stop_reason is not None
                and turn.id not in yielded_turn_ends
            ):
                yield TurnEnd(
                    turn=turn.turn_number or 0,
                    usage=Usage(
                        input_tokens=turn.input_tokens or 0,
                        output_tokens=turn.output_tokens or 0,
                    ),
                    stop_reason=turn.stop_reason,
                )
                yielded_turn_ends.add(turn.id)

        # Terminal? Emit final event and exit.
        if session.status in TERMINAL_STATUSES:
            if session.status == "failed":
                yield LoopError(
                    message=session.error_message or "session failed",
                    code="error",
                )
            else:
                replay_usage = Usage(
                    input_tokens=session.total_input_tokens or 0,
                    output_tokens=session.total_output_tokens or 0,
                )
                cost_usd = (
                    compute_cost_usd(
                        provider=session.provider,
                        model=session.model,
                        usage=replay_usage,
                    )
                    if session.provider and session.model
                    else None
                )
                yield Done(
                    reason=session.termination_reason or "end_turn",
                    usage=replay_usage,
                    cost_usd=cost_usd,
                )
            return

        # Safety cap — give up tailing apparent zombies.
        if time.monotonic() - started_monotonic > MAX_TAIL_SECONDS:
            yield LoopError(
                message=(
                    f"tail timed out after {MAX_TAIL_SECONDS}s — "
                    f"session {session_public_id} still 'running'"
                ),
                code="orphaned",
            )
            return

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# Backwards-compat alias for older imports. tail_session subsumes the
# old replay_session — for fully completed sessions, the first poll
# cycle yields everything and the next iteration exits cleanly.
async def replay_session(
    session_public_id: str,
) -> AsyncIterator[LoopEvent]:
    async for ev in tail_session(session_public_id):
        yield ev
