"""CascadeTransport — run every agent cheapest-first with per-turn fallback.

A meta-transport that implements the Transport protocol by wrapping a ladder of
(provider, model) rungs. On each completion it tries the cheapest rung first;
if that rung fails STRUCTURALLY (transport error, an exception, or an empty
completion with no text and no tool calls) it falls back to the next, more
capable rung. The first rung that produces a real completion wins.

Why this is safe for side-effecting agents: only the read-only LLM *completion*
is retried on fallback. The agent loop executes a turn's tool calls only AFTER
this transport returns a successful completion, so a fallback never re-runs a
tool — side effects happen exactly once. (Contrast with run_agent_cascade,
which re-runs the whole agent and is therefore read-only-only.)

Limitation: the gate is STRUCTURAL, not semantic — a cheap model that returns a
valid-but-wrong tool call is accepted (no per-turn confidence exists). Semantic
safety for mutations comes from the existing approval gates + per-agent ladder
choice (start high-stakes agents on a stronger rung).

Streaming note: each rung is buffered, then replayed on success, so events are
not token-streamed during a cascade turn. Fine for the system agents; the chat
agent's real-time streaming can be optimised later (stream rung-1 optimistically,
fall back only on a pre-content error).
"""
import logging
from typing import Any, AsyncIterator, Optional

from intelligence.transport.base import (
    TextDelta,
    ToolUseComplete,
    ToolUseStart,
    TransportError,
    TransportEvent,
)

logger = logging.getLogger(__name__)


class CascadeTransport:
    def __init__(self, ladder=None, transport_for=None):
        # Imported lazily to avoid an import cycle (cascade.core -> transport
        # registry -> ... ). Defaults to the aggressive cheapest-first ladder.
        if ladder is None:
            from intelligence.cascade.core import DEFAULT_LADDER
            ladder = DEFAULT_LADDER
        if transport_for is None:
            from intelligence.transport import registry as _reg
            transport_for = _reg.get_transport
        self._ladder = tuple(ladder)
        self._transport_for = transport_for

    async def stream(
        self,
        messages: list,
        model: str,                     # ignored — the ladder selects the model
        system: Optional[str] = None,
        max_tokens: int = 4096,
        tools: Optional[list[dict[str, Any]]] = None,
        extra_body: Optional[dict[str, Any]] = None,
    ) -> AsyncIterator[TransportEvent]:
        if not self._ladder:
            yield TransportError(message="CascadeTransport has an empty ladder", code="cascade_no_rungs")
            return

        last_error: Optional[TransportError] = None
        for i, rung in enumerate(self._ladder):
            transport = self._transport_for(rung.provider)
            buffered: list[TransportEvent] = []
            failed: Optional[TransportError] = None
            saw_content = False
            try:
                async for ev in transport.stream(
                    messages, rung.model, system=system,
                    max_tokens=max_tokens, tools=tools, extra_body=extra_body,
                ):
                    if isinstance(ev, TransportError):
                        failed = ev
                        break
                    if isinstance(ev, (TextDelta, ToolUseStart, ToolUseComplete)):
                        saw_content = True
                    buffered.append(ev)
            except Exception as exc:  # a transport blowing up shouldn't kill the cascade
                failed = TransportError(
                    message=f"{type(exc).__name__}: {exc}", code="cascade_rung_exception",
                )

            if failed is not None or not saw_content:
                last_error = failed or TransportError(
                    message=f"rung {rung.model} produced an empty completion",
                    code="empty_completion",
                )
                logger.info(
                    "cascade transport: rung %d %s/%s -> fallback (%s)",
                    i + 1, rung.provider, rung.model, last_error.message[:160],
                )
                continue

            if i > 0:
                logger.info(
                    "cascade transport: accepted rung %d %s/%s after %d fallback(s)",
                    i + 1, rung.provider, rung.model, i,
                )
            for ev in buffered:
                yield ev
            return

        # Every rung failed structurally.
        yield last_error or TransportError(
            message="all cascade rungs failed to produce a completion",
            code="cascade_exhausted",
        )
