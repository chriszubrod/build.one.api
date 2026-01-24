# Python Standard Library Imports
import json
import logging
from typing import Optional, Dict, Any, List

# Third-party Imports

# Local Imports
from modules.copilot.business.model import (
    Conversation,
    ConversationMessage,
    MessageRole,
    CopilotIntent,
    CopilotResponse,
)
from integrations.azure.ai import AzureOpenAIClient

logger = logging.getLogger(__name__)


# System prompt for the copilot
COPILOT_SYSTEM_PROMPT = """You are an AI assistant for Build.One, a construction management application.

IMPORTANT: Be action-oriented. When users ask for information, provide it directly. DO NOT say "hold on", "let me search", or "please wait" - just give the answer or say what you found.

Your capabilities:
1. Answer questions about documents, bills, invoices, projects, and vendors
2. Search for documents and information
3. Help categorize and organize documents
4. Provide insights on spending and project costs
5. Check for duplicate or suspicious documents

Context about the system:
- Users upload documents (PDFs, images) which are automatically extracted and categorized
- Bills are invoices FROM vendors TO the user (accounts payable)
- Invoices are documents FROM the user TO their customers (accounts receivable)
- Projects contain multiple bills, vendors, and documents
- Currently, I can search indexed DOCUMENTS but cannot directly query the Bills, Vendors, or Projects database tables

IMPORTANT LIMITATIONS - Be honest about these:
- I can search document content that has been indexed
- I CANNOT query the Bills table directly (bill numbers, amounts, dates)
- I CANNOT query Vendors or Projects tables
- If asked about bills/vendors/projects data, explain this limitation

Guidelines:
- Be concise and direct
- Give answers immediately - don't say you'll "search" or "look into it"
- If you can't answer, say so clearly with the reason
- For clarifying questions, ask them but don't promise future action

Response style:
- GOOD: "I found 3 documents mentioning Walker Lumber..."
- GOOD: "I don't have access to the Bills database, so I can't list your bills. I can only search document content."
- BAD: "Let me search for that. Please hold on..."
- BAD: "I will look into this for you...\""""


INTENT_DETECTION_PROMPT = """Analyze the user's message and determine their intent.

Respond with JSON only:
{
    "intent": "intent_name",
    "confidence": 0.85,
    "entities": {
        "document_id": "if mentioned",
        "vendor_name": "if mentioned",
        "project_name": "if mentioned",
        "date_range": "if mentioned",
        "amount": "if mentioned"
    },
    "requires_search": true/false,
    "requires_action": true/false
}

Available intents:
- search_documents: User wants to find documents
- question_answer: User is asking a question about documents/data
- get_status: User wants to know status of something
- summarize: User wants a summary
- categorize_document: User wants to categorize a document
- check_duplicates: User wants to check for duplicates
- create_bill: User wants to create a bill
- list_entities: User wants to list bills, vendors, projects, etc.
- analyze_spending: User wants spending analysis
- greeting: User is greeting or starting conversation
- help: User needs help understanding the system
- unknown: Cannot determine intent"""


