"""Agent-to-agent delegation as a tool.

`make_delegation_tool` returns a Tool that any agent can register. When
the parent invokes it, the handler:

  1. Spawns a sub-session for `target_agent` via background.start_run,
     setting ParentSessionId to the parent's session id and inheriting
     the parent's RequestingUserId.
  2. Subscribes to the sub-session's SessionChannel.
  3. Forwards each event (except terminal `done` / `error`) into the
     parent's channel so the SSE consumer sees a live picture of the
     sub-agent's reasoning, tool calls, and approval flow.
  4. Accumulates the sub-agent's final assistant text as the tool result.

Approval flow: sub-agent approval requests carry their own
session_public_id, so the UI POSTs decisions to the sub-session's
/approve URL — handled transparently by the existing approval
coordinator (which is per-session_id by construction).
"""
import logging
from typing import Optional

from pydantic import BaseModel, Field

from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.tools.schema import input_schema_from


logger = logging.getLogger(__name__)


# Events we forward onto the parent's stream. Terminal events are
# excluded because the UI treats them as "the run finished" — the
# parent's run is still going while a sub-session is mid-execution.
_FORWARDED_EVENT_TYPES = {
    "turn_start",
    "text_delta",
    "tool_call_start",
    "tool_call_end",
    "turn_end",
    "approval_request",
    "approval_decision",
}


class _DelegationArgs(BaseModel):
    task: str = Field(
        description=(
            "The task description to hand off to the specialist agent. "
            "Pass the user's request verbatim, or a clarified version "
            "that captures all the necessary context (the specialist "
            "starts with no memory of this conversation)."
        ),
    )


def make_delegation_tool(
    *,
    name: str,
    target_agent: str,
    description: str,
) -> Tool:
    """Build a Tool that delegates to another registered agent.

    `target_agent` must be the name of a registered agent. The check
    happens at handler invocation time (not registration time) so this
    factory can be called before the target agent's module is imported.
    """

    async def _handler(args: dict, ctx: ToolContext) -> ToolResult:
        # Late imports — these reach into intelligence.api which may
        # itself import composition modules at startup. Lazy avoids a
        # circular at module-load.
        from intelligence.api import background, channel as session_channel

        parsed = _DelegationArgs(**args)

        if ctx.session_public_id is None or ctx.session_id is None:
            return ToolResult(
                content=(
                    "Delegation is unavailable in this context "
                    "(no parent session id)."
                ),
                is_error=True,
            )

        parent_channel = await session_channel.get(ctx.session_public_id)
        requesting_id: Optional[int] = None
        if ctx.requesting_user_id is not None:
            try:
                requesting_id = int(ctx.requesting_user_id)
            except (TypeError, ValueError):
                requesting_id = None

        try:
            sub_public_id = await background.start_run(
                agent_name=target_agent,
                user_message=parsed.task,
                requesting_user_id=requesting_id,
                parent_session_id=ctx.session_id,
            )
        except Exception as exc:
            logger.exception(
                "delegation: failed to start sub-agent %r", target_agent
            )
            return ToolResult(
                content=f"Failed to start {target_agent}: {exc}",
                is_error=True,
            )

        sub_channel = await session_channel.get(sub_public_id)
        if sub_channel is None:
            return ToolResult(
                content=(
                    f"Sub-agent {target_agent} started but its event "
                    f"channel is unavailable (session {sub_public_id})."
                ),
                is_error=True,
            )

        final_text_chunks: list[str] = []
        is_error = False
        async for ev in sub_channel.subscribe():
            if ev.type == "text_delta":
                final_text_chunks.append(ev.text)
            if (
                ev.type in _FORWARDED_EVENT_TYPES
                and parent_channel is not None
            ):
                try:
                    # Stamp the source so the UI can group concurrent
                    # sub-agent events into per-specialist lanes. Use
                    # model_copy so we don't mutate the channel-buffered
                    # original (other late subscribers see it untouched).
                    stamped = ev.model_copy(update={
                        "session_public_id": sub_public_id,
                        "agent_name": target_agent,
                    })
                    parent_channel.publish(stamped)
                except Exception:
                    logger.exception(
                        "delegation: failed to forward %s event to parent",
                        ev.type,
                    )
            if ev.type == "error":
                is_error = True
                final_text_chunks.append(f"[sub-agent error: {ev.message}]")
                break
            if ev.type == "done":
                break

        result_text = "".join(final_text_chunks).strip()
        if not result_text:
            result_text = (
                f"({target_agent} returned no text — see "
                f"session {sub_public_id} for details)"
            )
        return ToolResult(content=result_text, is_error=is_error)

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema_from(_DelegationArgs),
        handler=_handler,
    )
