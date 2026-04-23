"""
In-process publish/subscribe for profile-change events.

Used by the `GET /api/v1/auth/me/changes` SSE stream to notify a logged-in
user that their profile/permissions have been mutated (e.g. an admin
changed their role). Consumers invalidate their cached `/me` response on
receipt.

Scope: single worker process. Under `-w 2` gunicorn, an event published on
worker A will not reach an SSE subscriber anchored on worker B — accepted
trade-off ("B-lite"). Cross-worker delivery is covered by the React
client's `refetchOnWindowFocus: true` on `['me']` plus the server-side
`_permission_cache` still being busted globally via `invalidate_all_caches()`.

Call `register_event_loop()` once during FastAPI startup so publishes from
sync route handlers (threadpool context) can dispatch safely back to the
event loop via `call_soon_threadsafe`.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

_QUEUE_MAX = 16

_subscribers: dict[int, set[asyncio.Queue]] = {}
_loop: Optional[asyncio.AbstractEventLoop] = None


def register_event_loop() -> None:
    """Capture the running event loop for cross-thread publish dispatch."""
    global _loop
    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        _loop = asyncio.get_event_loop()


@asynccontextmanager
async def profile_event_subscription(user_id: int) -> AsyncIterator[asyncio.Queue]:
    """
    Open a subscription to a user's profile events. Usage:

        async with profile_event_subscription(user_id) as queue:
            while True:
                event = await queue.get()
                yield event
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subscribers.setdefault(user_id, set()).add(queue)
    try:
        yield queue
    finally:
        subs = _subscribers.get(user_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                _subscribers.pop(user_id, None)


def publish_profile_changed(user_id: int) -> None:
    """
    Fire-and-forget: notify any active SSE subscribers for `user_id` that
    their profile/permissions may have changed. Safe to call from sync
    threadpool code (routes through `call_soon_threadsafe`).
    """
    subs = _subscribers.get(user_id)
    if not subs:
        return
    if _loop is None:
        logger.warning("profile_events: publish before register_event_loop(); dropping")
        return
    event = {"event": "profile_changed", "data": {"user_id": user_id}}
    for queue in list(subs):
        _loop.call_soon_threadsafe(_enqueue_or_drop, queue, event)


def publish_profile_changed_many(user_ids) -> None:
    for uid in user_ids:
        publish_profile_changed(uid)


def _enqueue_or_drop(queue: asyncio.Queue, event: dict) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        logger.warning("profile_events: subscriber queue full, dropping event")
