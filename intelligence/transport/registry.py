"""Transport registry — name → Transport factory.

The indirection exists so agents (and the model cascade) can reference
providers by string (in their Agent definition / ladder rung) rather than
importing a class.
"""
from typing import Callable

from intelligence.transport.anthropic import AnthropicTransport
from intelligence.transport.base import Transport
from intelligence.transport.foundry import FoundryTransport


def _cascade_factory() -> Transport:
    # Deferred import: CascadeTransport -> cascade.core -> this registry, so a
    # top-level import here would be circular. Building it lazily is fine.
    from intelligence.transport.cascade import CascadeTransport
    return CascadeTransport()


_providers: dict[str, Callable[[], Transport]] = {
    "anthropic": AnthropicTransport,
    # Azure AI Foundry — OpenAI-compatible surface for DeepSeek + GPT-5.4 family.
    "foundry": FoundryTransport,
    # Cheapest-first meta-transport (per-turn fallback over the default ladder).
    # run_agent builds a per-agent-laddered instance directly; this factory
    # serves get_transport("cascade") with the default ladder.
    "cascade": _cascade_factory,
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
