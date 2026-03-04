"""
Copilot Agent

LangGraph agent that powers the conversational copilot with 18 tools.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, List, Dict, Any

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from core.ai.agents.base import (
    AgentToolContext,
    make_setup_context,
    make_llm_call_node,
    make_tool_node,
    make_should_continue,
    make_check_complete,
    build_standard_agent_graph,
)
from core.ai.agents.copilot_agent.config import (
    COPILOT_AGENT_SYSTEM_PROMPT,
    MAX_LLM_CALLS,
)
from core.ai.agents.copilot_agent.graph.state import (
    CopilotAgentState,
    initial_state,
)
from core.ai.agents.copilot_agent.graph.tools import (
    COPILOT_TOOLS,
    TOOLS_BY_NAME,
)
from core.ai.llm.claude import get_claude_model

logger = logging.getLogger(__name__)


def _get_model_with_tools():
    model = get_claude_model()
    return model.bind_tools(COPILOT_TOOLS)


copilot_agent = build_standard_agent_graph(
    state_class=CopilotAgentState,
    setup_fn=make_setup_context(AgentToolContext, COPILOT_AGENT_SYSTEM_PROMPT),
    llm_call_fn=make_llm_call_node(_get_model_with_tools, COPILOT_AGENT_SYSTEM_PROMPT),
    tool_node_fn=make_tool_node(TOOLS_BY_NAME),
    should_continue_fn=make_should_continue(max_calls=MAX_LLM_CALLS),
    check_complete_fn=make_check_complete(),
)


def run_copilot(
    tenant_id: int,
    message: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Run the copilot agent for a single user turn.

    Args:
        tenant_id: Tenant context
        message: The user's message
        history: Prior conversation messages as LangChain message objects or dicts

    Returns:
        {
            "message": str,         # The assistant's final text response
            "tool_results": [...],  # Tool calls and results for suggestions/sources
            "messages": [...],      # Full message list for conversation continuity
        }
    """
    try:
        state = initial_state(tenant_id=tenant_id)

        messages = []
        if history:
            messages.extend(history)
        messages.append(HumanMessage(content=message))
        state["messages"] = messages

        result = copilot_agent.invoke(state)

        # Extract final text and tool results
        final_text = ""
        tool_results = []

        for msg in result.get("messages", []):
            if isinstance(msg, AIMessage) and msg.content and not getattr(msg, "tool_calls", None):
                final_text = msg.content
            elif isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    tool_results.append({
                        "tool": tc["name"],
                        "input": tc["args"],
                    })
            elif isinstance(msg, ToolMessage):
                # Attach result to the most recent tool_result entry
                if tool_results:
                    tool_results[-1]["result"] = msg.content

        # Get the last AIMessage text (the final response)
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                content = msg.content
                if isinstance(content, str) and content.strip():
                    final_text = content
                    break

        return {
            "message": final_text,
            "tool_results": tool_results,
            "messages": result.get("messages", []),
        }

    except Exception as e:
        logger.error("Copilot agent failed: %s", e, exc_info=True)
        return {
            "message": "I encountered an error processing your request. Please try again.",
            "tool_results": [],
            "messages": [],
        }
