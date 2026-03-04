"""
Contract Labor Matching Agent

LangGraph agent that matches imported timesheet entries to vendors, projects, and rates.
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
from core.ai.agents.contract_labor_agent.config import (
    CONTRACT_LABOR_AGENT_SYSTEM_PROMPT,
    MAX_LLM_CALLS,
)
from core.ai.agents.contract_labor_agent.graph.state import (
    ContractLaborMatchState,
    initial_state,
)
from core.ai.agents.contract_labor_agent.graph.tools import (
    CONTRACT_LABOR_TOOLS,
    TOOLS_BY_NAME,
)
from core.ai.llm.claude import get_claude_model

logger = logging.getLogger(__name__)


def _get_model_with_tools():
    model = get_claude_model()
    return model.bind_tools(CONTRACT_LABOR_TOOLS)


def _batch_end_condition(state: dict) -> bool:
    """End when all entries have been processed."""
    entries = state.get("entries_to_match", [])
    matched = state.get("entries_matched", 0)
    skipped = state.get("entries_skipped", 0)
    return (matched + skipped) >= len(entries)


contract_labor_agent = build_standard_agent_graph(
    state_class=ContractLaborMatchState,
    setup_fn=make_setup_context(AgentToolContext, CONTRACT_LABOR_AGENT_SYSTEM_PROMPT),
    llm_call_fn=make_llm_call_node(_get_model_with_tools, CONTRACT_LABOR_AGENT_SYSTEM_PROMPT),
    tool_node_fn=make_tool_node(TOOLS_BY_NAME),
    should_continue_fn=make_should_continue(max_calls=MAX_LLM_CALLS),
    check_complete_fn=make_check_complete(end_condition_fn=_batch_end_condition),
)


def _extract_proposals_from_state(state: dict) -> list[dict]:
    """Extract all propose_match results from the state."""
    proposals = []
    for msg in state.get("messages", []):
        if isinstance(msg, ToolMessage):
            try:
                content = msg.content
                if content.startswith("{"):
                    result = ast.literal_eval(content)
                    if isinstance(result, dict) and result.get("success") and "entry_index" in result:
                        proposals.append(result)
            except Exception:
                continue
    return proposals


def match_entries(
    tenant_id: int,
    entries: list[dict],
) -> dict:
    """
    Match a batch of contract labor entries to vendors and projects.

    Args:
        tenant_id: Tenant context
        entries: List of dicts with {index, name, job, date, hours}

    Returns:
        {
            "proposals": [...],
            "entries_matched": int,
            "entries_skipped": int,
        }
    """
    try:
        state = initial_state(
            tenant_id=tenant_id,
            entries=entries,
        )

        # Build instruction with all entries
        entry_lines = []
        for i, e in enumerate(entries):
            entry_lines.append(f"  {i}: Name={e.get('name')}, Job={e.get('job')}, Date={e.get('date')}")

        instruction = (
            f"Please match these {len(entries)} contract labor entries to vendors and projects.\n"
            f"For each entry, search for the vendor and project, check for carry-forward rates, "
            f"then submit a match proposal.\n\n"
            f"Entries:\n" + "\n".join(entry_lines)
        )
        state["messages"] = [HumanMessage(content=instruction)]

        result = contract_labor_agent.invoke(state)
        proposals = _extract_proposals_from_state(result)

        return {
            "proposals": proposals,
            "entries_matched": len(proposals),
            "entries_skipped": len(entries) - len(proposals),
        }

    except Exception as e:
        logger.warning("Contract labor matching agent failed: %s", e)
        return {"proposals": [], "entries_matched": 0, "entries_skipped": len(entries), "error": str(e)}
