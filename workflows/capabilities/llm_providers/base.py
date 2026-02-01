# Python Standard Library Imports
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Task types for model selection
TASK_CLASSIFICATION = "classification"
TASK_EXTRACTION = "extraction"
TASK_REASONING = "reasoning"
TASK_EMBEDDING = "embedding"
TASK_DEFAULT = "default"


@dataclass
class LlmResponse:
    """
    Standardized response from an LLM provider.
    """
    content: str
    role: str = "assistant"
    finish_reason: Optional[str] = None
    usage: Dict[str, int] = field(default_factory=dict)
    provider: Optional[str] = None
    model: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "content": self.content,
            "role": self.role,
            "finish_reason": self.finish_reason,
            "usage": self.usage,
            "provider": self.provider,
            "model": self.model,
        }


class LlmProvider(ABC):
    """
    Abstract base class for LLM providers.
    
    All providers must implement these methods for consistent usage.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this provider (e.g., 'azure', 'ollama')."""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if this provider is currently available.
        
        For local providers, this may check if the service is running.
        For cloud providers, this may check credentials are configured.
        """
        pass
    
    @abstractmethod
    def get_model_for_task(self, task_type: str) -> str:
        """
        Get the appropriate model name for a given task type.
        
        Args:
            task_type: One of TASK_* constants
            
        Returns:
            Model name/identifier for this provider
        """
        pass
    
    @abstractmethod
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> LlmResponse:
        """
        Generate a chat completion.
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            json_mode: If True, request JSON output format
            
        Returns:
            LlmResponse with the generated content
            
        Raises:
            Exception: If the completion fails
        """
        pass
    
    def supports_json_mode(self) -> bool:
        """Check if this provider supports JSON response format."""
        return True
    
    def supports_task(self, task_type: str) -> bool:
        """
        Check if this provider supports a given task type.
        
        Override in subclasses to restrict capabilities.
        """
        return True
