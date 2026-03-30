from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from core.ai.agents.email_agent.config import HEURISTIC_FALLBACK_THRESHOLD
from core.ai.agents.email_agent.graph.state import EmailClassificationState, initial_state
from core.ai.agents.email_agent.graph.tools import (
    check_sender_override,
    lookup_sender_history,
    submit_classification,
    lookup_email_thread,
    create_or_advance_thread,
)
from core.ai.agents.base import (
    AgentToolContext,
    make_setup_context,
    make_llm_call_node,
    make_tool_node,
    make_should_continue,
    make_check_complete,
    build_standard_agent_graph,
)
from core.ai.agents.email_agent.config import EMAIL_AGENT_SYSTEM_PROMPT, MAX_LLM_CALLS
from core.ai.llm.claude import get_claude_model

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    classification: str
    confidence:     float
    reasoning:      str
    signals:        list[Any]           = field(default_factory=list)

    # Thread fields — populated after create_or_advance_thread runs
    thread_id:      Optional[str]       = None
    new_stage:      Optional[str]       = None
    requires_action: bool               = False
    entity_handoff: Optional[str]       = None


# ---------------------------------------------------------------------------
# Pre-flight: detect reply/forward from subject line
# ---------------------------------------------------------------------------

def _detect_reply_forward(subject: str) -> tuple[bool, bool]:
    """
    Derive is_reply and is_forward from the email subject prefix.
    Industry-standard convention — no API call required.
    """
    s = (subject or "").strip().lower()
    is_reply   = s.startswith("re:")
    is_forward = s.startswith("fw:") or s.startswith("fwd:")
    return is_reply, is_forward


# ---------------------------------------------------------------------------
# Agent graph — built once at module level
# ---------------------------------------------------------------------------

_ALL_TOOLS = [
    check_sender_override,
    lookup_sender_history,
    submit_classification,
    lookup_email_thread,
    create_or_advance_thread,
]
_TOOLS_BY_NAME = {t.name: t for t in _ALL_TOOLS}


def _get_model_with_tools():
    return get_claude_model().bind_tools(_ALL_TOOLS)


