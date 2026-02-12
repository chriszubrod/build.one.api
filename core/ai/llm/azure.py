# core/ai/llm/azure.py
from __future__ import annotations

from typing import Any, Optional

from langchain.chat_models import init_chat_model

import config


def get_chat_model(
    model: str = "gpt-4o-mini",
    model_provider: str = "azure_openai",
    temperature: float = 0.0,
    **kwargs: Any,
):
    """Shared chat model for all core.ai agents. Credentials from config.Settings()."""
    settings = config.Settings()
    return init_chat_model(
        model=model or settings.azure_openai_deployment_name,
        model_provider=model_provider,
        temperature=temperature,
        api_key=settings.azure_openai_api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        azure_deployment=settings.azure_openai_deployment_name,
        api_version=settings.azure_openai_api_version,
        **kwargs,
    )
