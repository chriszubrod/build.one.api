"""
Email Classification Agent

LangGraph agent that classifies incoming emails using Claude.
Falls back to heuristic classification when the agent fails.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from langchain_core.messages import HumanMessage, AIMessage

from core.ai.agents.base import (
    AgentToolContext,
    make_setup_context,
    make_llm_call_node,
    make_tool_node,
    make_should_continue,
    make_check_complete,
    build_standard_agent_graph,
)
from core.ai.agents.email_agent.config import (
    EMAIL_AGENT_SYSTEM_PROMPT,
    MAX_LLM_CALLS,
)
from core.ai.agents.email_agent.graph.state import (
    EmailClassificationState,
    initial_state,
)
from core.ai.agents.email_agent.graph.tools import (
    EMAIL_AGENT_TOOLS,
    TOOLS_BY_NAME,
)
from core.ai.llm.claude import get_claude_model
from core.ai.email_classifier import (
    EmailClassifier,
    ClassificationResult,
    MessageType,
)

logger = logging.getLogger(__name__)


def _get_model_with_tools():
    """Get Claude model with email agent tools bound."""
    model = get_claude_model()
    return model.bind_tools(EMAIL_AGENT_TOOLS)


# Build the graph
email_agent = build_standard_agent_graph(
    state_class=EmailClassificationState,
    setup_fn=make_setup_context(AgentToolContext, EMAIL_AGENT_SYSTEM_PROMPT),
    llm_call_fn=make_llm_call_node(_get_model_with_tools, EMAIL_AGENT_SYSTEM_PROMPT),
    tool_node_fn=make_tool_node(TOOLS_BY_NAME),
    should_continue_fn=make_should_continue(max_calls=MAX_LLM_CALLS),
    check_complete_fn=make_check_complete(),  # interactive mode: end after one response
)


def _build_user_message(subject: str, from_email: str, body: str, attachments: list) -> str:
    """Build the user message describing the email to classify."""
    parts = [f"Subject: {subject}"]
    if from_email:
        parts.append(f"From: {from_email}")
    if attachments:
        att_list = ", ".join(
            f"{a.get('name', 'unknown')} ({a.get('content_type', 'unknown')})"
            for a in attachments
        )
        parts.append(f"Attachments: {att_list}")
    if body:
        # Truncate body to avoid token waste
        body_truncated = body[:2000]
        if len(body) > 2000:
            body_truncated += "\n... [truncated]"
        parts.append(f"\nBody:\n{body_truncated}")

    parts.append("\nPlease classify this email. Check for sender overrides first.")
    return "\n".join(parts)


def _extract_result_from_state(state: dict) -> Optional[dict]:
    """Extract the submit_classification result from tool messages in the state."""
    from langchain_core.messages import ToolMessage
    import ast

    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, ToolMessage):
            try:
                content = msg.content
                if content.startswith("{"):
                    result = ast.literal_eval(content)
                    if isinstance(result, dict) and result.get("success") and "classification_type" in result:
                        return result
            except Exception:
                continue
    return None


def classify_email_heuristic(
    subject: str = "",
    from_email: str = "",
    body: str = "",
    attachments: list = None,
    override_service=None,
) -> ClassificationResult:
    """
    Heuristic-only classification (no agent call).  Fast and free.
    Used during list_inbox() to avoid blocking page load.
    """
    attachments = attachments or []
    heuristic = EmailClassifier(override_service=override_service)
    return heuristic.classify(
        subject=subject,
        from_email=from_email,
        body=body,
        attachments=attachments,
    )


def classify_email(
    tenant_id: int,
    subject: str = "",
    from_email: str = "",
    body: str = "",
    attachments: list = None,
    override_service=None,
) -> ClassificationResult:
    """
    Classify an email using the LangGraph agent with heuristic fallback.

    Runs the heuristic classifier first (free, fast). If confidence is below
    the threshold, invokes the LangGraph agent for a smarter classification.

    Returns a ClassificationResult compatible with the existing interface.
    """
    from core.ai.agents.email_agent.config import HEURISTIC_FALLBACK_THRESHOLD
    attachments = attachments or []

    # --- Step 1: Try heuristic first (free, instant) ---
    heuristic = EmailClassifier(override_service=override_service)
    heuristic_result = heuristic.classify(
        subject=subject,
        from_email=from_email,
        body=body,
        attachments=attachments,
    )

    # If heuristic is confident enough, use it
    if heuristic_result.confidence >= HEURISTIC_FALLBACK_THRESHOLD:
        return heuristic_result

    # --- Step 2: Heuristic uncertain — invoke the agent ---
    try:
        state = initial_state(
            tenant_id=tenant_id,
            subject=subject,
            from_email=from_email,
            body=body,
            attachments=attachments,
        )

        user_msg = _build_user_message(subject, from_email, body, attachments)
        state["messages"] = [HumanMessage(content=user_msg)]

        result = email_agent.invoke(state)

        # Extract the classification from the submit_classification tool result
        tool_result = _extract_result_from_state(result)
        if tool_result:
            return ClassificationResult(
                message_type=MessageType(tool_result["classification_type"]),
                confidence=tool_result["confidence"],
                signals=heuristic_result.signals + [
                    f"agent_reasoning: {tool_result.get('reasoning', '')}",
                ],
            )

        # Agent ran but didn't call submit_classification — fall back
        logger.warning("Email agent did not produce a classification, using heuristic")
        return heuristic_result

    except Exception as e:
        logger.warning("Email classification agent failed, using heuristic: %s", e)
        return heuristic_result
