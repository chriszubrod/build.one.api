"""
Invoice Composition Agent

LangGraph agent that intelligently groups billable items for customer invoices.
"""
from __future__ import annotations

import ast
import logging
from typing import Optional

from langchain_core.messages import HumanMessage, ToolMessage

from core.ai.agents.base import (
    AgentToolContext,
    make_setup_context,
    make_llm_call_node,
    make_tool_node,
    make_should_continue,
    make_check_complete,
    build_standard_agent_graph,
)
from core.ai.agents.invoice_agent.config import (
    INVOICE_AGENT_SYSTEM_PROMPT,
    MAX_LLM_CALLS,
)
from core.ai.agents.invoice_agent.graph.state import (
    InvoiceCompositionState,
    initial_state,
)
from core.ai.agents.invoice_agent.graph.tools import (
    INVOICE_TOOLS,
    TOOLS_BY_NAME,
)
from core.ai.llm.claude import get_claude_model

logger = logging.getLogger(__name__)


def _get_model_with_tools():
    model = get_claude_model()
    return model.bind_tools(INVOICE_TOOLS)


invoice_agent = build_standard_agent_graph(
    state_class=InvoiceCompositionState,
    setup_fn=make_setup_context(AgentToolContext, INVOICE_AGENT_SYSTEM_PROMPT),
    llm_call_fn=make_llm_call_node(_get_model_with_tools, INVOICE_AGENT_SYSTEM_PROMPT),
    tool_node_fn=make_tool_node(TOOLS_BY_NAME),
    should_continue_fn=make_should_continue(max_calls=MAX_LLM_CALLS),
    check_complete_fn=make_check_complete(),
)


def compose_invoice(
    tenant_id: int,
    project_public_id: str,
) -> dict:
    """
    Propose an invoice composition for a project's billable items.

    Returns:
        {
            "groups": [...],
            "total_amount": float,
            "summary": str,
        }
    """
    try:
        state = initial_state(tenant_id=tenant_id, project_public_id=project_public_id)
        state["messages"] = [
            HumanMessage(
                content=f"Please compose an invoice for project {project_public_id}. "
                "Load the billable items and project details, check prior invoices "
                "for formatting patterns, then propose a logical grouping."
            )
        ]

        result = invoice_agent.invoke(state)

        # Extract propose_grouping result
        for msg in reversed(result.get("messages", [])):
            if isinstance(msg, ToolMessage):
                try:
                    content = msg.content
                    if content.startswith("{"):
                        data = ast.literal_eval(content)
                        if isinstance(data, dict) and data.get("success") and "groups" in data:
                            return {
                                "groups": data["groups"],
                                "total_amount": data.get("total_amount", 0),
                                "summary": data.get("summary", ""),
                            }
                except Exception:
                    continue

        return {"groups": [], "total_amount": 0, "summary": "Agent did not produce a grouping."}

    except Exception as e:
        logger.warning("Invoice composition agent failed: %s", e)
        return {"groups": [], "total_amount": 0, "summary": f"Error: {e}"}
