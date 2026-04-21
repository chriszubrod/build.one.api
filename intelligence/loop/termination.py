"""Termination policy for the loop.

BudgetPolicy caps a run by turns and tokens. Dollar enforcement lands with
the pricing table in L3 observability.
"""
from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class BudgetPolicy:
    max_turns: int = 20
    max_tokens: int = 200_000
    max_usd: Optional[float] = None  # not enforced until pricing table lands


TerminationReason = Literal[
    "end_turn",    # assistant concluded normally
    "stop_sequence",
    "max_turns",   # hit max_turns cap
    "max_tokens",  # hit cumulative token cap
    "error",       # transport or runtime error
]
