"""Contract Labor coding-shadow scorecard: pure builder (U-797a). No DB, no I/O.

Input rows (one per done suggestion, from ReadContractLaborCodingScorecardRows), as dicts:
  work_date, decision_source, sub_cost_code_id, confidence, may_split, final_code_ids
final_code_ids is the tuple of distinct final line codes on that (CL, project), or None when the
CL is not yet in a labelled status (unfinalized: excluded from every rate).

Definitions:
  finalized      final_code_ids is non-empty.
  suggested      finalized AND a suggestion exists.
  exact          single-coded day (one final code) and the suggestion equals that code.
                 A split day is never exact.
  confident-wrong a suggested, not exact row with Confidence >= 0.70.
  split flag     flagged = may_split; a split day = final set >= 2 codes. Precision and recall
                 are over all finalized days (a flag counts with or without a suggestion).
  ship bar       >= 40 scored (suggested, finalized) days AND exact >= 64 %, integer-exact.
Wilson 95 % intervals come from the bill scorecard's wilson_interval (reused by import).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional

from entities.bill_intake_prediction.business.coding_scorecard import wilson_interval

CONFIDENT_THRESHOLD = Decimal("0.70")
SHIP_MIN_SCORED = 40
SHIP_MIN_EXACT_PCT = 64  # integer percent, compared without floats
SOURCES = ("history", "model", "none")
# Availability failures (the model or Claude was down): not model answers, so they leave every rate.
AVAILABILITY_FAILURES = frozenset({"model_unavailable", "claude_unavailable"})


def _rate(k: int, n: int) -> Optional[float]:
    return round(k / n, 4) if n else None


def _pct(k: int, n: int) -> Optional[float]:
    return round(k * 100 / n, 1) if n else None


def _ci(k: int, n: int) -> Optional[list[float]]:
    interval = wilson_interval(k, n)
    if interval is None:
        return None
    return [round(interval[0], 4), round(interval[1], 4)]


def _block(correct: int, scored: int) -> dict[str, Any]:
    return {
        "correct": correct,
        "scored": scored,
        "pct": _pct(correct, scored),
        "ci95": _ci(correct, scored),
    }


def _is_exact(suggestion: int, final: frozenset[int]) -> bool:
    return len(final) == 1 and suggestion in final


def _score(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Exact and confident-wrong over the suggested, finalized rows in `rows`."""
    scored = correct = confident_wrong = 0
    for row in rows:
        final = frozenset(row["final_code_ids"])
        exact = _is_exact(row["sub_cost_code_id"], final)
        scored += 1
        if exact:
            correct += 1
        elif row.get("confidence") is not None and Decimal(str(row["confidence"])) >= CONFIDENT_THRESHOLD:
            confident_wrong += 1
    return {
        "exact": _block(correct, scored),
        "confident_wrong": {
            "count": confident_wrong,
            "scored": scored,
            "pct": _pct(confident_wrong, scored),
            "ci95": _ci(confident_wrong, scored),
        },
    }


def build_cl_coding_scorecard(
    rows: Iterable[dict[str, Any]], *, since: Optional[date] = None
) -> dict[str, Any]:
    selected = [
        r
        for r in rows
        if r.get("failure_reason") not in AVAILABILITY_FAILURES
        and (since is None or (r.get("work_date") is not None and r["work_date"] >= since))
    ]
    finalized = [r for r in selected if r.get("final_code_ids")]
    suggested = [r for r in finalized if r.get("sub_cost_code_id") is not None]

    # Split-flag precision and recall over every finalized day.
    flagged = [r for r in finalized if r.get("may_split")]
    split_days = [r for r in finalized if len(set(r["final_code_ids"])) >= 2]
    flagged_and_split = [r for r in flagged if len(set(r["final_code_ids"])) >= 2]

    overall = _score(suggested)
    scored_total = overall["exact"]["scored"]
    correct_total = overall["exact"]["correct"]
    ship_met = scored_total >= SHIP_MIN_SCORED and correct_total * 100 >= SHIP_MIN_EXACT_PCT * scored_total

    by_source: dict[str, Any] = {}
    for source in SOURCES:
        subset = [r for r in suggested if r.get("decision_source") == source]
        by_source[source] = {
            "suggested": len(subset),
            **_score(subset),
        }

    return {
        "population": {
            "crew_days": len(selected),
            "finalized": len(finalized),
            "unfinalized": len(selected) - len(finalized),
        },
        "coverage": {
            "suggested": len(suggested),
            "finalized": len(finalized),
            "rate": _rate(len(suggested), len(finalized)),
        },
        "exact": overall["exact"],
        "confident_wrong": overall["confident_wrong"],
        "split_flag": {
            "flagged": len(flagged),
            "flagged_and_split": len(flagged_and_split),
            "split_days": len(split_days),
            "precision": _rate(len(flagged_and_split), len(flagged)),
            "recall": _rate(len(flagged_and_split), len(split_days)),
        },
        "by_source": by_source,
        "ship_bar": {
            "scored": scored_total,
            "exact_pct": _pct(correct_total, scored_total),
            "min_scored": SHIP_MIN_SCORED,
            "min_exact": SHIP_MIN_EXACT_PCT / 100,
            "met": ship_met,
        },
    }
