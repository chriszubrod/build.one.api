"""In-memory coordinator for pending approval requests.

When the loop hits a requires_approval tool, it registers a future
keyed by (session_public_id, request_id) and awaits it. The approval
endpoint resolves the future when the user's decision arrives. If no
decision lands before the timeout, the loop treats it as a rejection.

Mirrors the SessionChannel pattern in intelligence/api/channel.py —
all state is process-local, rebuilt on restart. A session that was
pending at the time of a server restart would see its approval
request orphaned; the user sees a stalled UI (we mark those sessions
as failed on session_runner exception).
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional


logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT_SECONDS = 300   # 5 minutes


@dataclass
class Decision:
    decision: str              # "approved" | "rejected" | "timed_out"
    final_input: Optional[dict[str, Any]] = None
    decided_by: Optional[str] = None    # user public_id for audit


# Registry: session_public_id → {request_id → Future[Decision]}
_pending: dict[str, dict[str, "asyncio.Future[Decision]"]] = {}
_lock = asyncio.Lock()


async def register(
    session_public_id: str, request_id: str
) -> "asyncio.Future[Decision]":
    """Create and register a future for this approval request.

    Returns the future the runner should await. If a future already
    exists for this (session, request) pair, returns it — idempotent
    from the runner's perspective.
    """
    async with _lock:
        by_request = _pending.setdefault(session_public_id, {})
        if request_id in by_request:
            return by_request[request_id]
        future: asyncio.Future[Decision] = asyncio.get_event_loop().create_future()
        by_request[request_id] = future
        return future


async def resolve(
    session_public_id: str,
    request_id: str,
    decision: Decision,
) -> bool:
    """Deliver a decision to the waiting runner. Returns True if a
    pending future was found and resolved; False if there is no
    matching pending request (e.g., already decided, timed out, or
    the run ended first).
    """
    async with _lock:
        by_request = _pending.get(session_public_id)
        if not by_request:
            return False
        future = by_request.pop(request_id, None)
        if future is None:
            return False
    if future.done():
        # Already resolved (e.g. by timeout). Not a fresh delivery.
        return False
    future.set_result(decision)
    return True


async def cleanup(session_public_id: str) -> None:
    """Remove all pending state for a session.

    Called when the run terminates so orphaned futures don't linger
    (and so re-running doesn't collide with stale registries).
    """
    async with _lock:
        by_request = _pending.pop(session_public_id, None)
    if not by_request:
        return
    for req_id, fut in by_request.items():
        if not fut.done():
            try:
                fut.set_result(
                    Decision(decision="timed_out", final_input=None)
                )
            except Exception:
                logger.exception(
                    "failed to cancel pending approval %s/%s during cleanup",
                    session_public_id,
                    req_id,
                )


async def await_decision(
    session_public_id: str,
    request_id: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Decision:
    """Wait for the user's decision. Returns a synthetic `timed_out`
    Decision if no decision arrives before the timeout.
    """
    future = await register(session_public_id, request_id)
    try:
        return await asyncio.wait_for(future, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        # Mark as timed_out and clear the registry entry so a late
        # POST /approve doesn't confuse us.
        async with _lock:
            by_request = _pending.get(session_public_id)
            if by_request is not None:
                by_request.pop(request_id, None)
        return Decision(decision="timed_out", final_input=None)
