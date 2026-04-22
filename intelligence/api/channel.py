"""In-memory event bus for active agent sessions.

One `SessionChannel` per running session, keyed by the session's PublicId.
The background task that drives the run publishes every LoopEvent to the
channel; SSE handlers subscribe to receive them.

Lifecycle:
  - Created when a run starts (register() returns a fresh channel).
  - Lives in memory while the run is active and for a grace window after
    completion (so late SSE subscribers still get the stream).
  - After grace expires, the channel is unregistered — further subscribers
    fall back to DB replay (implemented in the router).

Thread/async notes:
  - Everything is asyncio-native. `publish()` is synchronous (pushes to
    bounded queues without awaiting), `subscribe()` returns an async
    iterator.
  - Subscribers joining mid-run replay the buffered history first, then
    tail live events.
"""
import asyncio
import logging
from typing import AsyncIterator, Optional

from intelligence.loop.events import LoopEvent


logger = logging.getLogger(__name__)


# Grace window — how long a completed channel stays available for late
# subscribers before it's garbage-collected.
COMPLETION_GRACE_SECONDS = 60

# Bounded subscriber queue. If a subscriber's queue fills (slow consumer),
# publishes drop for that subscriber rather than blocking the whole run.
# 4096 is generous for normal agent volume.
SUBSCRIBER_QUEUE_SIZE = 4096


class SessionChannel:
    """Pub/sub for one agent session."""

    def __init__(self, session_public_id: str):
        self.session_public_id = session_public_id
        self._history: list[LoopEvent] = []
        self._subscribers: list[asyncio.Queue[Optional[LoopEvent]]] = []
        self._done = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def is_done(self) -> bool:
        return self._done.is_set()

    def publish(self, event: LoopEvent) -> None:
        """Record an event to history and push to all subscribers."""
        self._history.append(event)
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "subscriber queue full for session %s; dropping event %s",
                    self.session_public_id,
                    event.type,
                )
        if event.type in ("done", "error"):
            self._done.set()
            # Wake every subscriber so its generator sees the end-of-stream.
            for q in self._subscribers:
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass

    async def subscribe(self) -> AsyncIterator[LoopEvent]:
        """Yield all events — historical first, then live until the run ends."""
        q: asyncio.Queue[Optional[LoopEvent]] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_SIZE
        )
        # Replay history before attaching so we don't miss or duplicate.
        async with self._lock:
            for ev in self._history:
                q.put_nowait(ev)
            if self._done.is_set():
                q.put_nowait(None)
            self._subscribers.append(q)

        try:
            while True:
                ev = await q.get()
                if ev is None:
                    return
                yield ev
        finally:
            # Remove self from the subscriber list. Safe even if already removed.
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass


# ─── Module-level registry ───────────────────────────────────────────────

_channels: dict[str, SessionChannel] = {}
_registry_lock = asyncio.Lock()


async def register(session_public_id: str) -> SessionChannel:
    """Create + register a channel for a new session."""
    async with _registry_lock:
        if session_public_id in _channels:
            raise ValueError(
                f"Channel already registered for session {session_public_id}"
            )
        ch = SessionChannel(session_public_id)
        _channels[session_public_id] = ch
        return ch


async def get(session_public_id: str) -> Optional[SessionChannel]:
    async with _registry_lock:
        return _channels.get(session_public_id)


async def schedule_cleanup(
    session_public_id: str, grace_seconds: int = COMPLETION_GRACE_SECONDS
) -> None:
    """Remove the channel after a grace window. Called when the run finishes."""
    await asyncio.sleep(grace_seconds)
    async with _registry_lock:
        _channels.pop(session_public_id, None)
    logger.debug("channel gc'd for session %s", session_public_id)
