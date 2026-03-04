# core/ai/llm/claude.py
from __future__ import annotations

from typing import Any, Optional

from langchain.chat_models import init_chat_model

import config


def get_claude_model(
    model: str = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    **kwargs: Any,
):
    """Shared Claude chat model for all LangGraph agents. Credentials from config.Settings()."""
    settings = config.Settings()
    return init_chat_model(
        model=model or settings.anthropic_model,
        model_provider="anthropic",
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=settings.anthropic_api_key,
        **kwargs,
    )
