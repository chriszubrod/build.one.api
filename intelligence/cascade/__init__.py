"""Model cascade — cheapest-first escalation across providers.

A `StructuredTask` is attempted against an ordered ladder of `(provider,
model)` rungs. Each rung produces a structured result + self-reported
`confidence`; an acceptance gate (deterministic validator AND confidence ≥ τ)
decides accept-or-escalate. First acceptance wins; if every rung fails the
gate, the best attempt is returned with `needs_human=True` — a failing result
is never silently accepted.

The ladder spans Foundry (DeepSeek/GPT-5.4 family) and Anthropic (Haiku/
Sonnet) transparently, so "try the cheap models, fall back to Claude" is just
the default ladder. See `core.DEFAULT_LADDER`.
"""
from intelligence.cascade.agent_cascade import run_agent_cascade
from intelligence.cascade.core import (
    Attempt,
    CascadeResult,
    DEFAULT_LADDER,
    Rung,
    StructuredTask,
    run_cascade,
    run_ladder,
)

__all__ = [
    "Attempt",
    "CascadeResult",
    "DEFAULT_LADDER",
    "Rung",
    "StructuredTask",
    "run_cascade",
    "run_ladder",
    "run_agent_cascade",
]
