"""
VendorAgent Graph Definition

Compiles the LangGraph agent for vendor type classification.
"""
from __future__ import annotations

import logging
from typing import Optional

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END

from core.ai.agents.vendor_agent.graph.state import VendorAgentState, initial_state
from core.ai.agents.vendor_agent.graph.nodes import (
    setup_context,
    llm_call,
    tool_node,
    update_metrics,
    should_continue,
    check_complete,
)
from core.ai.agents.vendor_agent.business.service import VendorAgentService
from core.ai.agents.vendor_agent.config import MAX_LLM_CALLS_PER_RUN

logger = logging.getLogger(__name__)


# =============================================================================
# Graph Builder
# =============================================================================

def build_vendor_agent():
    """
    Build and compile the VendorAgent graph.

    Graph structure:
        START
          │
          ▼
        setup_context
          │
          ▼
        llm_call ◄─────────────────┐
          │                        │
          ▼                        │
        should_continue            │
          │                        │
          ├─── tool_node ──► update_metrics
          │         │              │
          │         └──────────────┘
          │
          ▼
        check_complete
          │
          ├─── (continue) ──► llm_call
          │
          └─── (done) ──► END
    """
    builder = StateGraph(VendorAgentState)

    # Add nodes
    builder.add_node("setup_context", setup_context)
    builder.add_node("llm_call", llm_call)
    builder.add_node("tool_node", tool_node)
    builder.add_node("update_metrics", update_metrics)
    builder.add_node("check_complete", lambda state: {})  # Pass-through for routing

    # Add edges
    builder.add_edge(START, "setup_context")
    builder.add_edge("setup_context", "llm_call")

    # After LLM call, decide: execute tools or check if done
    builder.add_conditional_edges(
        "llm_call",
        should_continue,
        {
            "tool_node": "tool_node",
            "check_complete": "check_complete",
        }
    )

    # After tool execution, update metrics and go back to LLM
    builder.add_edge("tool_node", "update_metrics")
    builder.add_edge("update_metrics", "llm_call")

    # After check_complete, either continue or end
    builder.add_conditional_edges(
        "check_complete",
        check_complete,
        {
            "llm_call": "llm_call",
            "__end__": END,
        }
    )

    return builder.compile()


# Compiled agent instance
vendor_agent = build_vendor_agent()


# =============================================================================
# Agent Runner Functions
# =============================================================================

def run_batch_classification(
    tenant_id: int,
    user_id: str = None,
    created_by: str = None,
) -> dict:
    """
    Run the VendorAgent in batch mode to classify vendors without types.

    Creates an agent run record, executes the agent, and returns results.

    Args:
        tenant_id: Tenant context
        user_id: User who triggered the run
        created_by: Attribution for audit

    Returns:
        Dict with run results including proposals_created, vendors_processed, etc.
    """
    service = VendorAgentService()

    # Create agent run record
    run = service.start_run(
        tenant_id=tenant_id,
        trigger_type="manual",
        trigger_source="batch_classification",
        created_by=created_by or user_id,
    )

    try:
        # Initialize state
        state = initial_state(
            tenant_id=tenant_id,
            agent_run_id=run.id,
            user_id=user_id,
            mode="batch",
        )

        # Add initial instruction
        state["messages"] = [
            HumanMessage(content=(
                "Please classify vendors that are missing a VendorType. "
                "Start by getting the list of vendors missing a type, then process each one. "
                "For each vendor, gather context and either create a proposal or skip if uncertain."
            ))
        ]

        # Run the agent
        result = vendor_agent.invoke(state)

        # Complete the run
        service.complete_run(
            run.public_id,
            vendors_processed=result.get("vendors_processed", 0),
            proposals_created=result.get("proposals_created", 0),
            error_count=len(result.get("errors", [])),
            summary={
                "skipped": result.get("skipped_count", 0),
                "llm_calls": result.get("llm_calls", 0),
                "errors": result.get("errors", []),
            },
        )

        return {
            "success": True,
            "run_public_id": run.public_id,
            "vendors_processed": result.get("vendors_processed", 0),
            "proposals_created": result.get("proposals_created", 0),
            "skipped": result.get("skipped_count", 0),
            "llm_calls": result.get("llm_calls", 0),
        }

    except Exception as e:
        logger.error(f"Batch classification failed: {e}")
        service.fail_run(
            run.public_id,
            summary={"error": str(e)},
        )
        return {
            "success": False,
            "run_public_id": run.public_id,
            "error": str(e),
        }


