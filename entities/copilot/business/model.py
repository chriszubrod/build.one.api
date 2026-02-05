# Python Standard Library Imports
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class CopilotIntent(str, Enum):
    """Detected intents from user messages."""
    # Information retrieval
    SEARCH_DOCUMENTS = "search_documents"
    QUESTION_ANSWER = "question_answer"
    GET_STATUS = "get_status"
    SUMMARIZE = "summarize"
    
    # Document operations
    CATEGORIZE_DOCUMENT = "categorize_document"
    CHECK_DUPLICATES = "check_duplicates"
    EXTRACT_DOCUMENT = "extract_document"
    
    # Entity operations
    CREATE_BILL = "create_bill"
    CREATE_INVOICE = "create_invoice"
    UPDATE_ENTITY = "update_entity"
    LIST_ENTITIES = "list_entities"
    
    # Analysis
    ANALYZE_SPENDING = "analyze_spending"
    FIND_ANOMALIES = "find_anomalies"
    
    # Workflows / compliance
    CHECK_VENDOR_COMPLIANCE = "check_vendor_compliance"

    # General
    GREETING = "greeting"
    HELP = "help"
    CLARIFICATION = "clarification"
    UNKNOWN = "unknown"


class MessageRole(str, Enum):
    """Role of a message in the conversation."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class ConversationMessage:
    """A single message in a conversation."""
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    def to_openai_message(self) -> Dict[str, str]:
        """Convert to OpenAI message format."""
        return {
            "role": self.role.value,
            "content": self.content,
        }


@dataclass
class Conversation:
    """A conversation with the copilot."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: List[ConversationMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    context: Dict[str, Any] = field(default_factory=dict)  # Persistent context
    
    def add_message(self, role: MessageRole, content: str, metadata: Optional[Dict] = None) -> ConversationMessage:
        """Add a message to the conversation."""
        message = ConversationMessage(
            role=role,
            content=content,
            metadata=metadata or {},
        )
        self.messages.append(message)
        self.updated_at = datetime.utcnow()
        return message

    def get_messages_for_context(self, max_messages: int = 20) -> List[Dict[str, str]]:
        """Get recent messages in OpenAI format for context."""
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        return [msg.to_openai_message() for msg in recent]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "messages": [msg.to_dict() for msg in self.messages],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "context": self.context,
        }


@dataclass
class CopilotResponse:
    """Response from the copilot."""
    message: str
    intent: Optional[CopilotIntent] = None
    action_taken: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    suggestions: List[str] = field(default_factory=list)
    sources: List[Dict[str, Any]] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_action: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message": self.message,
            "intent": self.intent.value if self.intent else None,
            "action_taken": self.action_taken,
            "data": self.data,
            "suggestions": self.suggestions,
            "sources": self.sources,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_action": self.confirmation_action,
        }