class CopilotService:
    """
    AI Copilot service that orchestrates conversations and actions.
    
    Provides a conversational interface to all Build.One AI capabilities.
    """

    def __init__(self, openai_client: Optional[AzureOpenAIClient] = None):
        """Initialize the CopilotService."""
        self._openai_client = openai_client
        self._conversations: Dict[str, Conversation] = {}
        
        # Lazy-loaded services
        self._qa_service = None
        self._search_service = None
        self._categorization_service = None
        self._anomaly_service = None

    @property
    def openai_client(self) -> AzureOpenAIClient:
        """Lazy load OpenAI client."""
        if self._openai_client is None:
            self._openai_client = AzureOpenAIClient()
        return self._openai_client

    @property
    def qa_service(self):
        """Lazy load Q&A service."""
        if self._qa_service is None:
            from modules.qa.business.service import get_qa_service
            self._qa_service = get_qa_service()
        return self._qa_service

    @property
    def search_service(self):
        """Lazy load search service."""
        if self._search_service is None:
            from modules.search.business.service import get_search_service
            self._search_service = get_search_service()
        return self._search_service

    @property
    def categorization_service(self):
        """Lazy load categorization service."""
        if self._categorization_service is None:
            from modules.categorization.business.service import get_categorization_service
            self._categorization_service = get_categorization_service()
        return self._categorization_service

    @property
    def anomaly_service(self):
        """Lazy load anomaly service."""
        if self._anomaly_service is None:
            from modules.anomaly.business.service import get_anomaly_service
            self._anomaly_service = get_anomaly_service()
        return self._anomaly_service

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
        """
        Process a user message and return a response.

        Args:
            message: The user's message
            conversation_id: Optional conversation ID for context
            context: Optional additional context

        Returns:
            CopilotResponse with the assistant's response
        """
        logger.info(f"Processing message: {message[:100]}...")

        # Get or create conversation
        conversation = self.get_or_create_conversation(conversation_id)
        
        # Add user message
        conversation.add_message(MessageRole.USER, message)

        # Detect intent
        intent_result = self._detect_intent(message)
        intent = intent_result.get("intent", "unknown")
        
        try:
            intent_enum = CopilotIntent(intent)
        except ValueError:
            intent_enum = CopilotIntent.UNKNOWN

        # Route based on intent
        response = self._route_intent(
            intent_enum,
            message,
            conversation,
            intent_result,
            context,
        )

        # Add assistant response to conversation
        conversation.add_message(
            MessageRole.ASSISTANT,
            response.message,
            metadata={"intent": intent, "action": response.action_taken},
        )

        return response

    def _detect_intent(self, message: str) -> Dict[str, Any]:
        """Detect the intent of a user message."""
        try:
            response = self.openai_client.chat_completion_with_json(
                messages=[
                    {"role": "system", "content": INTENT_DETECTION_PROMPT},
                    {"role": "user", "content": message},
                ],
                temperature=0.1,
            )
            return response
        except Exception as e:
            logger.error(f"Intent detection error: {e}")
            return {"intent": "unknown", "confidence": 0.0}

    def _route_intent(
        self,
        intent: CopilotIntent,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Route to appropriate handler based on intent."""
        
        handlers = {
            CopilotIntent.SEARCH_DOCUMENTS: self._handle_search,
            CopilotIntent.QUESTION_ANSWER: self._handle_question,
            CopilotIntent.GET_STATUS: self._handle_status,
            CopilotIntent.CATEGORIZE_DOCUMENT: self._handle_categorize,
            CopilotIntent.CHECK_DUPLICATES: self._handle_duplicates,
            CopilotIntent.LIST_ENTITIES: self._handle_list,
            CopilotIntent.GREETING: self._handle_greeting,
            CopilotIntent.HELP: self._handle_help,
        }

        handler = handlers.get(intent, self._handle_general)
        
        try:
            return handler(message, conversation, intent_result, context)
        except Exception as e:
            logger.error(f"Handler error for {intent}: {e}")
            return self._handle_general(message, conversation, intent_result, context)

    def _handle_search(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle document search requests."""
        entities = intent_result.get("entities", {})
        query = entities.get("search_query", message)

        try:
            results = self.search_service.hybrid_search(query, top=5)
            
            if not results:
                return CopilotResponse(
                    message="I couldn't find any documents matching your search. Try different keywords or check if the documents have been uploaded and extracted.",
                    intent=CopilotIntent.SEARCH_DOCUMENTS,
                    suggestions=[
                        "Try broader search terms",
                        "Check pending extractions",
                        "Upload new documents",
                    ],
                )

            # Format results
            result_text = f"I found {len(results)} document(s) matching your search:\n\n"
            for i, doc in enumerate(results, 1):
                filename = doc.get("original_filename") or doc.get("filename", "Unknown")
                category = doc.get("category", "Uncategorized")
                score = doc.get("@search.score", 0)
                result_text += f"{i}. **{filename}** ({category}) - Relevance: {score:.0%}\n"

            return CopilotResponse(
                message=result_text,
                intent=CopilotIntent.SEARCH_DOCUMENTS,
                action_taken="search",
                data={"results": results},
                suggestions=[
                    "View document details",
                    "Categorize uncategorized documents",
                    "Search with different terms",
                ],
            )

        except Exception as e:
            logger.error(f"Search error: {e}")
            return CopilotResponse(
                message=f"I encountered an error while searching: {str(e)}",
                intent=CopilotIntent.SEARCH_DOCUMENTS,
            )

    def _handle_question(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle Q&A requests."""
        # Check if question is about database entities we can't query
        message_lower = message.lower()
        if any(word in message_lower for word in ["bills", "invoices", "vendors", "projects"]):
            if any(word in message_lower for word in ["how many", "list", "show me", "what", "total"]):
                return CopilotResponse(
                    message="I can't directly query the Bills, Vendors, or Projects database yet. I can only search through indexed document content.\n\nTo answer questions like 'What bills do I have?' or 'Show me vendors', I would need database query access which isn't implemented yet.\n\nI *can* search for documents that mention specific terms. Try: 'Search for documents mentioning Walker Lumber'",
                    intent=CopilotIntent.QUESTION_ANSWER,
                    suggestions=[
                        "Search documents for [vendor name]",
                        "What's the system status?",
                        "Help",
                    ],
                )
        
        try:
            qa_result = self.qa_service.ask(message)
            
            sources = []
            if qa_result.get("sources"):
                sources = [
                    {"filename": s.get("filename"), "public_id": s.get("public_id")}
                    for s in qa_result["sources"][:3]
                ]

            return CopilotResponse(
                message=qa_result.get("answer", "I couldn't find an answer to that question."),
                intent=CopilotIntent.QUESTION_ANSWER,
                sources=sources,
                suggestions=["Ask a follow-up question", "Search for related documents"],
            )

        except Exception as e:
            logger.error(f"Q&A error: {e}")
            return CopilotResponse(
                message=f"I had trouble answering that question. Error: {str(e)}",
                intent=CopilotIntent.QUESTION_ANSWER,
            )

    def _handle_status(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle status check requests."""
        try:
            from modules.attachment.persistence.repo import AttachmentRepository
            repo = AttachmentRepository()

            # Get pending categorizations
            pending_cat = repo.read_pending_categorization(limit=10)
            
            # Get pending extractions
            pending_ext = repo.read_pending_extraction()

            status_msg = "**System Status:**\n\n"
            status_msg += f"- **{len(pending_ext)}** documents pending extraction\n"
            status_msg += f"- **{len(pending_cat)}** documents pending categorization\n"

            if pending_cat:
                status_msg += "\n**Recent uncategorized documents:**\n"
                for doc in pending_cat[:5]:
                    filename = doc.original_filename or doc.filename
                    status_msg += f"- {filename}\n"

            suggestions = []
            if pending_ext:
                suggestions.append("Process pending extractions")
            if pending_cat:
                suggestions.append("Categorize pending documents")

            return CopilotResponse(
                message=status_msg,
                intent=CopilotIntent.GET_STATUS,
                data={
                    "pending_extraction": len(pending_ext),
                    "pending_categorization": len(pending_cat),
                },
                suggestions=suggestions or ["All caught up!"],
            )

        except Exception as e:
            logger.error(f"Status check error: {e}")
            return CopilotResponse(
                message=f"Error checking status: {str(e)}",
                intent=CopilotIntent.GET_STATUS,
            )

    def _handle_categorize(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle categorization requests."""
        entities = intent_result.get("entities", {})
        doc_id = entities.get("document_id")

        if not doc_id:
            return CopilotResponse(
                message="Which document would you like me to categorize? Please provide the document ID or name.",
                intent=CopilotIntent.CATEGORIZE_DOCUMENT,
                requires_confirmation=False,
            )

        try:
            result = self.categorization_service.categorize_attachment_by_public_id(doc_id)
            
            if result:
                return CopilotResponse(
                    message=f"I've categorized the document as **{result.category.value}** with {result.confidence:.0%} confidence.\n\nReasoning: {result.reasoning}",
                    intent=CopilotIntent.CATEGORIZE_DOCUMENT,
                    action_taken="categorize",
                    data=result.to_dict(),
                )
            else:
                return CopilotResponse(
                    message="I couldn't categorize that document. Make sure it exists and has been extracted.",
                    intent=CopilotIntent.CATEGORIZE_DOCUMENT,
                )

        except Exception as e:
            logger.error(f"Categorization error: {e}")
            return CopilotResponse(
                message=f"Error categorizing document: {str(e)}",
                intent=CopilotIntent.CATEGORIZE_DOCUMENT,
            )

    def _handle_duplicates(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle duplicate check requests."""
        entities = intent_result.get("entities", {})
        doc_id = entities.get("document_id")

        if not doc_id:
            return CopilotResponse(
                message="Which document would you like me to check for duplicates? Please provide the document ID.",
                intent=CopilotIntent.CHECK_DUPLICATES,
            )

        try:
            result = self.anomaly_service.check_attachment_by_public_id(doc_id)
            
            if result and result.has_anomaly:
                msg = f"**Anomaly detected:** {result.message}\n\n"
                if result.related_documents:
                    msg += "Related documents:\n"
                    for doc in result.related_documents:
                        msg += f"- {doc.filename} ({doc.match_reason})\n"
                
                return CopilotResponse(
                    message=msg,
                    intent=CopilotIntent.CHECK_DUPLICATES,
                    action_taken="check_duplicates",
                    data=result.to_dict(),
                )
            else:
                return CopilotResponse(
                    message="No duplicates or anomalies detected for this document.",
                    intent=CopilotIntent.CHECK_DUPLICATES,
                    action_taken="check_duplicates",
                )

        except Exception as e:
            logger.error(f"Duplicate check error: {e}")
            return CopilotResponse(
                message=f"Error checking for duplicates: {str(e)}",
                intent=CopilotIntent.CHECK_DUPLICATES,
            )

    def _handle_list(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle list entity requests."""
        return CopilotResponse(
            message="I can't list Bills, Vendors, or Projects directly from the database yet - that feature isn't implemented.\n\n**What I can do:**\n- Search indexed document content\n- Check system status (pending extractions/categorizations)\n- Answer questions about document content\n\n**What you can do:**\n- Use the Bills page to view your bills\n- Use the Vendors page to view vendors\n- Ask me to search for documents mentioning specific terms",
            intent=CopilotIntent.LIST_ENTITIES,
            suggestions=[
                "What's the system status?",
                "Search for documents about [topic]",
                "Help",
            ],
        )

    def _handle_greeting(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle greetings."""
        return CopilotResponse(
            message="Hello! I'm your Build.One AI assistant. I can help you with:\n\n"
                   "- **Searching** for documents, bills, and invoices\n"
                   "- **Answering questions** about your project data\n"
                   "- **Categorizing** uploaded documents\n"
                   "- **Checking** for duplicate or suspicious documents\n"
                   "- **Summarizing** project costs and spending\n\n"
                   "What would you like help with today?",
            intent=CopilotIntent.GREETING,
            suggestions=[
                "Show me pending documents",
                "Search for recent bills",
                "What's the status of my uploads?",
            ],
        )

    def _handle_help(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle help requests."""
        return CopilotResponse(
            message="Here's what I can help you with:\n\n"
                   "**Document Management:**\n"
                   "- \"Find invoices from ABC Lumber\"\n"
                   "- \"Categorize document XYZ\"\n"
                   "- \"Check this document for duplicates\"\n\n"
                   "**Questions & Answers:**\n"
                   "- \"What bills are due this week?\"\n"
                   "- \"How much have we spent on Project 123?\"\n"
                   "- \"Show me all contracts expiring soon\"\n\n"
                   "**Status & Reports:**\n"
                   "- \"What documents need review?\"\n"
                   "- \"Show pending categorizations\"\n"
                   "- \"Summarize recent activity\"\n\n"
                   "Just ask naturally - I'll figure out what you need!",
            intent=CopilotIntent.HELP,
        )

    def _handle_general(
        self,
        message: str,
        conversation: Conversation,
        intent_result: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> CopilotResponse:
        """Handle general conversation using GPT."""
        try:
            # Build messages with conversation history
            messages = [{"role": "system", "content": COPILOT_SYSTEM_PROMPT}]
            messages.extend(conversation.get_messages_for_context(max_messages=10))

            response = self.openai_client.chat_completion(
                messages=messages,
                temperature=0.7,
            )

            content = response.get("content", "I'm not sure how to help with that.")

            # Check if response contains an action
            action_taken = None
            if "```json" in content:
                try:
                    json_start = content.find("```json") + 7
                    json_end = content.find("```", json_start)
                    action_json = content[json_start:json_end].strip()
                    action_data = json.loads(action_json)
                    action_taken = action_data.get("action")
                    # Remove action block from message
                    content = content[:content.find("```json")].strip()
                except:
                    pass

            return CopilotResponse(
                message=content,
                intent=CopilotIntent.UNKNOWN,
                action_taken=action_taken,
            )

        except Exception as e:
            logger.error(f"General handler error: {e}")
            return CopilotResponse(
                message="I encountered an error processing your request. Please try again.",
                intent=CopilotIntent.UNKNOWN,
            )


# Singleton instance
_copilot_service: Optional[CopilotService] = None


def get_copilot_service() -> CopilotService:
    """Get or create the singleton CopilotService instance."""
    global _copilot_service
    if _copilot_service is None:
        _copilot_service = CopilotService()
    return _copilot_service
