"""HTTP surface for agent runs — POST a run, stream events via SSE, cancel.

Endpoints:
  POST /api/v1/agents/{name}/runs              start a run
  GET  /api/v1/agents/runs/{public_id}/events  stream events (SSE)
  POST /api/v1/agents/runs/{public_id}/cancel  cancel (requester only)

Auth: bearer JWT or HttpOnly cookie — whatever `get_current_user_api`
accepts. RBAC gate: Modules.DASHBOARD (anyone with dashboard access can
invoke agents for now). Cancel requires the caller's user_id to match
the session's RequestingUserId.
"""
import asyncio
import json
import logging
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from intelligence.api import background
from intelligence.api import channel as session_channel
from intelligence.api.replay import replay_session
from intelligence.loop import approval as approval_coordinator
from intelligence.loop.events import LoopEvent
from intelligence.persistence.approval_repo import AgentApprovalRequestRepo
from intelligence.persistence.session_repo import AgentSessionRepo
from intelligence.registry import agents as agent_registry
from shared.api.responses import item_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "agents"])


# ─── Request bodies ──────────────────────────────────────────────────────

class RunStart(BaseModel):
    user_message: str


class ApprovalBody(BaseModel):
    request_id: str
    decision: Literal["approve", "reject", "edit"]
    edited_input: Optional[dict[str, Any]] = None


# ─── Helpers ─────────────────────────────────────────────────────────────

def _event_to_sse(event: LoopEvent) -> bytes:
    """Format a LoopEvent as an SSE record: `event: <type>\\ndata: <json>\\n\\n`."""
    payload = event.model_dump_json()
    return f"event: {event.type}\ndata: {payload}\n\n".encode("utf-8")


async def _live_stream(
    public_id: str, request: Request
) -> AsyncIterator[bytes]:
    """Subscribe to a live channel and emit SSE records until it ends or client disconnects."""
    channel = await session_channel.get(public_id)
    if channel is None:
        return

    async for event in channel.subscribe():
        if await request.is_disconnected():
            return
        yield _event_to_sse(event)


async def _replay_stream(
    public_id: str, request: Request
) -> AsyncIterator[bytes]:
    """Synthesize events from the DB for a completed session with no live channel."""
    async for event in replay_session(public_id):
        if await request.is_disconnected():
            return
        yield _event_to_sse(event)


async def _sse_generator(
    public_id: str, request: Request
) -> AsyncIterator[bytes]:
    """Pick live vs. replay and stream accordingly."""
    channel = await session_channel.get(public_id)
    if channel is not None:
        async for chunk in _live_stream(public_id, request):
            yield chunk
    else:
        async for chunk in _replay_stream(public_id, request):
            yield chunk


# ─── Endpoints ───────────────────────────────────────────────────────────

@router.post("/agents/{name}/runs")
async def start_agent_run(
    name: str,
    body: RunStart,
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
):
    """Start an agent run. Returns session_public_id; run continues in background."""
    if agent_registry.get(name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {name}",
        )

    requesting_user_id = current_user.get("id") or current_user.get("user_id")
    try:
        public_id = await background.start_run(
            agent_name=name,
            user_message=body.user_message,
            requesting_user_id=requesting_user_id,
        )
    except Exception as exc:
        logger.exception("failed to start agent run")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start run: {exc}",
        )

    return item_response({
        "session_public_id": public_id,
        "agent": name,
    })


@router.get("/agents/runs/{public_id}/events")
async def stream_agent_run_events(
    public_id: str,
    request: Request,
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
):
    """Stream LoopEvents as SSE. Live if the run is active; replayed from DB otherwise."""
    # Confirm the session exists before opening the stream.
    repo = AgentSessionRepo()
    session = await asyncio.to_thread(repo.read_by_public_id, public_id)
    if session is None:
        raise_not_found("Agent session")

    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "X-Accel-Buffering": "no",  # disable proxy buffering
    }
    return StreamingResponse(
        _sse_generator(public_id, request),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/agents/runs/{public_id}/continue")
async def continue_agent_run(
    public_id: str,
    body: RunStart,
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
):
    """Continue a conversation by posting a follow-up user_message.

    Creates a NEW AgentSession whose PreviousSessionId points at the prior
    head. The loop loads prior conversation history so the LLM sees the
    full dialogue. Only the original requester may continue.
    """
    repo = AgentSessionRepo()
    prior = await asyncio.to_thread(repo.read_by_public_id, public_id)
    if prior is None:
        raise_not_found("Agent session")
    if prior.agent_name is None or prior.id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Prior session is malformed; cannot continue.",
        )

    caller_id = current_user.get("id") or current_user.get("user_id")

    def _coerce(x) -> Optional[int]:
        if x is None:
            return None
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    if _coerce(caller_id) != _coerce(prior.requesting_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who started the conversation may continue it.",
        )

    if agent_registry.get(prior.agent_name) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {prior.agent_name}",
        )

    try:
        new_public_id = await background.start_run(
            agent_name=prior.agent_name,
            user_message=body.user_message,
            requesting_user_id=_coerce(caller_id),
            previous_session_id=prior.id,
        )
    except Exception as exc:
        logger.exception("failed to continue agent run")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to continue run: {exc}",
        )

    return item_response({
        "session_public_id": new_public_id,
        "previous_session_public_id": public_id,
        "agent": prior.agent_name,
    })


