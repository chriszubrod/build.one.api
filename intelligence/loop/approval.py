"""Cross-worker coordinator for pending approval requests.

When the loop hits a requires_approval tool, it registers an in-memory
future keyed by (session_public_id, request_id) AND starts a DB-polling
task on the AgentApprovalRequest row. Whichever signals first wins.

Why both: the /approve endpoint may land on a different gunicorn worker
than the one running the loop (with `-w 2` in prod). The in-memory
`_pending` dict is process-local, so a cross-worker /approve resolves
Worker-B's future (which no one awaits) while Worker-A's loop blocks.
The DB poll is the fallback — every worker persists the decision row,
so any worker can observe it. Happy path (same worker) still wins in
milliseconds via the in-memory future.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional


logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT_SECONDS = 300   # 5 minutes
DB_POLL_INTERVAL_SECONDS = 1.5  # cross-worker fallback cadence


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
    session_id: Optional[int] = None,
) -> Decision:
    """Wait for the user's decision. Returns a synthetic `timed_out`
    Decision if no decision arrives before the timeout.

    When `session_id` is supplied, a DB-polling task runs in parallel
    with the in-memory future so cross-worker /approve requests are
    observed even when they resolve another worker's future.
    """
    future = await register(session_public_id, request_id)

    # If we have no session_id, fall back to future-only behavior
    # (e.g. dry-run scripts without persistence).
    if session_id is None:
        try:
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            async with _lock:
                by_request = _pending.get(session_public_id)
                if by_request is not None:
                    by_request.pop(request_id, None)
            return Decision(decision="timed_out", final_input=None)

    poll_task = asyncio.create_task(
        _poll_db_for_decision(session_id, request_id)
    )
    future_task = asyncio.ensure_future(future)

    try:
        done, pending = await asyncio.wait(
            {future_task, poll_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        poll_task.cancel()
        raise

    if not done:
        # Timed out without any decision.
        poll_task.cancel()
        async with _lock:
            by_request = _pending.get(session_public_id)
            if by_request is not None:
                by_request.pop(request_id, None)
        return Decision(decision="timed_out", final_input=None)

    # Whichever task finished first wins. Cancel the loser.
    for t in pending:
        t.cancel()

    winner = next(iter(done))
    try:
        return winner.result()
    except Exception:
        logger.exception(
            "approval decision task raised for session=%s request=%s",
            session_public_id, request_id,
        )
        return Decision(decision="timed_out", final_input=None)


async def _poll_db_for_decision(
    session_id: int, request_id: str
) -> Decision:
    """Poll AgentApprovalRequest until its Status leaves 'pending'.

    Returns a Decision synthesized from the persisted row. Imported
    lazily to avoid a circular import at module load.
    """
    from intelligence.persistence.approval_repo import (
        AgentApprovalRequestRepo,
    )
    repo = AgentApprovalRequestRepo()

    while True:
        await asyncio.sleep(DB_POLL_INTERVAL_SECONDS)
        row = await asyncio.to_thread(
            repo.read_by_session_request, session_id, request_id
        )
        if row is None or row.status == "pending" or row.status is None:
            continue
        final_input: Optional[dict[str, Any]] = None
        if row.final_input:
            try:
                final_input = json.loads(row.final_input)
            except json.JSONDecodeError:
                logger.warning(
                    "approval row %s has non-JSON FinalInput; ignoring",
                    request_id,
                )
        return Decision(
            decision=row.status,
            final_input=final_input,
            decided_by=None,
        )
