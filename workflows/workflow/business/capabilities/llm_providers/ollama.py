# Python Standard Library Imports
import json
import logging
import os
from typing import Dict, List, Optional

# Third-party Imports
import httpx

# Local Imports
from workflows.workflow.business.capabilities.llm_providers.base import (
    LlmProvider,
    LlmResponse,
    TASK_CLASSIFICATION,
    TASK_EXTRACTION,
    TASK_REASONING,
    TASK_EMBEDDING,
    TASK_DEFAULT,
)

logger = logging.getLogger(__name__)


class OllamaProvider(LlmProvider):
    """
    Local Ollama provider for running models locally.
    
    Ollama must be running locally with appropriate models pulled.
    Default: llama3.2 for general tasks, nomic-embed-text for embeddings.
    """
    
    # Default models for different task types
    DEFAULT_MODELS = {
        TASK_CLASSIFICATION: "llama3.2",
        TASK_EXTRACTION: "llama3.2",
        TASK_REASONING: "llama3.2",  # Could use larger model
        TASK_EMBEDDING: "nomic-embed-text",
        TASK_DEFAULT: "llama3.2",
    }
    
    def __init__(
        self,
        base_url: Optional[str] = None,
        models: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize Ollama provider.
        
        Args:
            base_url: Ollama API URL (default: http://localhost:11434)
            models: Override default models for task types
        """
        self.base_url = base_url or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self.base_url = self.base_url.rstrip("/")
        self.models = {**self.DEFAULT_MODELS, **(models or {})}
        self._available = None
        self._available_models: Optional[List[str]] = None
    
    @property
    def name(self) -> str:
        return "ollama"
    
    def is_available(self) -> bool:
        """Check if Ollama is running and accessible."""
        if self._available is not None:
            return self._available
        
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    self._available_models = [
                        m.get("name", "").split(":")[0] 
                        for m in data.get("models", [])
                    ]
                    self._available = True
                else:
                    self._available = False
        except Exception as e:
            logger.debug(f"Ollama not available: {e}")
            self._available = False
        
        return self._available
    
    def get_model_for_task(self, task_type: str) -> str:
        """Get the appropriate model for the task."""
        return self.models.get(task_type, self.models[TASK_DEFAULT])
    
    def _has_model(self, model_name: str) -> bool:
        """Check if a specific model is available."""
        if self._available_models is None:
            self.is_available()
        
        if self._available_models is None:
            return False
        
        # Check without version tag
        base_model = model_name.split(":")[0]
        return base_model in self._available_models
    
    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        model: Optional[str] = None,
    ) -> LlmResponse:
        """
        Generate a chat completion using Ollama.
        """
        if not self.is_available():
            raise RuntimeError("Ollama is not available")
        
        model = model or self.get_model_for_task(TASK_DEFAULT)
        
        # Check if model is pulled
        if not self._has_model(model):
            raise RuntimeError(
                f"Model '{model}' not available in Ollama. "
                f"Run: ollama pull {model}"
            )
        
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
            },
        }
        
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        
        if json_mode:
            payload["format"] = "json"
        
        try:
            with httpx.Client(timeout=120.0) as client:
                response = client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
            
            result = response.json()
            message = result.get("message", {})
            content = message.get("content", "")
            
            # If JSON mode, validate the response is valid JSON
            if json_mode:
                try:
                    json.loads(content)
                except json.JSONDecodeError:
                    # Try to extract JSON from the response
                    content = self._extract_json(content)
            
            return LlmResponse(
                content=content,
                role=message.get("role", "assistant"),
                finish_reason="stop" if result.get("done") else None,
                usage={
                    "prompt_tokens": result.get("prompt_eval_count", 0),
                    "completion_tokens": result.get("eval_count", 0),
                    "total_tokens": (
                        result.get("prompt_eval_count", 0) + 
                        result.get("eval_count", 0)
                    ),
                },
                provider=self.name,
                model=model,
            )
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e.response.status_code}")
            raise RuntimeError(f"Ollama API error: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            raise
    
    def _extract_json(self, content: str) -> str:
        """Try to extract JSON from a response that may have extra text."""
        # Try to find JSON object
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            potential_json = content[start:end]
            try:
                json.loads(potential_json)
                return potential_json
            except json.JSONDecodeError:
                pass
        
        # Return original if extraction fails
        return content
    
    def supports_json_mode(self) -> bool:
        return True
    
    def supports_task(self, task_type: str) -> bool:
        # Ollama supports all basic tasks
        # Complex reasoning might be better on cloud
        if task_type == TASK_REASONING:
            # Return True but router may prefer cloud
            return True
        return True
