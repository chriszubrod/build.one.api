# Python Standard Library Imports
import json
import logging
from typing import Dict, List, Optional

# Local Imports
from workflows.workflow.business.capabilities.llm_providers.base import (
    LlmProvider,
    LlmResponse,
    TASK_CLASSIFICATION,
    TASK_EXTRACTION,
    TASK_REASONING,
    TASK_DEFAULT,
)

logger = logging.getLogger(__name__)


class AzureOpenAIProvider(LlmProvider):
    """
    Azure OpenAI provider wrapping the existing AzureOpenAIClient.
    """
    
    def __init__(self):
        self._client = None
        self._available = None
    
    @property
    def name(self) -> str:
        return "azure"
    
    def _get_client(self):
        """Lazy load the Azure OpenAI client."""
        if self._client is None:
            try:
                from integrations.azure.ai.openai_client import AzureOpenAIClient
                self._client = AzureOpenAIClient()
            except Exception as e:
                logger.warning(f"Failed to initialize Azure OpenAI client: {e}")
                self._client = None
        return self._client
    
    def is_available(self) -> bool:
        """Check if Azure OpenAI is configured and available."""
        if self._available is not None:
            return self._available
        
        try:
            client = self._get_client()
            self._available = client is not None
        except Exception:
            self._available = False
        
        return self._available
    
    def get_model_for_task(self, task_type: str) -> str:
        """
        Get the appropriate model for the task.
        
        Azure deployments are configured server-side, so we return
        the deployment name from config.
        """
        # The deployment name is configured in the client
        # For now, use the default deployment for all tasks
        # In future, could have separate deployments for reasoning (o1/o3)
        return "default"
    
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> LlmResponse:
        """
        Generate a chat completion using Azure OpenAI.
        """
        client = self._get_client()
        if client is None:
            raise RuntimeError("Azure OpenAI client not available")
        
        if json_mode:
            # Use the JSON completion method
            content = client.chat_completion_with_json(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            # chat_completion_with_json returns parsed JSON dict
            return LlmResponse(
                content=json.dumps(content),
                role="assistant",
                provider=self.name,
                model="azure-openai",
            )
        else:
            result = client.chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return LlmResponse(
                content=result.get("content", ""),
                role=result.get("role", "assistant"),
                finish_reason=result.get("finish_reason"),
                usage=result.get("usage", {}),
                provider=self.name,
                model="azure-openai",
            )
    
    def supports_json_mode(self) -> bool:
        return True
    
    def supports_task(self, task_type: str) -> bool:
        # Azure supports all task types
        return True
