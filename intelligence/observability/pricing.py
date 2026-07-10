"""Per-provider per-model pricing — used to convert token counts to dollars.

Prices are dollars per 1,000,000 tokens. Update as providers publish new
rates. Models not in the table return None (no cost computed; UI just
shows tokens). Adding a new model = one entry; no other code changes.

Anthropic source: https://docs.anthropic.com/en/docs/about-claude/pricing
Last reviewed: 2026-04-23.
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


# Map of provider → model_id → pricing.
# Match the model strings used by Agent definitions.
PRICING: dict[str, dict[str, ModelPricing]] = {
    "anthropic": {
        # Sonnet 4.6
        "claude-sonnet-4-6": ModelPricing(
            input=3.00,
            output=15.00,
            cache_write=3.75,
            cache_read=0.30,
        ),
        # Opus 4.7
        "claude-opus-4-7": ModelPricing(
            input=15.00,
            output=75.00,
            cache_write=18.75,
            cache_read=1.50,
        ),
        # Haiku 4.5
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

    cost = (
        usage.input_tokens * pricing.input
        + usage.output_tokens * pricing.output
        + usage.cache_creation_input_tokens * pricing.cache_write
        + usage.cache_read_input_tokens * pricing.cache_read
    ) / 1_000_000.0
    return round(cost, 6)