def run_single_vendor_classification(
    tenant_id: int,
    vendor_id: int,
    user_id: str = None,
    user_message: str = None,
) -> dict:
    """
    Run the VendorAgent for a single vendor (interactive mode).

    Used for the sidebar chat where a user is viewing a specific vendor.
    Caller must resolve vendor_public_id to vendor_id before calling; only vendor_id is used here.

    Args:
        tenant_id: Tenant context
        vendor_id: The vendor to classify (database ID; resolved from public_id at API boundary)
        user_id: User interacting with the agent
        user_message: Optional message from the user

    Returns:
        Dict with agent response and any proposals created
    """
    service = VendorAgentService()

    # Create or continue agent run
    run = service.start_run(
        tenant_id=tenant_id,
        trigger_type="manual",
        trigger_source="single_vendor",
        context={"vendor_id": vendor_id},
        created_by=user_id,
    )

    try:
        # Load existing conversation for this vendor
        conversation = service.get_recent_conversation(vendor_id=vendor_id, limit=20)

        # Initialize state
        state = initial_state(
            tenant_id=tenant_id,
            agent_run_id=run.id,
            user_id=user_id,
            mode="single",
            vendor_id=vendor_id,
        )

        # Build message history from conversation
        from langchain_core.messages import SystemMessage, AIMessage

        messages = []
        for msg in conversation:
            if msg.role == "user":
                messages.append(HumanMessage(content=msg.content))
            elif msg.role == "agent":
                messages.append(AIMessage(content=msg.content))

        # Add user's new message. Use only vendor_id (resolved from public_id at API boundary).
        instruction = user_message or (
            f"Please analyze this vendor and determine the appropriate VendorType. "
            "Check rejection history first, then gather context from bills, documents, etc."
        )
        instruction = f"Vendor ID (use this for all tools): {vendor_id}.\n\nUser: {instruction}"
        messages.append(HumanMessage(content=instruction))
        state["messages"] = messages

        # Log user message to conversation
        if user_message:
            service.add_user_message(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
                content=user_message,
                message_type="chat",
            )

        # Run the agent
        print("DEBUG run_single_vendor_classification: invoking graph")
        result = vendor_agent.invoke(state)

        # Get the final agent response
        final_response = None
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, AIMessage) and msg.content:
                final_response = msg.content
                break
        print("DEBUG run_single_vendor_classification: final_response (first 200 chars) =", (final_response or "")[:200])

        # Log agent response to conversation
        if final_response:
            service.add_agent_message(
                tenant_id=tenant_id,
                vendor_id=vendor_id,
                content=final_response,
                message_type="response",
                agent_run_id=run.id,
            )

        # Complete the run
        service.complete_run(
            run.public_id,
            vendors_processed=1,
            proposals_created=result.get("proposals_created", 0),
        )

        return {
            "success": True,
            "run_public_id": run.public_id,
            "response": final_response,
            "proposals_created": result.get("proposals_created", 0),
            "skipped": result.get("skipped_count", 0),
        }

    except Exception as e:
        logger.error(f"Single vendor classification failed: {e}")
        print("DEBUG run_single_vendor_classification: exception =", e)
        service.fail_run(run.public_id, summary={"error": str(e)})
        return {
            "success": False,
            "run_public_id": run.public_id,
            "error": str(e),
        }


# =============================================================================
# Debug / Visualization
# =============================================================================

if __name__ == "__main__":
    # Visualize the graph
    try:
        print(vendor_agent.get_graph().draw_ascii())
    except Exception:
        print(
            """
    VendorAgent Graph:

    [START]
        │
        ▼
    [setup_context]
        │
        ▼
    [llm_call] ◄─────────────────┐
        │                        │
        ▼                        │
    {should_continue}            │
        │                        │
        ├──► [tool_node] ──► [update_metrics]
        │                        │
        │                        └───────────┘
        ▼
    [check_complete]
        │
        ├──► (continue) ──► [llm_call]
        │
        └──► (done) ──► [END]
    """
        )
