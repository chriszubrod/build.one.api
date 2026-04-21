"""Tool registry — name → Tool lookup.

A single global registry. Tools register themselves at import time; callers
resolve by name when assembling an agent's tool set.
"""
from typing import Optional

from intelligence.tools.base import Tool


_tools: dict[str, Tool] = {}


def register(tool: Tool) -> None:
    if tool.name in _tools:
        raise ValueError(f"Tool already registered: {tool.name!r}")
    _tools[tool.name] = tool


def get(name: str) -> Optional[Tool]:
    return _tools.get(name)


def resolve(names: list[str]) -> list[Tool]:
    """Resolve a list of tool names. Raises KeyError listing any that are missing."""
    missing = [n for n in names if n not in _tools]
    if missing:
        raise KeyError(f"Tools not registered: {missing}")
    return [_tools[n] for n in names]


def all_tools() -> list[Tool]:
    return list(_tools.values())


def clear() -> None:
    """Primarily for tests."""
    _tools.clear()
