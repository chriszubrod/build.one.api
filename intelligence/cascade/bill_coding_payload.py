"""Pure bill-coding cascade payload builders (U-654a backtest + U-654b runtime)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from intelligence.cascade.bill_coding import (
    SUSPENSE_SCC_NUMBER,
    normalize_project_abbreviation,
    normalize_scc_number,
    truncate_bill_text,
)
from intelligence.cascade.core import CascadeResult


@dataclass(frozen=True)
class PriorBillVote:
    bill_id: int
    created: datetime
    dominant_scc_id: int
    dominant_project_id: Optional[int]


def build_history_evidence(
    priors: Sequence[PriorBillVote],
    scc_by_id: dict[int, Any],
    *,
    project_id: Optional[int] = None,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    counts: dict[int, int] = defaultdict(int)
    filtered = [
        p
        for p in priors
        if project_id is None
        or (p.dominant_project_id is not None and p.dominant_project_id == project_id)
    ]
    for p in filtered:
        counts[p.dominant_scc_id] += 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:top_n]
    evidence: list[dict[str, Any]] = []
    for scc_id, cnt in ranked:
        ref = scc_by_id.get(scc_id)
        if ref is None:
            continue
        evidence.append(
            {"scc_number": ref.number, "scc_name": ref.name, "count": cnt}
        )
    return evidence


def build_candidate_sccs_for_bill(
    scc_by_id: dict[int, Any],
    *,
    as_of: datetime,
    ambiguous_numbers: frozenset[str],
) -> list[dict[str, str]]:
    # Catalog timestamps are naive UTC (DB DATETIME2). Normalise an aware cutoff instead of
    # letting the comparison raise TypeError (U-676g: the drop path passed an aware now()).
    if as_of.tzinfo is not None:
        as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)
    suspense_norm = normalize_scc_number(SUSPENSE_SCC_NUMBER)
    out: list[dict[str, str]] = []
    for ref in sorted(scc_by_id.values(), key=lambda r: r.number):
        if ref.created >= as_of:
            continue
        if ref.number in ambiguous_numbers or ref.number == suspense_norm:
            continue
        out.append({"number": ref.number, "name": ref.name})
    return out


def narrow_candidate_sccs(
    full_candidates: Sequence[dict[str, str]],
    vendor_history_scc_numbers: Sequence[str],
    *,
    min_history: int = 3,
) -> tuple[list[dict[str, str]], bool]:
    """Shrink the SCC catalog slice using vendor history + sibling families (U-676o)."""
    full_norm_numbers = {
        normalize_scc_number(row["number"]) for row in full_candidates
    }
    distinct_in_full: set[str] = set()
    families: set[str] = set()
    for raw in vendor_history_scc_numbers:
        norm = normalize_scc_number(raw)
        if not norm or norm not in full_norm_numbers:
            continue
        distinct_in_full.add(norm)
        families.add(norm.partition(".")[0])
    if len(distinct_in_full) < min_history:
        return list(full_candidates), False

    out: list[dict[str, str]] = []
    for row in full_candidates:
        norm = normalize_scc_number(row["number"])
        if norm.partition(".")[0] in families:
            out.append(row)
    if not out:
        return list(full_candidates), False
    return out, True


def vendor_history_scc_numbers_from_priors(
    priors: Sequence[PriorBillVote],
    scc_by_id: dict[int, Any],
) -> list[str]:
    """Distinct SCC numbers from prior bills (order not significant for narrowing)."""
    out: set[str] = set()
    for p in priors:
        ref = scc_by_id.get(p.dominant_scc_id)
        if ref is None:
            continue
        num = str(ref.number)
        if normalize_scc_number(num):
            out.add(num)
    return list(out)


def build_candidate_projects_for_bill(
    projects: list[dict[str, str]],
    *,
    ambiguous_abbrevs: frozenset[str],
) -> list[dict[str, str]]:
    return [
        row
        for row in projects
        if normalize_project_abbreviation(row["abbreviation"]) not in ambiguous_abbrevs
    ]


def build_payload(
    bill: Any,
    *,
    bill_text: str,
    vendor_name: str,
    mode: str,
    priors: Sequence[PriorBillVote],
    scc_by_id: dict[int, Any],
    candidate_sccs: list[dict[str, str]],
    candidate_projects: Optional[list[dict[str, str]]],
) -> dict[str, Any]:
    project_id = bill.label_project_id if mode == "A" else None
    return {
        "vendor_name": vendor_name,
        "bill_text": truncate_bill_text(bill_text),
        "history_evidence": build_history_evidence(
            priors, scc_by_id, project_id=project_id
        ),
        "candidate_sccs": candidate_sccs,
        "candidate_projects": candidate_projects,
    }


def resolve_text_prediction(
    cascade: CascadeResult,
    *,
    scc_number_to_id: dict[str, int],
    proj_abbrev_to_id: dict[str, int],
) -> tuple[Optional[int], Optional[int]]:
    if not cascade.accepted or cascade.result is None:
        return None, None
    res = cascade.result
    scc_num = res.get("scc_number")
    scc_id = None
    if isinstance(scc_num, str):
        scc_id = scc_number_to_id.get(normalize_scc_number(scc_num))
    proj = res.get("project_abbreviation")
    proj_id = None
    if isinstance(proj, str) and normalize_project_abbreviation(proj):
        proj_id = proj_abbrev_to_id.get(normalize_project_abbreviation(proj))
    return scc_id, proj_id


def history_evidence_from_scc_counts(
    rows: Sequence[Any],
) -> list[dict[str, Any]]:
    """Map ReadVendorSccCountsForBills rows to cascade history_evidence shape."""
    out: list[dict[str, Any]] = []
    for row in rows:
        number = getattr(row, "Number", None)
        name = getattr(row, "Name", None)
        count = getattr(row, "VoteCount", None)
        if number is None or count is None:
            continue
        out.append(
            {
                "scc_number": str(number),
                "scc_name": str(name or ""),
                "count": int(count),
            }
        )
    return out
