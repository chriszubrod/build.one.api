from __future__ import annotations

from langchain.tools import tool
from core.ai.llm.ollama import get_chat_model


# Define the tools
@tool
def multiply(a: int, b: int) -> int:
    """Multiply two numbers together"""
    return a * b


@tool
def divide(a: int, b: int) -> int | str:
    """Divide a by b. Returns an error message if b is 0."""
    if b == 0:
        return "Error: Cannot divide by zero"
    return a // b


@tool
def add(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b



TOOLS = [multiply, divide, add]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# Initialize the model
MODEL = get_chat_model()

# Bind the tools to the model
MODEL_WITH_TOOLS = MODEL.bind_tools(TOOLS)
