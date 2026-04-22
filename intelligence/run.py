"""Top-level agent invocation — the single entry point for running an agent.

    async for ev in run_agent(
        name="scout",
        user_message="What's sub-cost-code 10.01?",
    ):
        ...

What happens under the hood:
  1. Registry lookup — resolves `name` to an Agent.
  2. Login — calls /api/v1/mobile/auth/login with the agent's credentials,
     gets the bearer JWT and the agent's user_id.
  3. Tool resolution — gathers Tool instances for each name on the Agent.
  4. ToolContext — built with auth_token, agent_id, session_id placeholder,
     and the requesting user's id.
  5. Session — run_session() creates the AgentSession row, drives the loop,
     persists every turn and tool call, yields LoopEvents.
"""
from typing import AsyncIterator, Optional

from intelligence.auth import login_agent_with_user_id
from intelligence.loop.events import LoopEvent
from intelligence.loop.session_runner import run_session
from intelligence.registry import agents as agent_registry
from intelligence.tools import registry as tool_registry
from intelligence.tools.base import ToolContext
from intelligence.transport.registry import get_transport


async def run_agent(
    *,
    name: str,
    user_message: str,
    requesting_user_id: Optional[int] = None,
    parent_session_id: Optional[int] = None,
    previous_session_id: Optional[int] = None,
    on_session_created: Optional[callable] = None,
) -> AsyncIterator[LoopEvent]:
    """Run an agent by name. Yields LoopEvents as they happen.

    Raises ValueError if the agent is not registered. Raises AgentAuthError
    if login fails (propagates — no session row is created in that case).
    """
    # 1. Resolve the agent
    agent = agent_registry.get(name)
    if agent is None:
        raise ValueError(f"Unknown agent: {name!r}")

    # 2. Log in as the agent user
    access_token, agent_user_id = await login_agent_with_user_id(agent.credentials_key)

    # 3. Resolve the tools declared on the agent
    tools = tool_registry.resolve(list(agent.tools))

    # 4. Build the tool context (agent_id is the agent user's public_id if we
    #    later surface that; for now we carry auth_token + requesting_user).
    ctx = ToolContext(
        agent_id=None,  # populated when we surface the agent user's public_id
        auth_token=access_token,
        session_id=None,  # populated by session_runner once the row exists
        requesting_user_id=str(requesting_user_id) if requesting_user_id else None,
    )

    # 5. Drive the persistent session
    transport = get_transport(agent.provider)
    async for ev in run_session(
        transport=transport,
        provider=agent.provider,
        agent_name=agent.name,
        model=agent.model,
        user_message=user_message,
        tools=tools,
        ctx=ctx,
        system=agent.system_prompt,
        budget=agent.budget,
        agent_user_id=agent_user_id,
        requesting_user_id=requesting_user_id,
        parent_session_id=parent_session_id,
        previous_session_id=previous_session_id,
        on_session_created=on_session_created,
    ):
        yield ev
