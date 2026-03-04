# Python Standard Library Imports
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional, List, Dict


class MessageRole(str, Enum):
    """Role of a message in the conversation."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class ConversationMessage:
    """A single message in a conversation.

    For user messages, ``content`` is a plain string.
    For assistant messages, ``content`` may be a list of Claude content blocks
    (dicts with ``type`` keys such as ``text``, ``tool_use``, etc.).
    """
    role: MessageRole
    content: Any  # str for user messages, list[dict] for assistant content blocks
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def display_content(self) -> str:
        """Extract human-readable text from content (handles Claude content blocks)."""
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):
            texts = []
            for block in self.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block["text"])
                elif hasattr(block, "type") and block.type == "text":
                    texts.append(block.text)
            return "\n".join(texts) if texts else ""
        return str(self.content)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses (display-friendly)."""
        return {
            "role": self.role.value,
            "content": self.display_content(),
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class Conversation:
    """A conversation with the copilot."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: List[ConversationMessage] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    context: Dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: MessageRole, content: Any, metadata: Optional[Dict] = None) -> ConversationMessage:
        """Add a message to the conversation."""
        message = ConversationMessage(
            role=role,
            content=content,
            metadata=metadata or {},
        )
        self.messages.append(message)
        self.updated_at = datetime.utcnow()
        return message

    def add_assistant_turn(self, content_blocks) -> ConversationMessage:
        """Add an assistant turn with Claude content blocks (serialized to dicts)."""
        serialized = [
            block.model_dump() if hasattr(block, "model_dump") else block
            for block in content_blocks
        ]
        return self.add_message(MessageRole.ASSISTANT, serialized)

    def to_claude_messages(self, max_messages: int = 20) -> List[Dict[str, Any]]:
        """Convert conversation history to Claude API message format.

        Skips system messages (those go in the ``system`` parameter).
        Returns the most recent ``max_messages`` messages.
        """
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        claude_messages = []
        for msg in recent:
            if msg.role == MessageRole.SYSTEM:
                continue
            claude_messages.append({
                "role": msg.role.value,
                "content": msg.content,  # str for user, list[dict] for assistant
            })
        return claude_messages

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
    intent: Optional[str] = None
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
            "intent": self.intent,
            "action_taken": self.action_taken,
            "data": self.data,
            "suggestions": self.suggestions,
            "sources": self.sources,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_action": self.confirmation_action,
        }
