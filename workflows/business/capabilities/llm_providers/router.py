# Python Standard Library Imports
import logging
import os
from typing import Dict, List, Optional

# Local Imports
from workflows.capabilities.llm_providers.base import (
    LlmProvider,
    LlmResponse,
    TASK_CLASSIFICATION,
    TASK_EXTRACTION,
    TASK_REASONING,
    TASK_EMBEDDING,
    TASK_DEFAULT,
)

logger = logging.getLogger(__name__)


class LlmProviderRouter:
    """
    Routes LLM requests to the appropriate provider based on task type and availability.
    
    Routing strategy:
    - Simple tasks (classification, extraction): Try local first, fallback to cloud
    - Complex reasoning: Use cloud (better models like o1/o3)
    - Embeddings: Local if available
    
    Configuration via environment:
    - LLM_PREFER_LOCAL=true: Prefer local providers when available
    - LLM_CLOUD_ONLY=true: Skip local providers entirely
    """
    
    # Tasks that prefer local providers
    LOCAL_PREFERRED_TASKS = {TASK_CLASSIFICATION, TASK_EXTRACTION, TASK_EMBEDDING}
    
    # Tasks that should use cloud
    CLOUD_PREFERRED_TASKS = {TASK_REASONING}
    
    def __init__(self):
        self._providers: Dict[str, LlmProvider] = {}
        self._initialized = False
        
        # Configuration
        self.prefer_local = os.getenv("LLM_PREFER_LOCAL", "true").lower() == "true"
        self.cloud_only = os.getenv("LLM_CLOUD_ONLY", "false").lower() == "true"
    
    def _initialize_providers(self) -> None:
        """Lazy initialize all providers."""
        if self._initialized:
            return
        
        # Initialize local providers
        if not self.cloud_only:
            try:
                from workflows.capabilities.llm_providers.ollama import OllamaProvider
                ollama = OllamaProvider()
                self._providers["ollama"] = ollama
                logger.debug("Initialized Ollama provider")
            except Exception as e:
                logger.debug(f"Ollama provider not available: {e}")
        
        # Initialize cloud providers
        try:
            from workflows.capabilities.llm_providers.azure import AzureOpenAIProvider
            azure = AzureOpenAIProvider()
            self._providers["azure"] = azure
            logger.debug("Initialized Azure OpenAI provider")
        except Exception as e:
            logger.debug(f"Azure OpenAI provider not available: {e}")
        
        self._initialized = True
    
    def get_provider(self, task_type: str = TASK_DEFAULT) -> LlmProvider:
        """
        Get the best available provider for a given task type.
        
        Args:
            task_type: One of TASK_* constants
            
        Returns:
            The selected LlmProvider
            
        Raises:
            RuntimeError: If no provider is available
        """
        self._initialize_providers()
        
        if not self._providers:
            raise RuntimeError("No LLM providers configured")
        
        # Determine provider order based on task type
        if task_type in self.CLOUD_PREFERRED_TASKS:
            provider_order = ["azure", "ollama"]
        elif task_type in self.LOCAL_PREFERRED_TASKS and self.prefer_local:
            provider_order = ["ollama", "azure"]
        else:
            provider_order = ["azure", "ollama"]
        
        # Find first available provider that supports the task
        for provider_name in provider_order:
            provider = self._providers.get(provider_name)
            if provider is None:
                continue
            
            if not provider.is_available():
                logger.debug(f"Provider {provider_name} not available")
                continue
            
            if not provider.supports_task(task_type):
                logger.debug(f"Provider {provider_name} doesn't support task {task_type}")
                continue
            
            logger.debug(f"Selected provider {provider_name} for task {task_type}")
            return provider
        
        raise RuntimeError(
            f"No available provider for task type '{task_type}'. "
            f"Checked providers: {list(self._providers.keys())}"
        )
    
    def get_provider_by_name(self, name: str) -> Optional[LlmProvider]:
        """Get a specific provider by name."""
        self._initialize_providers()
        return self._providers.get(name)
    
    def list_available_providers(self) -> List[str]:
        """List all available provider names."""
        self._initialize_providers()
        return [
            name for name, provider in self._providers.items()
            if provider.is_available()
        ]
    
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        task_type: str = TASK_DEFAULT,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        fallback: bool = True,
    ) -> LlmResponse:
        """
        Route a chat completion to the appropriate provider.
        
        Args:
            messages: Chat messages
            task_type: Task type for provider selection
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            json_mode: Request JSON output
            fallback: If True, try next provider on failure
            
        Returns:
            LlmResponse from the selected provider
        """
        self._initialize_providers()
        
        # Get ordered list of providers to try
        if task_type in self.CLOUD_PREFERRED_TASKS:
            provider_order = ["azure", "ollama"]
        elif task_type in self.LOCAL_PREFERRED_TASKS and self.prefer_local:
            provider_order = ["ollama", "azure"]
        else:
            provider_order = ["azure", "ollama"]
        
        last_error: Optional[Exception] = None
        
        for provider_name in provider_order:
            provider = self._providers.get(provider_name)
            if provider is None:
                continue
            
            if not provider.is_available():
                continue
            
            if not provider.supports_task(task_type):
                continue
            
            try:
                logger.debug(f"Trying provider {provider_name} for task {task_type}")
                return provider.chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
            except Exception as e:
                logger.warning(f"Provider {provider_name} failed: {e}")
                last_error = e
                
                if not fallback:
                    raise
                
                # Continue to next provider
                continue
        
        if last_error:
            raise last_error
        
        raise RuntimeError(f"No available provider for task type '{task_type}'")


# Global router instance
_router: Optional[LlmProviderRouter] = None


def get_llm_router() -> LlmProviderRouter:
    """Get the global LLM provider router instance."""
    global _router
    if _router is None:
        _router = LlmProviderRouter()
    return _router
