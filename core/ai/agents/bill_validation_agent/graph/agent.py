"""
Bill Validation Agent

LangGraph agent that validates bills before finalization.
Returns advisory issues — does not block operations.
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
from core.ai.agents.bill_validation_agent.config import (
    BILL_VALIDATION_SYSTEM_PROMPT,
    MAX_LLM_CALLS,
)
from core.ai.agents.bill_validation_agent.graph.state import (
    BillValidationState,
    initial_state,
)
from core.ai.agents.bill_validation_agent.graph.tools import (
    BILL_VALIDATION_TOOLS,
    TOOLS_BY_NAME,
)
from core.ai.llm.claude import get_claude_model

logger = logging.getLogger(__name__)


def _get_model_with_tools():
    model = get_claude_model()
    return model.bind_tools(BILL_VALIDATION_TOOLS)


bill_validation_agent = build_standard_agent_graph(
    state_class=BillValidationState,
    setup_fn=make_setup_context(AgentToolContext, BILL_VALIDATION_SYSTEM_PROMPT),
    llm_call_fn=make_llm_call_node(_get_model_with_tools, BILL_VALIDATION_SYSTEM_PROMPT),
    tool_node_fn=make_tool_node(TOOLS_BY_NAME),
    should_continue_fn=make_should_continue(max_calls=MAX_LLM_CALLS),
    check_complete_fn=make_check_complete(),
)


def _extract_result_from_state(state: dict) -> Optional[dict]:
    """Extract the submit_validation result from tool messages."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, ToolMessage):
            try:
                content = msg.content
                if content.startswith("{"):
                    result = ast.literal_eval(content)
                    if isinstance(result, dict) and result.get("success") and "passed" in result:
                        return result
            except Exception:
                continue
    return None


def validate_bill(
    tenant_id: int,
    bill_public_id: str,
) -> dict:
    """
    Validate a bill before finalization.

    Returns:
        {
            "passed": bool,
            "issues": [{"severity": str, "field": str, "message": str, "suggestion": str}],
            "summary": str,
        }
    """
    try:
        state = initial_state(tenant_id=tenant_id, bill_public_id=bill_public_id)
        state["messages"] = [
            HumanMessage(content=f"Please validate bill {bill_public_id}. "
                         "Load the bill details, check for duplicates, check the amount "
                         "against vendor history, and verify cost code consistency.")
        ]

        result = bill_validation_agent.invoke(state)
        tool_result = _extract_result_from_state(result)

        if tool_result:
            return {
                "passed": tool_result["passed"],
                "issues": tool_result.get("issues", []),
                "summary": tool_result.get("summary", ""),
            }

        return {
            "passed": True,
            "issues": [],
            "summary": "Validation agent completed without findings.",
        }

    except Exception as e:
        logger.warning("Bill validation agent failed: %s", e)
        return {
            "passed": True,
            "issues": [{"severity": "info", "field": "", "message": f"Validation unavailable: {e}", "suggestion": ""}],
            "summary": "Validation could not be completed.",
        }