email_agent = build_standard_agent_graph(
    state_class=EmailClassificationState,
    setup_fn=make_setup_context(AgentToolContext, EMAIL_AGENT_SYSTEM_PROMPT),
    llm_call_fn=make_llm_call_node(_get_model_with_tools, EMAIL_AGENT_SYSTEM_PROMPT),
    tool_node_fn=make_tool_node(_TOOLS_BY_NAME),
    should_continue_fn=make_should_continue(max_calls=MAX_LLM_CALLS),
    check_complete_fn=make_check_complete(),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_user_message(state: dict) -> str:
    """Build the user-facing message injected into the agent graph."""
    body_preview = (state.get("body") or "")[:2000]
    attachments  = state.get("attachments") or []
    att_summary  = ", ".join(a.get("name", "") for a in attachments) if attachments else "none"

    thread_context = ""
    if state.get("thread_id"):
        thread_context = (
            f"\nTHREAD_ID: {state['thread_id']}"
            f"\nTHREAD_STAGE: {state.get('thread_stage', 'unknown')}"
        )

    return (
        f"SUBJECT: {state.get('subject', '')}\n"
        f"FROM: {state.get('from_email', '')}\n"
        f"IS_REPLY: {state.get('is_reply', False)}\n"
        f"IS_FORWARD: {state.get('is_forward', False)}\n"
        f"ATTACHMENTS: {att_summary}"
        f"{thread_context}\n\n"
        f"BODY:\n{body_preview}"
    )


def _extract_result_from_state(agent_state: dict) -> Optional[dict]:
    """
    Walk messages in reverse to find the last successful submit_classification
    ToolMessage. Returns the parsed result dict or None.
    """
    from langchain_core.messages import ToolMessage
    for message in reversed(agent_state.get("messages", [])):
        if not isinstance(message, ToolMessage):
            continue
        try:
            result = ast.literal_eval(message.content)
            if isinstance(result, dict) and result.get("success") and "classification_type" in result:
                return result
        except Exception:
            continue
    return None


def _extract_thread_result_from_state(agent_state: dict) -> Optional[dict]:
    """
    Walk messages in reverse to find the last successful create_or_advance_thread
    ToolMessage. Returns the parsed result dict or None.
    """
    from langchain_core.messages import ToolMessage
    for message in reversed(agent_state.get("messages", [])):
        if not isinstance(message, ToolMessage):
            continue
        try:
            result = ast.literal_eval(message.content)
            if isinstance(result, dict) and result.get("success") and "thread_id" in result:
                return result
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def classify_email_heuristic(
    subject:     str,
    from_email:  str,
    body:        str,
    attachments: list[dict],
) -> ClassificationResult:
    """
    Heuristic-only classification — no agent call.
    Used by list_inbox() to avoid blocking page load.
    Thread detection is not run here — heuristic is classification-only.
    """
    from core.ai.agents.email_agent.graph.classifier import EmailClassifier
    classifier = EmailClassifier()
    result = classifier.classify(
        subject=subject,
        from_email=from_email,
        body=body,
        attachments=attachments,
    )
    return ClassificationResult(
        classification=result.classification_type,
        confidence=result.confidence,
        reasoning=result.reasoning,
        signals=result.signals,
    )


def classify_email(
    subject:             str,
    from_email:          str,
    body:                str,
    attachments:         list[dict],
    inbox_record_id:     Optional[int] = None,
    internet_message_id: Optional[str] = None,
    sender_role:         str           = "ORIGINATOR",
) -> ClassificationResult:
    """
    Full two-phase classification with thread awareness.

    Phase 1 — Pre-flight:
        Detect is_reply and is_forward from subject prefix.
        Look up existing EmailThread by internet_message_id if provided.

    Phase 2 — Classification:
        Run EmailClassifier heuristic. If confidence >= threshold, return.
        Otherwise invoke the LangGraph agent.

    Phase 3 — Thread write:
        Call create_or_advance_thread to persist the thread state.
        Returns full ClassificationResult including thread fields.
    """
    # ------------------------------------------------------------------
    # Phase 1 — Pre-flight
    # ------------------------------------------------------------------
    is_reply, is_forward = _detect_reply_forward(subject)

    existing_thread_id = None
    existing_stage     = None

    if internet_message_id:
        try:
            from entities.inbox.persistence.email_thread_repo import EmailThreadRepository
            thread_repo    = EmailThreadRepository()
            existing_thread = thread_repo.read_by_internet_message_id(internet_message_id)
            if existing_thread:
                existing_thread_id = existing_thread.public_id
                existing_stage     = existing_thread.current_stage
        except Exception as error:
            logger.warning(f"Thread preflight lookup failed (non-fatal): {error}")

    # ------------------------------------------------------------------
    # Phase 2 — Classification (heuristic → agent)
    # ------------------------------------------------------------------
    from core.ai.agents.email_agent.graph.classifier import EmailClassifier
    classifier  = EmailClassifier()
    heuristic   = classifier.classify(
        subject=subject,
        from_email=from_email,
        body=body,
        attachments=attachments,
    )

    classification = heuristic.classification_type
    confidence     = heuristic.confidence
    reasoning      = heuristic.reasoning
    signals        = heuristic.signals

    if confidence < HEURISTIC_FALLBACK_THRESHOLD:
        try:
            state = initial_state()
            state.update({
                "subject":              subject,
                "from_email":           from_email,
                "body":                 body,
                "attachments":          attachments,
                "is_reply":             is_reply,
                "is_forward":           is_forward,
                "internet_message_id":  internet_message_id,
                "thread_id":            existing_thread_id,
                "thread_stage":         existing_stage,
            })

            agent_state = email_agent.invoke({
                "messages": [{"role": "user", "content": _build_user_message(state)}],
                **state,
            })

            agent_result = _extract_result_from_state(agent_state)
            if agent_result:
                classification = agent_result["classification_type"]
                confidence     = agent_result["confidence"]
                reasoning      = f"agent_reasoning: {agent_result.get('reasoning', '')}"
                signals        = heuristic.signals + [reasoning]

        except Exception as error:
            logger.error(f"Email agent invocation failed, using heuristic: {error}")

    # ------------------------------------------------------------------
    # Phase 3 — Thread write
    # ------------------------------------------------------------------
    thread_id      = None
    new_stage      = None
    action_required = False
    entity_handoff = None

    if inbox_record_id:
        try:
            thread_result = create_or_advance_thread.invoke({
                "classification_type":  classification,
                "confidence":           confidence,
                "inbox_record_id":      inbox_record_id,
                "internet_message_id":  internet_message_id or "",
                "subject":              subject,
                "is_reply":             is_reply,
                "is_forward":           is_forward,
                "sender_role":          sender_role,
                "existing_thread_id":   existing_thread_id or "",
                "current_stage":        existing_stage or "",
            })

            if thread_result.get("success"):
                thread_id       = thread_result.get("thread_id")
                new_stage       = thread_result.get("new_stage")
                action_required = thread_result.get("requires_action", False)
                entity_handoff  = thread_result.get("entity_handoff") or None

        except Exception as error:
            logger.error(f"create_or_advance_thread failed (non-fatal): {error}")

    return ClassificationResult(
        classification=  classification,
        confidence=      confidence,
        reasoning=       reasoning,
        signals=         signals,
        thread_id=       thread_id,
        new_stage=       new_stage,
        requires_action= action_required,
        entity_handoff=  entity_handoff,
    )
