# Python Standard Library Imports
import json
import logging
from typing import Optional, Dict, Any, List

# Local Imports
import config
from entities.copilot.business.model import (
    Conversation,
    MessageRole,
    CopilotResponse,
)
from core.ai.agents.copilot_agent.graph.agent import run_copilot

logger = logging.getLogger(__name__)
settings = config.Settings()


class CopilotService:
    """AI Copilot service powered by Claude via LangGraph.

    Provides a conversational interface to all Build.One capabilities
    using the copilot LangGraph agent with 18 tools.
    """

    def __init__(self):
        self._conversations: Dict[str, Conversation] = {}

    def get_or_create_conversation(self, conversation_id: Optional[str] = None) -> Conversation:
        """Get an existing conversation or create a new one."""
        if conversation_id and conversation_id in self._conversations:
            return self._conversations[conversation_id]
        conversation = Conversation()
        self._conversations[conversation.id] = conversation
        return conversation

    def get_conversation(self, conversation_id: str) -> Optional[Conversation]:
        """Get a conversation by ID."""
        return self._conversations.get(conversation_id)

    def chat(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> CopilotResponse:
        """Process a user message through the LangGraph copilot agent."""
        logger.info(f"Processing message: {message[:100]}...")

        conversation = self.get_or_create_conversation(conversation_id)
        conversation.add_message(MessageRole.USER, message)

        tenant_id = (context or {}).get("tenant_id", 1)

        # Convert conversation history to LangChain messages for the agent
        from langchain_core.messages import HumanMessage, AIMessage
        history = []
        for msg_dict in conversation.to_claude_messages(max_messages=20)[:-1]:
            role = msg_dict["role"]
            content = msg_dict["content"]
            if role == "user":
                if isinstance(content, str):
                    history.append(HumanMessage(content=content))
            elif role == "assistant":
                if isinstance(content, str):
                    history.append(AIMessage(content=content))
                elif isinstance(content, list):
                    text_parts = [
                        b["text"] for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    if text_parts:
                        history.append(AIMessage(content="\n".join(text_parts)))

        result = run_copilot(
            tenant_id=tenant_id,
            message=message,
            history=history,
        )

        final_text = result.get("message", "")
        tool_results = result.get("tool_results", [])

        # Store the assistant response in conversation history
        if final_text:
            conversation.add_message(MessageRole.ASSISTANT, final_text)

        suggestions = self._generate_suggestions(tool_results)
        sources = self._extract_sources(tool_results)

        return CopilotResponse(
            message=final_text,
            suggestions=suggestions,
            sources=sources,
        )

    def _generate_suggestions(self, tool_results: List[Dict[str, Any]]) -> List[str]:
        """Generate context-aware follow-up suggestions based on which tools were called."""
        if not tool_results:
            return [
                "What's in my inbox?",
                "Show me recent bills",
                "Search for documents",
            ]

        suggestions = []
        tools_used = {r["tool"] for r in tool_results}

        if "search_documents" in tools_used:
            suggestions.append("Search with different terms")
        if "list_bills" in tools_used:
            suggestions.append("Show me more details on a specific bill")
        if "list_vendors" in tools_used:
            suggestions.append("Check compliance for a vendor")
        if "get_system_status" in tools_used:
            suggestions.append("Categorize pending documents")
        if "list_inbox_emails" in tools_used:
            suggestions.append("Show me details on an email")
            suggestions.append("Process the next email")
        if "get_inbox_email_detail" in tools_used:
            suggestions.append("Extract data from the attachment")
            suggestions.append("Skip this email")
        if "extract_email_attachment" in tools_used:
            suggestions.append("Create a draft bill from this")
            suggestions.append("Create a draft expense from this")
        if "create_bill_from_extraction" in tools_used or "create_expense_from_extraction" in tools_used:
            suggestions.append("Process the next inbox email")
        if "forward_email_to_pm" in tools_used or "skip_inbox_email" in tools_used:
            suggestions.append("What else is in my inbox?")

        if not suggestions:
            suggestions = ["Ask a follow-up question", "What else can you help with?"]

        return suggestions[:3]

    def _extract_sources(self, tool_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract document source citations from search/QA tool results."""
        sources = []
        for r in tool_results:
            if r["tool"] in ("search_documents", "answer_question"):
                try:
                    result_data = json.loads(r["result"]) if isinstance(r["result"], str) else r["result"]
                except (json.JSONDecodeError, TypeError):
                    continue

                if r["tool"] == "search_documents":
                    for doc in result_data.get("documents", []):
                        sources.append({
                            "filename": doc.get("filename"),
                            "public_id": doc.get("public_id"),
                        })
                elif r["tool"] == "answer_question":
                    for src in result_data.get("sources", []):
                        sources.append({
                            "filename": src.get("filename"),
                            "public_id": src.get("public_id"),
                        })

        return sources[:5]


# Singleton instance
_copilot_service: Optional[CopilotService] = None


def get_copilot_service() -> CopilotService:
    """Get or create the singleton CopilotService instance."""
    global _copilot_service
    if _copilot_service is None:
        _copilot_service = CopilotService()
    return _copilot_service
