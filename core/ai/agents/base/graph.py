"""
Base Agent Graph Builder

Factory function that assembles the standard LangGraph agent topology.
"""
from __future__ import annotations

from typing import Callable, Optional

from langgraph.graph import StateGraph, START, END


def build_standard_agent_graph(
    state_class: type,
    setup_fn: Callable,
    llm_call_fn: Callable,
    tool_node_fn: Callable,
    should_continue_fn: Callable,
    check_complete_fn: Callable,
    update_metrics_fn: Optional[Callable] = None,
):
    """
    Build and compile the standard agent graph topology.

    Graph structure:
        START
          |
          v
        setup_context
          |
          v
        llm_call <------------------+
          |                         |
          v                         |
        should_continue             |
          |                         |
          +---> tool_node --> [update_metrics] --+
          |
          v
        check_complete
          |
          +---> (continue) ---> llm_call
          |
          +---> (done) ---> END

    Args:
        state_class: The TypedDict state class for this agent
        setup_fn: Node that initializes context
        llm_call_fn: Node that invokes the LLM
        tool_node_fn: Node that executes tool calls
        should_continue_fn: Router after llm_call
        check_complete_fn: Router for end vs continue
        update_metrics_fn: Optional node between tool_node and llm_call
    """
    builder = StateGraph(state_class)

    builder.add_node("setup_context", setup_fn)
    builder.add_node("llm_call", llm_call_fn)
    builder.add_node("tool_node", tool_node_fn)

    if update_metrics_fn:
        builder.add_node("update_metrics", update_metrics_fn)

    # Pass-through node for routing
    builder.add_node("check_complete", lambda state: {})

    # Edges
    builder.add_edge(START, "setup_context")
    builder.add_edge("setup_context", "llm_call")

    builder.add_conditional_edges(
        "llm_call",
        should_continue_fn,
        {"tool_node": "tool_node", "check_complete": "check_complete"},
    )

    if update_metrics_fn:
        builder.add_edge("tool_node", "update_metrics")
        builder.add_edge("update_metrics", "llm_call")
    else:
        builder.add_edge("tool_node", "llm_call")

    builder.add_conditional_edges(
        "check_complete",
        check_complete_fn,
        {"llm_call": "llm_call", "__end__": END},
    )

    return builder.compile()
