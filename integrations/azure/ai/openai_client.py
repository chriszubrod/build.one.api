# Python Standard Library Imports
import json
import logging
from typing import Optional, List, Dict, Any

# Third-party Imports
import httpx

# Local Imports
import config

logger = logging.getLogger(__name__)


class AzureOpenAIError(Exception):
    """Base exception for Azure OpenAI operations."""
    pass


class AzureOpenAIClient:
    """
    Azure OpenAI client using raw HTTP REST API.
    Supports chat completions with GPT-4o-mini.
    """

    def __init__(self):
        """Initialize Azure OpenAI client."""
        settings = config.Settings()
        self.endpoint = settings.azure_openai_endpoint
        self.api_key = settings.azure_openai_api_key
        self.deployment_name = settings.azure_openai_deployment_name
        self.api_version = settings.azure_openai_api_version

        if not self.endpoint:
            raise ValueError("Azure OpenAI endpoint is required")
        if not self.api_key:
            raise ValueError("Azure OpenAI API key is required")

        # Ensure endpoint doesn't have trailing slash
        self.endpoint = self.endpoint.rstrip("/")

    def _get_headers(self) -> dict:
        """Get standard headers for Azure OpenAI requests."""
        return {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

    def _build_url(self, path: str) -> str:
        """Build the full URL for an API endpoint."""
        return f"{self.endpoint}/openai/deployments/{self.deployment_name}/{path}?api-version={self.api_version}"

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        top_p: float = 1.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
        stop: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Create a chat completion.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
                      Roles: 'system', 'user', 'assistant'
            temperature: Sampling temperature (0-2). Lower = more focused.
            max_tokens: Maximum tokens to generate.
            top_p: Nucleus sampling parameter.
            frequency_penalty: Penalty for token frequency (-2 to 2).
            presence_penalty: Penalty for token presence (-2 to 2).
            stop: Stop sequences.

        Returns:
            Dict containing the completion response with keys:
            - content: The generated text
            - role: 'assistant'
            - finish_reason: Why generation stopped
            - usage: Token usage statistics

        Raises:
            AzureOpenAIError: If the API call fails
        """
        try:
            url = self._build_url("chat/completions")
            headers = self._get_headers()

            payload = {
                "messages": messages,
                "temperature": temperature,
                "top_p": top_p,
                "frequency_penalty": frequency_penalty,
                "presence_penalty": presence_penalty,
            }

            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if stop is not None:
                payload["stop"] = stop

            logger.debug(f"Azure OpenAI chat completion request: {len(messages)} messages")

            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()

            result = response.json()
            choice = result.get("choices", [{}])[0]
            message = choice.get("message", {})

            return {
                "content": message.get("content", ""),
                "role": message.get("role", "assistant"),
                "finish_reason": choice.get("finish_reason"),
                "usage": result.get("usage", {}),
            }

        except httpx.HTTPStatusError as e:
            error_text = e.response.text if hasattr(e.response, "text") else str(e)
            logger.error(f"Azure OpenAI HTTP error: {e.response.status_code} - {error_text}")
            raise AzureOpenAIError(f"Azure OpenAI API error: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Azure OpenAI error: {e}")
            raise AzureOpenAIError(f"Azure OpenAI error: {str(e)}")

    def chat_completion_with_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a chat completion that returns JSON.

        The model is instructed to return valid JSON. The response is parsed
        and returned as a Python dict.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            temperature: Sampling temperature (0-2).
            max_tokens: Maximum tokens to generate.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            AzureOpenAIError: If the API call fails or JSON parsing fails.
        """
        try:
            url = self._build_url("chat/completions")
            headers = self._get_headers()

            payload = {
                "messages": messages,
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }

            if max_tokens is not None:
                payload["max_tokens"] = max_tokens

            logger.debug(f"Azure OpenAI JSON chat completion request: {len(messages)} messages")

            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()

            result = response.json()
            choice = result.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content", "{}")

            # Parse the JSON content
            try:
                parsed_content = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                raise AzureOpenAIError(f"Invalid JSON in response: {str(e)}")

            return parsed_content

        except httpx.HTTPStatusError as e:
            error_text = e.response.text if hasattr(e.response, "text") else str(e)
            logger.error(f"Azure OpenAI HTTP error: {e.response.status_code} - {error_text}")
            raise AzureOpenAIError(f"Azure OpenAI API error: {e.response.status_code}")
        except AzureOpenAIError:
            raise
        except Exception as e:
            logger.error(f"Azure OpenAI error: {e}")
            raise AzureOpenAIError(f"Azure OpenAI error: {str(e)}")

    def simple_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Simple helper for single-turn completions.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt for context.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens to generate.

        Returns:
            The generated text content.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        result = self.chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return result.get("content", "")
