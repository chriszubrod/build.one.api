"""Conversation history synthesis from persisted sessions.

When a user continues a conversation, the new AgentSession needs the prior
sessions' full message history as its loop-starting context. This module
walks the PreviousSessionId chain and reconstructs canonical Messages with
proper user/assistant/tool-result alternation.

Shape produced per session (oldest first):
  user(text: session.user_message)
  → for each turn:
      assistant([text?, tool_use*])
      user([tool_result*])    if the turn had tool calls
  → next session...
"""
import asyncio
import json
import logging
from typing import Optional

from intelligence.messages.types import (
    Message,
    Text,
    ToolResult,
    ToolUse,
)
from intelligence.persistence.session_repo import (
    AgentSession,
    AgentSessionRepo,
    AgentToolCallRepo,
    AgentTurnRepo,
)


logger = logging.getLogger(__name__)


# Safety cap so a malformed chain can't DOS by walking forever.
MAX_CHAIN_DEPTH = 200


async def load_chain_history(session_id: int) -> list[Message]:
    """Walk the PreviousSessionId chain backwards from session_id and return
    the synthesized conversation history in chronological order.

    The resulting list is ready to pass as prior_history into run().
    """
    session_repo = AgentSessionRepo()
    turn_repo = AgentTurnRepo()
    tool_call_repo = AgentToolCallRepo()

    # 1. Walk backwards collecting sessions.
    sessions: list[AgentSession] = []
    current_id: Optional[int] = session_id
    for _ in range(MAX_CHAIN_DEPTH):
        if current_id is None:
            break
        session = await asyncio.to_thread(session_repo.read_by_id, current_id)
        if session is None:
            break
        sessions.append(session)
        current_id = session.previous_session_id
    else:
        logger.warning(
            "history chain walk hit MAX_CHAIN_DEPTH=%s; truncating", MAX_CHAIN_DEPTH
        )

    sessions.reverse()  # oldest first

    # 2. Reconstruct canonical Messages for each session in order.
    messages: list[Message] = []
    for session in sessions:
        if not session.id or not session.user_message:
            continue
        messages.append(
            Message(role="user", content=[Text(text=session.user_message)])
        )

        turns = await asyncio.to_thread(turn_repo.read_by_session_id, session.id)
        for turn in turns:
            if turn.id is None:
                continue
            tool_calls = await asyncio.to_thread(
                tool_call_repo.read_by_turn_id, turn.id
            )

            # assistant content = [Text?, ToolUse*]
            assistant_blocks: list = []
            if turn.assistant_text:
                assistant_blocks.append(Text(text=turn.assistant_text))
            for tc in tool_calls:
                try:
                    tc_input = json.loads(tc.tool_input) if tc.tool_input else {}
                except json.JSONDecodeError:
                    tc_input = {}
                if tc.tool_use_id and tc.tool_name:
                    assistant_blocks.append(
                        ToolUse(
                            id=tc.tool_use_id,
                            name=tc.tool_name,
                            input=tc_input,
                        )
                    )
            if assistant_blocks:
                messages.append(Message(role="assistant", content=assistant_blocks))

            # Tool results from this turn → user message
            if tool_calls:
                result_blocks: list = []
                for tc in tool_calls:
                    if not tc.tool_use_id:
                        continue
                    result_blocks.append(
                        ToolResult(
                            tool_use_id=tc.tool_use_id,
                            content=tc.tool_output or "",
                            is_error=bool(tc.is_error),
                        )
                    )
                if result_blocks:
                    messages.append(Message(role="user", content=result_blocks))

    return messages
