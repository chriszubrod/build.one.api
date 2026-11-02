"""Suggested SubCostCodes for a Contract Labor entry (U-749).

Pure ranking over prior line counts — the live form of U-673's signal
``S0 vendor x project`` (``scripts/u673_labor_coding_backtest.py``:
``_dominant`` / ``predict_s0_vendor`` / ``predict_s0_vendor_project``).
The counts come from ``dbo.ReadContractLaborSubCostCodeCounts``, which applies
U-673's visibility filters (labelled status set, non-holding code, 2-day
coding lag, own entry excluded). This module only ranks; it does no I/O.

Rule (identical to U-673 S0 vendor x project):
  1. If the vendor's lines on THIS project total >= ``min_priors``, rank those
     counts — source ``vendor_project``.
  2. Else if the vendor's lines across all projects total >= ``min_priors``,
     rank those — source ``vendor``.
  3. Else no suggestion — source ``none``.
Ranking is count desc, then SubCostCodeId asc; the top ``top_n`` ids are kept.
``prior_count`` is the total line count of the scope that produced the ranking
(internal only — the API does not serialize it).

Deliberate departures from U-673 S0 (``prior_is_visible``):
  (a) ``coded_at`` ordering is NOT ported. U-673 also hides a prior whose
      approval time is at/after the query line's; a live entry being edited
      has no coded_at, so only the 2-day lag and own-entry exclusion apply.
  (b) The as-of date is the ENTRY's ``WorkDate``, applied to every line of the
      entry, not each line's own ``LineDate`` as in the backtest (the sproc
      still dates PRIOR lines by ``COALESCE(LineDate, WorkDate)``).

The vendor-wide fallback counts the vendor's lines on ALL projects (the S0
design), so it can suggest a code first used on another project. Only
SubCostCode ids from the catalog are surfaced — never line descriptions,
notes, or other projects' identities.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

# Mirrors scripts.u653b_backtest.MIN_PRIOR_BILLS (3) as used by U-673's _dominant.
MIN_PRIORS = 3
TOP_N = 3

SOURCE_VENDOR_PROJECT = "vendor_project"
SOURCE_VENDOR = "vendor"
SOURCE_NONE = "none"


@dataclass(frozen=True)
class CodeSuggestion:
    sub_cost_code_ids: tuple[int, ...]
    source: str
    prior_count: int


NO_SUGGESTION = CodeSuggestion(sub_cost_code_ids=(), source=SOURCE_NONE, prior_count=0)


def rank_sub_cost_codes(
    project_counts: Mapping[int, int],
    vendor_counts: Mapping[int, int],
    *,
    min_priors: int = MIN_PRIORS,
    top_n: int = TOP_N,
) -> CodeSuggestion:
    """Rank SubCostCode ids by prior usage (U-673 S0 vendor x project)."""
    for counts, source in (
        (project_counts, SOURCE_VENDOR_PROJECT),
        (vendor_counts, SOURCE_VENDOR),
    ):
        positive = [(scc_id, n) for scc_id, n in counts.items() if n > 0]
        total = sum(n for _, n in positive)
        if total >= min_priors:
            positive.sort(key=lambda item: (-item[1], item[0]))
            return CodeSuggestion(
                sub_cost_code_ids=tuple(scc_id for scc_id, _ in positive[:top_n]),
                source=source,
                prior_count=total,
            )
    return NO_SUGGESTION
