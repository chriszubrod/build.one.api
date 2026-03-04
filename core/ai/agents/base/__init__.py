"""
Base Agent Infrastructure

Shared components for all LangGraph agents:
- BaseAgentState: Common state fields
- AgentToolContext: Tool execution context
- Node factories: make_llm_call_node, make_tool_node, etc.
- Graph builder: build_standard_agent_graph
"""
from core.ai.agents.base.state import BaseAgentState, base_initial_state
from core.ai.agents.base.context import AgentToolContext
from core.ai.agents.base.nodes import (
    make_setup_context,
    make_llm_call_node,
    make_tool_node,
    make_should_continue,
    make_check_complete,
)
from core.ai.agents.base.graph import build_standard_agent_graph

__all__ = [
    "BaseAgentState",
    "base_initial_state",
    "AgentToolContext",
    "make_setup_context",
    "make_llm_call_node",
    "make_tool_node",
    "make_should_continue",
    "make_check_complete",
    "build_standard_agent_graph",
]
