"""
VendorAgent Graph Nodes

Defines the nodes (steps) that make up the agent graph.
"""
from __future__ import annotations

import logging
from typing import Literal

from langchain_core.messages import SystemMessage, ToolMessage, AIMessage

from core.ai.agents.vendor_agent.graph.state import VendorAgentState
from core.ai.agents.vendor_agent.graph.tools import VENDOR_AGENT_TOOLS, ToolContext
from core.ai.agents.vendor_agent.config import (
    VENDOR_AGENT_SYSTEM_PROMPT,
    MAX_LLM_CALLS_PER_RUN,
)
from core.ai.llm.claude import get_claude_model

logger = logging.getLogger(__name__)


# =============================================================================
# Model Setup
# =============================================================================

def get_model_with_tools():
    """Get the Claude chat model with tools bound."""
    model = get_claude_model()
    return model.bind_tools(VENDOR_AGENT_TOOLS)


# Tool lookup for execution
TOOLS_BY_NAME = {tool.name: tool for tool in VENDOR_AGENT_TOOLS}


# =============================================================================
# Graph Nodes
# =============================================================================

def setup_context(state: VendorAgentState) -> dict:
    """
    Setup node - initializes tool context from state.

    This node runs first to ensure tools have access to tenant/run context.
    """
    ToolContext.set(
        tenant_id=state["tenant_id"],
        agent_run_id=state.get("agent_run_id"),
        user_id=state.get("user_id"),
    )

    # Add system message if this is the start
    if not state["messages"]:
        return {
            "messages": [SystemMessage(content=VENDOR_AGENT_SYSTEM_PROMPT)],
        }

    return {}


def llm_call(state: VendorAgentState) -> dict:
    """
    LLM node - invokes the model to decide next action.

    The model will either:
    - Call a tool (returns AIMessage with tool_calls)
    - Provide a final response (returns AIMessage without tool_calls)
    """
    model = get_model_with_tools()

    # Ensure system prompt is first
    messages = state["messages"]
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=VENDOR_AGENT_SYSTEM_PROMPT)] + messages

    try:
        print("DEBUG llm_call: invoking model (Azure)")
        result_msg = model.invoke(messages)
        content_preview = (getattr(result_msg, "content") or "")[:200]
        print("DEBUG llm_call: result_msg.content (first 200 chars) =", content_preview)
        return {
            "messages": [result_msg],
            "llm_calls": state.get("llm_calls", 0) + 1,
        }
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        print("DEBUG llm_call: exception =", e)
        error_msg = AIMessage(content=f"Error calling LLM: {str(e)}")
        return {
            "messages": [error_msg],
            "llm_calls": state.get("llm_calls", 0) + 1,
            "errors": state.get("errors", []) + [str(e)],
        }


def tool_node(state: VendorAgentState) -> dict:
    """
    Tool node - executes tool calls from the last AI message.

    Handles multiple tool calls in a single message.
    """
    result = []
    last = state["messages"][-1]

    if not getattr(last, "tool_calls", None):
        return {"messages": []}

    for tool_call in last.tool_calls:
        tool_name = tool_call["name"]
        tool_args = tool_call["args"]

        logger.info(f"Executing tool: {tool_name} with args: {tool_args}")
        print(f"[VendorAgent] Tool call: {tool_name}({tool_args})")

        try:
            tool = TOOLS_BY_NAME.get(tool_name)
            if not tool:
                observation = f"Error: Unknown tool '{tool_name}'"
            else:
                observation = tool.invoke(tool_args)

            # Log result summary
            if isinstance(observation, dict):
                summary_keys = {k: v for k, v in observation.items() if k in ('bill_count', 'expense_count', 'document_count', 'success', 'error', 'confidence', 'vendor_name')}
                if summary_keys:
                    print(f"[VendorAgent] Tool result: {tool_name} -> {summary_keys}")
                else:
                    print(f"[VendorAgent] Tool result: {tool_name} -> OK")
            else:
                print(f"[VendorAgent] Tool result: {tool_name} -> {str(observation)[:100]}")

            # Track proposals created
            if tool_name == "create_vendor_type_proposal":
                if isinstance(observation, dict) and observation.get("success"):
                    # Will be handled by update_metrics node
                    pass

        except Exception as e:
            logger.error(f"Tool {tool_name} failed: {e}")
            print(f"[VendorAgent] Tool FAILED: {tool_name} -> {e}")
            observation = f"Error executing tool: {str(e)}"

        result.append(
            ToolMessage(
                content=str(observation),
                tool_call_id=tool_call["id"],
            )
        )

    return {"messages": result}


def update_metrics(state: VendorAgentState) -> dict:
    """
    Metrics node - updates processing counters based on tool results.

    Called after tool execution to track proposals created and vendors processed.
    """
    updates = {}

    # Look at recent tool messages for results
    for msg in reversed(state["messages"]):
        if isinstance(msg, ToolMessage):
            try:
                # Parse the observation
                import ast
                content = msg.content
                if content.startswith("{"):
                    result = ast.literal_eval(content)
                    if isinstance(result, dict):
                        if result.get("success") and "proposal" in result:
                            updates["proposals_created"] = state.get("proposals_created", 0) + 1
                        if result.get("success") and result.get("logged"):
                            # skip_vendor was called
                            updates["skipped_count"] = state.get("skipped_count", 0) + 1
            except Exception:
                pass
        elif isinstance(msg, AIMessage):
            # Stop looking once we hit the AI message that triggered these tools
            break

    return updates


# =============================================================================
# Routing Functions
# =============================================================================

def should_continue(state: VendorAgentState) -> Literal["tool_node", "check_complete"]:
    """
    Router - decides whether to execute tools or check if we're done.

    Returns:
        "tool_node" if the last message has tool calls
        "check_complete" otherwise
    """
    # Check LLM call limit
    if state.get("llm_calls", 0) >= MAX_LLM_CALLS_PER_RUN:
        logger.warning(f"Reached max LLM calls ({MAX_LLM_CALLS_PER_RUN})")
        return "check_complete"

    last_msg = state["messages"][-1] if state["messages"] else None

    if last_msg and getattr(last_msg, "tool_calls", None):
        return "tool_node"

    return "check_complete"


def check_complete(state: VendorAgentState) -> Literal["llm_call", "__end__"]:
    """
    Completion check - determines if the agent should continue or finish.

    In single mode: we reach this only when the LLM returned a response with no
    tool calls (user gets one answer per turn). End immediately.
    In batch mode: continue if more work, else end.

    Returns:
        "llm_call" to continue processing
        "__end__" to finish
    """
    mode = state.get("mode", "batch")

    # Check LLM call limit
    if state.get("llm_calls", 0) >= MAX_LLM_CALLS_PER_RUN:
        logger.warning("Ending due to LLM call limit")
        return "__end__"

    # In single mode we only reach here when the model produced a final response (no tool calls).
    # End the run so we don't loop back and retry.
    if mode == "single":
        return "__end__"

    # In batch mode, the LLM decides when it's done
    # Check the last message - if it doesn't have tool calls and seems final
    last_msg = state["messages"][-1] if state["messages"] else None
    if last_msg and isinstance(last_msg, AIMessage):
        content = last_msg.content.lower() if last_msg.content else ""
        # Simple heuristic: if the message mentions completion/done
        if any(word in content for word in ["complete", "finished", "done", "processed all"]):
            return "__end__"

    # Continue by default in batch mode
    return "llm_call"
