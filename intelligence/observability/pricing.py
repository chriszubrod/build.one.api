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
}


def compute_cost_usd(
    *,
    provider: str,
    model: str,
    usage: Usage,
) -> Optional[float]:
    """Convert a Usage record to a dollar cost. Returns None if pricing is
    unknown for the (provider, model) pair so the caller can degrade
    gracefully (just show tokens, not $).
    """
    by_model = PRICING.get(provider)
    if not by_model:
        return None
    pricing = by_model.get(model)
    if not pricing:
        return None

    cost = (
        usage.input_tokens * pricing.input
        + usage.output_tokens * pricing.output
        + usage.cache_creation_input_tokens * pricing.cache_write
        + usage.cache_read_input_tokens * pricing.cache_read
    ) / 1_000_000.0
    return round(cost, 6)
