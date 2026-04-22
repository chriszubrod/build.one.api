"""Background-task driver for SSE-surfaced agent runs.

Wraps run_agent() so that:
  1. A SessionChannel is registered before the first event flows.
  2. Every LoopEvent is published to the channel as it arrives.
  3. The run's asyncio.Task is tracked by session_public_id so it can be
     cancelled via POST /runs/{id}/cancel.
  4. When the run ends, the channel is scheduled for grace-window cleanup.

The caller (POST /runs handler) receives the session row's public_id via
a Future that resolves as soon as the AgentSession is created — so the
HTTP response can return immediately with a usable id even though the
run itself is still in progress.
"""
import asyncio
import logging
from typing import Optional

from intelligence.api import channel as session_channel
from intelligence.loop.events import LoopError
from intelligence.run import run_agent


logger = logging.getLogger(__name__)


# Module-level task registry so the cancel endpoint can find the task by
# session_public_id. asyncio.Task entries are removed when the task finishes.
_tasks: dict[str, asyncio.Task] = {}
_tasks_lock = asyncio.Lock()


async def start_run(
    *,
    agent_name: str,
    user_message: str,
    requesting_user_id: Optional[int] = None,
    parent_session_id: Optional[int] = None,
) -> str:
    """Start an agent run in the background. Returns session_public_id.

    Does not return until the AgentSession row has been created so the
    public_id is meaningful to the caller. The actual LLM calls and tool
    dispatches happen after this returns.
    """
    session_public_id_future: asyncio.Future[str] = asyncio.Future()

    def _on_session_created(session) -> None:
        if not session_public_id_future.done():
            session_public_id_future.set_result(session.public_id)

    async def _run_and_publish() -> None:
        channel = None
        public_id: Optional[str] = None
        try:
            async for ev in run_agent(
                name=agent_name,
                user_message=user_message,
                requesting_user_id=requesting_user_id,
                parent_session_id=parent_session_id,
                on_session_created=_on_session_created,
            ):
                if channel is None:
                    # First event implies the session row now exists — the
                    # on_session_created callback ran before run_session
                    # yielded anything. Register the channel here under the
                    # public_id we learned.
                    public_id = session_public_id_future.result()
                    channel = await session_channel.register(public_id)
                channel.publish(ev)
        except asyncio.CancelledError:
            # Cancellation arrived mid-run. Surface it as an error event on
            # the channel so SSE subscribers see a clean end-of-stream.
            if channel is not None:
                channel.publish(
                    LoopError(message="run cancelled", code="cancelled")
                )
            raise
        except Exception as exc:
            logger.exception("agent run failed")
            if channel is not None:
                channel.publish(
                    LoopError(message=f"{type(exc).__name__}: {exc}", code="exception")
                )
            else:
                # Session row creation itself failed — nothing to publish to.
                # The exception is logged; the POST /runs caller already
                # failed via the future if we never resolved it.
                if not session_public_id_future.done():
                    session_public_id_future.set_exception(exc)
        finally:
            if public_id is not None:
                async with _tasks_lock:
                    _tasks.pop(public_id, None)
                asyncio.create_task(session_channel.schedule_cleanup(public_id))

    task = asyncio.create_task(_run_and_publish())

    # Wait until the session row is created (or the run errors early) before
    # returning. This is fast — CreateAgentSession is one sproc call.
    try:
        public_id = await session_public_id_future
    except Exception:
        # Surface the initial-setup error to the HTTP caller.
        raise

    async with _tasks_lock:
        _tasks[public_id] = task

    return public_id


async def cancel_run(session_public_id: str) -> bool:
    """Cancel an in-flight run. Returns True if a task was cancelled."""
    async with _tasks_lock:
        task = _tasks.get(session_public_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True
