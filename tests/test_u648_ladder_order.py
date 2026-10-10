"""U-648 — the cascade's DEFAULT_LADDER is cheapest-first by blended $/MTok.

Blended rate = (input + output) / 2 per MTok, read straight from PRICING under
each rung's declared provider, so the order is pinned to the table: a price edit
that breaks it fails here instead of silently leaving production mis-routed.
"""

from intelligence.cascade.core import DEFAULT_LADDER
from intelligence.observability.pricing import PRICING


def _blended_per_mtok(rung) -> float:
    rates = PRICING[rung.provider][rung.model]
    return (rates.input + rates.output) / 2


def test_default_ladder_exact_order():
    # Deliberately a snapshot: the reviewed order AND membership, so a rung added
    # or dropped fails here even when the cost ordering below still holds.
    assert [(r.provider, r.model) for r in DEFAULT_LADDER] == [
        ("foundry", "DeepSeek-V4-Flash"),
        ("anthropic", "claude-haiku-5-5"),
        ("foundry", "gpt-5.4-nano"),
        ("foundry", "gpt-5.4-mini"),
        ("anthropic", "claude-sonnet-5-5"),
    ]


def test_every_default_rung_is_priced_under_its_declared_provider():
    # Direct lookup, not compute_cost_usd: its cross-provider fallback would let
    # a rung with the wrong provider label pass.
    for r in DEFAULT_LADDER:
        assert r.model in PRICING[r.provider], r


def test_default_ladder_is_cheapest_first_by_blended_rate():
    # A PRICING edit that breaks this order must come with a DEFAULT_LADDER reorder.
    blended = {(r.provider, r.model): _blended_per_mtok(r) for r in DEFAULT_LADDER}
    rates = list(blended.values())
    assert all(later > earlier for earlier, later in zip(rates, rates[1:])), blended
