# core/ai/llm.py
from __future__ import annotations

from typing import Any, Optional

from langchain.chat_models import init_chat_model


def get_chat_model(
    model: str = "qwen2.5:7b",
    model_provider: str = "ollama",
    temperature: float = 0.0,
    **kwargs: Any,
):
    """Shared chat model for all core.ai agents. Single place for provider/config."""
    return init_chat_model(
        model=model,
        model_provider=model_provider,
        temperature=temperature,
        **kwargs,
    )