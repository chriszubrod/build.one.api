"""U-674 — an untouched cost-code pre-fill is not a reviewed code.

U-653 / U-654b pre-fill a draft line's SubCostCode (and, from text, its project) and the
form auto-saves, so the code reaches the database before anyone has looked at it.
`BillLineItem.CodingPrefillState` records `prefilled` until a person accepts or changes
the suggestion. A Bill must not enter review or complete while any line is still
`prefilled`: approval would otherwise rest on a code nobody chose.
"""
from __future__ import annotations

from typing import Sequence

from shared.lifecycle.completion_gate import UnreviewedPrefillRefused

UNREVIEWED_PREFILL_STATE = "prefilled"


def count_unreviewed_prefill(lines: Sequence[object]) -> int:
    return sum(1 for li in lines if getattr(li, "coding_prefill_state", None) == UNREVIEWED_PREFILL_STATE)


def unreviewed_prefill_message(count: int, *, action: str) -> str:
    noun = "line still carries" if count == 1 else "lines still carry"
    return (
        f"This bill cannot be {action} while {count} {noun} a suggested cost code "
        "nobody has reviewed. Accept or change each suggestion first."
    )


def assert_no_unreviewed_prefill(lines: Sequence[object], *, action: str) -> None:
    count = count_unreviewed_prefill(lines)
    if count:
        raise UnreviewedPrefillRefused(unreviewed_prefill_message(count, action=action))
