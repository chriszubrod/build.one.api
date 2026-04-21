"""Transport registry — name → Transport factory.

Phase 1 holds a single entry. The indirection exists so agents can reference
providers by string (in their Agent definition) rather than importing a class.
"""
from typing import Callable

from intelligence.transport.anthropic import AnthropicTransport
from intelligence.transport.base import Transport


_providers: dict[str, Callable[[], Transport]] = {
    "anthropic": AnthropicTransport,
}


def get_transport(provider: str) -> Transport:
    factory = _providers.get(provider)
    if factory is None:
        raise ValueError(
            f"Unknown provider {provider!r}. Registered: {sorted(_providers)}"
        )
    return factory()


def register(name: str, factory: Callable[[], Transport]) -> None:
    _providers[name] = factory