@router.post("/agents/runs/{public_id}/approve")
async def approve_agent_action(
    public_id: str,
    body: ApprovalBody,
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
):
    """Resolve a pending ApprovalRequest with the caller's decision.

    Only the user who started the session may approve. `decision`:
      - "approve" → run tool with proposed_input
      - "edit"    → run tool with edited_input (must be provided)
      - "reject"  → skip execution; synthetic tool error back to the LLM
    """
    # Look up session to enforce requester match.
    session_repo = AgentSessionRepo()
    session = await asyncio.to_thread(session_repo.read_by_public_id, public_id)
    if session is None or session.id is None:
        raise_not_found("Agent session")

    caller_id = current_user.get("id") or current_user.get("user_id")

    def _coerce(x) -> Optional[int]:
        if x is None:
            return None
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    if _coerce(caller_id) != _coerce(session.requesting_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who started the run may approve actions in it.",
        )

    # Look up the pending approval row.
    approval_repo = AgentApprovalRequestRepo()
    approval = await asyncio.to_thread(
        approval_repo.read_by_session_request,
        session.id,
        body.request_id,
    )
    if approval is None or approval.id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No approval request {body.request_id!r} for this session.",
        )
    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval already decided (status={approval.status}).",
        )

    # Map API decision → internal status + final_input
    if body.decision == "approve":
        final_input: Optional[dict[str, Any]] = None  # use proposed
        try:
            final_input = json.loads(approval.proposed_input or "{}")
        except json.JSONDecodeError:
            final_input = {}
        internal_status = "approved"
    elif body.decision == "edit":
        if body.edited_input is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="decision='edit' requires edited_input.",
            )
        final_input = body.edited_input
        internal_status = "approved"
    else:  # reject
        final_input = None
        internal_status = "rejected"

    caller_public_id = current_user.get("sub")
    decision_obj = approval_coordinator.Decision(
        decision=internal_status,
        final_input=final_input,
        decided_by=caller_public_id,
    )

    # Deliver to the waiting runner. If the runner already moved on
    # (timeout/crash), resolve() returns False and we still record the
    # decision for audit — the row moves out of 'pending'.
    await approval_coordinator.resolve(
        public_id, body.request_id, decision_obj
    )

    # Persist the decision with the caller's user id. Even if the runner
    # didn't receive it, the audit row is accurate.
    await asyncio.to_thread(
        approval_repo.set_decision,
        id=approval.id,
        status=internal_status,
        final_input=(
            json.dumps(final_input) if final_input is not None else None
        ),
        decided_by_user_id=_coerce(caller_id),
    )

    return item_response({
        "request_id": body.request_id,
        "status": internal_status,
    })


@router.post("/agents/runs/{public_id}/cancel")
async def cancel_agent_run(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.DASHBOARD)),
):
    """Cancel a running session. Only the user who started it may cancel."""
    repo = AgentSessionRepo()
    session = await asyncio.to_thread(repo.read_by_public_id, public_id)
    if session is None:
        raise_not_found("Agent session")

    caller_id = current_user.get("id") or current_user.get("user_id")
    # Defensive int coerce — RequestingUserId is BIGINT in DB; current_user
    # may carry it as str or int depending on upstream auth quirks.
    def _coerce(x) -> Optional[int]:
        if x is None:
            return None
        try:
            return int(x)
        except (TypeError, ValueError):
            return None

    if _coerce(caller_id) != _coerce(session.requesting_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the user who started the run may cancel it.",
        )

    cancelled = await background.cancel_run(public_id)
    return item_response({"cancelled": cancelled})
