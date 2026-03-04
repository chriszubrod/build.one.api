"""
Extraction Agent

LangGraph agent that extracts structured bill fields from OCR'd documents.
Uses Claude with tools for validation, entity lookup, and duplicate checking.
Falls back to ClaudeExtractionService or heuristic mapper on failure.
"""
from __future__ import annotations

import ast
import logging
from decimal import Decimal, InvalidOperation
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
from core.ai.agents.extraction_agent.config import (
    EXTRACTION_AGENT_SYSTEM_PROMPT,
    MAX_LLM_CALLS,
)
from core.ai.agents.extraction_agent.graph.state import (
    ExtractionAgentState,
    initial_state,
)
from core.ai.agents.extraction_agent.graph.tools import (
    EXTRACTION_AGENT_TOOLS,
    TOOLS_BY_NAME,
)
from core.ai.llm.claude import get_claude_model

logger = logging.getLogger(__name__)


def _get_model_with_tools():
    """Get Claude model with extraction tools bound."""
    model = get_claude_model()
    return model.bind_tools(EXTRACTION_AGENT_TOOLS)


# Build the graph
extraction_agent = build_standard_agent_graph(
    state_class=ExtractionAgentState,
    setup_fn=make_setup_context(AgentToolContext, EXTRACTION_AGENT_SYSTEM_PROMPT),
    llm_call_fn=make_llm_call_node(_get_model_with_tools, EXTRACTION_AGENT_SYSTEM_PROMPT),
    tool_node_fn=make_tool_node(TOOLS_BY_NAME),
    should_continue_fn=make_should_continue(max_calls=MAX_LLM_CALLS),
    check_complete_fn=make_check_complete(),
)


def _build_user_message(
    ocr_content: str,
    email_context: dict,
    projects: list = None,
    sub_cost_codes: list = None,
) -> str:
    """Build the user message with OCR content and context."""
    parts = []

    # Email context
    if email_context:
        parts.append("=== EMAIL CONTEXT ===")
        if email_context.get("from_email"):
            parts.append(f"From: {email_context['from_email']}")
        if email_context.get("subject"):
            parts.append(f"Subject: {email_context['subject']}")
        if email_context.get("filename"):
            parts.append(f"Attachment: {email_context['filename']}")
        parts.append("")

    # OCR content (truncated)
    if ocr_content:
        content = ocr_content[:6000]
        if len(ocr_content) > 6000:
            content += "\n... [truncated]"
        parts.append("=== DOCUMENT TEXT ===")
        parts.append(content)
        parts.append("")

    # Available projects
    if projects:
        parts.append("=== AVAILABLE PROJECTS ===")
        for p in projects:
            abbr = f" ({p.abbreviation})" if getattr(p, "abbreviation", None) else ""
            parts.append(f"- {p.name}{abbr}")
        parts.append("")

    # Available sub cost codes
    if sub_cost_codes:
        parts.append("=== AVAILABLE SUB COST CODES ===")
        for scc in sub_cost_codes:
            num = f"{scc.number} " if getattr(scc, "number", None) else ""
            parts.append(f"- {num}{scc.name}")
        parts.append("")

    parts.append(
        "Please extract all fields from this document. "
        "After extraction, call validate_extraction to check your work, "
        "then use lookup_vendor to verify the vendor exists. "
        "Finally, call finalize_extraction with your results."
    )
    return "\n".join(parts)


def _extract_result_from_state(state: dict) -> Optional[dict]:
    """Extract the finalize_extraction result from tool messages."""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, ToolMessage):
            try:
                content = msg.content
                if content.startswith("{"):
                    result = ast.literal_eval(content)
                    if isinstance(result, dict) and result.get("success") and "extraction" in result:
                        return result
            except Exception:
                continue
    return None


def _result_to_bill_extraction(result: dict):
    """Convert agent result dict to BillExtractionResult."""
    from entities.bill.business.extraction_mapper import BillExtractionResult, LineItemExtraction

    extraction = result.get("extraction", {})
    bill_result = BillExtractionResult()
    bill_result.note("Extraction Agent (LangGraph + Claude)")

    # Map fields
    bill_result.vendor_name = extraction.get("vendor_name")
    bill_result.bill_number = extraction.get("bill_number")
    bill_result.bill_date = extraction.get("bill_date")
    bill_result.due_date = extraction.get("due_date")
    bill_result.payment_terms_raw = extraction.get("payment_terms")
    bill_result.memo = extraction.get("memo")
    bill_result.ship_to_address = extraction.get("ship_to_address")
    bill_result.project_hint = extraction.get("project_name")
    bill_result.sub_cost_code_hint = extraction.get("sub_cost_code_name")
    bill_result.is_billable = extraction.get("is_billable")

    # Total amount
    total = extraction.get("total_amount")
    if total is not None:
        try:
            bill_result.total_amount = Decimal(str(total))
            bill_result.amount_confidence = 0.95
        except (InvalidOperation, ValueError):
            pass

    # Vendor confidence
    if bill_result.vendor_name:
        bill_result.vendor_confidence = 0.95
        bill_result.vendor_candidates = [(bill_result.vendor_name, 0.95)]

    if bill_result.bill_number:
        bill_result.bill_number_confidence = 0.95
    if bill_result.bill_date:
        bill_result.date_confidence = 0.95

    # Line items
    for li in extraction.get("line_items", []):
        if not isinstance(li, dict):
            continue
        desc = li.get("description", "").strip()
        if not desc:
            continue
        item = LineItemExtraction(description=desc)
        if li.get("amount") is not None:
            try:
                item.amount = Decimal(str(li["amount"]))
            except (InvalidOperation, ValueError):
                pass
        if li.get("quantity") is not None:
            try:
                item.quantity = float(li["quantity"])
            except (ValueError, TypeError):
                pass
        if li.get("unit_price") is not None:
            try:
                item.unit_price = Decimal(str(li["unit_price"]))
            except (InvalidOperation, ValueError):
                pass
        item.confidence = 0.90
        bill_result.line_items.append(item)

    # Overall confidence
    confidence = result.get("confidence", 0.0)
    scored = [c for c in [
        bill_result.vendor_confidence,
        bill_result.bill_number_confidence,
        bill_result.date_confidence,
        bill_result.amount_confidence,
    ] if c > 0]
    bill_result.overall_confidence = round(sum(scored) / len(scored), 3) if scored else confidence

    return bill_result


def extract_from_ocr(
    tenant_id: int,
    ocr_content: str,
    from_email: str = None,
    email_subject: str = None,
    attachment_filename: str = None,
    projects: list = None,
    sub_cost_codes: list = None,
):
    """
    Extract bill fields from OCR content using the LangGraph agent.

    Returns a BillExtractionResult on success, or None on failure
    (so the caller can fall back to ClaudeExtractionService or heuristics).
    """
    try:
        email_context = {
            "from_email": from_email,
            "subject": email_subject,
            "filename": attachment_filename,
        }

        state = initial_state(
            tenant_id=tenant_id,
            ocr_content=ocr_content,
            email_context=email_context,
        )

        user_msg = _build_user_message(ocr_content, email_context, projects, sub_cost_codes)
        state["messages"] = [HumanMessage(content=user_msg)]

        result = extraction_agent.invoke(state)

        tool_result = _extract_result_from_state(result)
        if tool_result:
            return _result_to_bill_extraction(tool_result)

        logger.warning("Extraction agent did not produce a finalized result")
        return None

    except Exception as e:
        logger.warning("Extraction agent failed: %s", e)
        return None
