"""Per-provider per-model pricing — used to convert token counts to dollars.

Prices are dollars per 1,000,000 tokens. Update as providers publish new
rates. Models not in the table return None (no cost computed; UI just
shows tokens). Adding a new model = one entry; no other code changes.

Anthropic source: https://docs.anthropic.com/en/docs/about-claude/pricing
Last reviewed: 2026-10-09.
"""
from dataclasses import dataclass
from typing import Optional

from intelligence.transport.base import Usage


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token rates."""
    input: float
    output: float
    cache_write: float   # cache_creation_input_tokens
    cache_read: float    # cache_read_input_tokens
    # Optional long-context tier, applied per request: when a request's
    # input + cache_creation + cache_read tokens exceed the threshold, every
    # rate comes from `long_context` instead.
    long_context_threshold: Optional[int] = None
    long_context: Optional["ModelPricing"] = None


# Map of provider → model_id → pricing.
# Every model id that runs (ladder rungs, non-cascade agent pins, overrides) or
# that is stored on historical AgentSession/AgentTurn rows, which replay
# re-prices. Never delete an entry.
PRICING: dict[str, dict[str, ModelPricing]] = {
    "anthropic": {
        # Sonnet 4.6 — legacy, kept for replay of historical sessions
        "claude-sonnet-4-6": ModelPricing(
            input=3.00,
            output=15.00,
            cache_write=3.75,
            cache_read=0.30,
        ),
        # Sonnet 5.5
        "claude-sonnet-5-5": ModelPricing(
            input=2.00,
            output=10.00,
            cache_write=2.50,
            cache_read=0.20,
        ),
        # Opus 4.7 — legacy, kept for replay of historical sessions
        "claude-opus-4-7": ModelPricing(
            input=15.00,
            output=75.00,
            cache_write=18.75,
            cache_read=1.50,
        ),
        # Haiku 5.5
        "claude-haiku-5-5": ModelPricing(
            input=0.10,
            output=0.50,
            cache_write=0.125,
            cache_read=0.01,
            long_context_threshold=100_000,
            long_context=ModelPricing(input=0.50, output=2.50, cache_write=0.625, cache_read=0.05),
        ),
        # Haiku 4.5 — legacy, kept for replay of historical sessions
        "claude-haiku-4-5-20251001": ModelPricing(
            input=1.00,
            output=5.00,
            cache_write=1.25,
            cache_read=0.10,
        ),
    },
    # Azure AI Foundry (confirmed 2026-06-30). cache_write is unused — the
    # Foundry transport doesn't create explicit caches (cache_creation = 0);
    # cache_read reflects Azure's prompt_tokens_details.cached_tokens.
    "foundry": {
        "DeepSeek-V4-Flash": ModelPricing(input=0.14, output=0.28, cache_write=0.14, cache_read=0.0028),
        "gpt-5.4-nano": ModelPricing(input=0.20, output=1.25, cache_write=0.20, cache_read=0.02),
        "gpt-5.4-mini": ModelPricing(input=0.75, output=4.50, cache_write=0.75, cache_read=0.075),
    },
}


def compute_cost_usd(
    *,
    provider: str,
    model: str,
    usage: Usage,
) -> Optional[float]:
    """Convert a Usage record to a dollar cost. Returns None if pricing is
    unknown for the model so the caller can degrade gracefully (show tokens,
    not $).

    Resolves by (provider, model) first; if that misses — notably when
    provider is "cascade" and `model` is the actual rung that ran — it falls
    back to searching all providers for the model.
    """
    by_model = PRICING.get(provider)
    pricing = by_model.get(model) if by_model else None
    if pricing is None:
        for prov_models in PRICING.values():
            if model in prov_models:
                pricing = prov_models[model]
                break
    if not pricing:
        return None

    if pricing.long_context is not None and pricing.long_context_threshold is not None:
        prompt_tokens = (
            usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
        )
        if prompt_tokens > pricing.long_context_threshold:
            pricing = pricing.long_context

    cost = (
        usage.input_tokens * pricing.input
        + usage.output_tokens * pricing.output
        + usage.cache_creation_input_tokens * pricing.cache_write
        + usage.cache_read_input_tokens * pricing.cache_read
    ) / 1_000_000.0
    return round(cost, 6)
