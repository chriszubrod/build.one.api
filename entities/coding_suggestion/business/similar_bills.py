"""Similar-past-bills token matching (U-691 semantics, U-701a production module).

IDF-weighted token overlap × amount band, top-K neighbour vote. Single source of truth
for scripts/u691_similar_bills_backtest.py and live SimilarBillsService.
"""
from __future__ import annotations

import heapq
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable, Mapping, NamedTuple, Optional, Sequence, TypeVar

from entities.attachment.business.extraction_envelope import extraction_payload_is_current
from intelligence.cascade.bill_coding import SUSPENSE_SCC_NUMBER, normalize_scc_number

DEFAULT_TOP_K = 10
DEFAULT_NEIGHBOUR_DISPLAY_K = 5
DEFAULT_AMOUNT_BAND_PCT = 0.25
DEFAULT_AMOUNT_BAND_CAP_RATIO = 4.0
MIN_TOKEN_LEN = 2

_STOP_WORDS = frozenset(
    "a an and are as at be by for from in is it of on or the to was with "
    "bill invoice payment total amount due date no num number ref po".split()
)

_TOKEN_RE = re.compile(r"[a-z]+")


def tokenize_bill_text(text: str) -> frozenset[str]:
    """Lowercase, strip punctuation, drop stop words and very short tokens; dedupe per bill."""
    if not text:
        return frozenset()
    tokens = _TOKEN_RE.findall(text.lower())
    out: set[str] = set()
    for tok in tokens:
        if len(tok) < MIN_TOKEN_LEN or tok in _STOP_WORDS:
            continue
        out.add(tok)
    return frozenset(out)


def _pdf_text_from_raw_blob(raw_blob_text: str) -> tuple[Optional[str], str]:
    """Parse extraction JSON once; return (text, outcome) with outcome ok|stale|empty."""
    try:
        payload = json.loads(raw_blob_text)
    except ValueError:
        return None, "stale"
    if not isinstance(payload, dict) or not extraction_payload_is_current(payload):
        return None, "stale"
    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return None, "empty"
    return content.strip(), "ok"


def bill_text_from_extraction_blob(raw_blob_text: str) -> Optional[str]:
    """Extract invoice body text from a current-stamp extraction envelope (content field only)."""
    text, outcome = _pdf_text_from_raw_blob(raw_blob_text)
    return text if outcome == "ok" else None


def _log_abs_amount(total_abs_amount: Decimal) -> float:
    if total_abs_amount <= 0:
        return float("-inf")
    return math.log(float(total_abs_amount))


def _date_only(dt: datetime) -> date:
    if isinstance(dt, datetime):
        return dt.date()
    return dt  # type: ignore[return-value]


_TToken = TypeVar("_TToken")


def cap_tokens_by_idf(
    tokens: frozenset[_TToken],
    idf: Mapping[_TToken, float],
    max_tokens: Optional[int],
) -> frozenset[_TToken]:
    """Keep up to ``max_tokens`` tokens with highest IDF (ties broken by token value). None = no cap."""
    if max_tokens is None or len(tokens) <= max_tokens:
        return tokens
    ranked = sorted(tokens, key=lambda t: (-idf.get(t, 0.0), t))
    return frozenset(ranked[:max_tokens])


def compute_idf_from_doc_token_sets(doc_tokens: Sequence[frozenset[str]]) -> dict[str, float]:
    """IDF = log((N + 1) / (df + 1)) over the supplied document token sets."""
    n_docs = len(doc_tokens)
    if n_docs == 0:
        return {}
    df: dict[str, int] = defaultdict(int)
    for toks in doc_tokens:
        for t in toks:
            df[t] += 1
    return {t: math.log((n_docs + 1) / (count + 1)) for t, count in df.items()}


def amount_band_factor(
    query_amount: Decimal,
    prior_amount: Decimal,
    *,
    band_pct: float = DEFAULT_AMOUNT_BAND_PCT,
    cap_ratio: float = DEFAULT_AMOUNT_BAND_CAP_RATIO,
) -> float:
    return amount_band_factor_logs(
        _log_abs_amount(query_amount),
        _log_abs_amount(prior_amount),
        band_pct=band_pct,
        cap_ratio=cap_ratio,
    )


def amount_band_factor_logs(
    query_log: float,
    prior_log: float,
    *,
    band_pct: float = DEFAULT_AMOUNT_BAND_PCT,
    cap_ratio: float = DEFAULT_AMOUNT_BAND_CAP_RATIO,
) -> float:
    if query_log == float("-inf") or prior_log == float("-inf"):
        return 0.0
    log_ratio = abs(query_log - prior_log)
    band_log = math.log(1.0 + band_pct)
    cap_log = math.log(cap_ratio)
    if log_ratio <= band_log:
        return 1.0
    if log_ratio >= cap_log:
        return 0.0
    return max(0.0, 1.0 - (log_ratio - band_log) / (cap_log - band_log))


def idf_weighted_jaccard(
    tokens_a: frozenset[str],
    weight_a: float,
    tokens_b: frozenset[str],
    weight_b: float,
    idf: dict[str, float],
) -> float:
    if not tokens_a or not tokens_b:
        return 0.0
    inter = tokens_a & tokens_b
    if not inter:
        return 0.0
    num = sum(idf.get(t, 0.0) for t in inter)
    den = weight_a + weight_b - num
    if den <= 0:
        return 0.0
    return num / den


@dataclass(frozen=True)
class BillTokenProfile:
    """Token profile for one bill (``enriched`` must expose a ``bill`` row)."""

    enriched: Any
    tokens: frozenset[str]
    idf_weight_sum: float
    log_abs_amount: float


def text_similarity_profiles(
    query: BillTokenProfile,
    prior: BillTokenProfile,
    idf: dict[str, float],
    *,
    band_pct: float,
    cap_ratio: float,
) -> float:
    jac = idf_weighted_jaccard(
        query.tokens,
        query.idf_weight_sum,
        prior.tokens,
        prior.idf_weight_sum,
        idf,
    )
    if jac <= 0:
        return 0.0
    band = amount_band_factor_logs(
        query.log_abs_amount,
        prior.log_abs_amount,
        band_pct=band_pct,
        cap_ratio=cap_ratio,
    )
    return jac * band


def _as_bill_row(bill_or_enriched: Any) -> Any:
    return bill_or_enriched.bill if hasattr(bill_or_enriched, "bill") else bill_or_enriched


def is_prior_eligible(
    prior: Any,
    query: Any,
    *,
    as_of: datetime,
) -> bool:
    """Strict rolling origin: created strictly before as_of; never self."""
    pb = _as_bill_row(prior)
    qb = _as_bill_row(query)
    if pb.bill_id == qb.bill_id:
        return False
    return pb.created < as_of


@dataclass(frozen=True)
class SimilarVote:
    predicted_scc_id: Optional[int]
    confidence: Optional[Decimal]
    neighbour_count: int
    top_k_used: int


def vote_from_scored_neighbours(
    scored: list[tuple[float, int, int]],
    top_k: int,
) -> SimilarVote:
    top = scored[:top_k]
    if not top:
        return SimilarVote(None, None, 0, 0)
    weights: dict[int, float] = defaultdict(float)
    for sim, _bid, scc_id in top:
        weights[scc_id] += sim
    ranked = sorted(weights.items(), key=lambda x: (-x[1], x[0]))
    top_scc, top_w = ranked[0]
    total_w = sum(weights.values())
    share = Decimal(str(top_w / total_w)) if total_w > 0 else None
    k_used = len(top)
    return SimilarVote(top_scc, share, k_used, k_used)


def _top_k_scored(
    scored: list[tuple[float, int, int]],
    top_k: int,
) -> list[tuple[float, int, int]]:
    if len(scored) <= top_k:
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored
    best = heapq.nlargest(top_k, scored, key=lambda x: (x[0], -x[1]))
    best.sort(key=lambda x: (-x[0], x[1]))
    return best


def token_match_ratio(
    query: BillTokenProfile,
    prior: BillTokenProfile,
    idf: dict[str, float],
) -> float:
    """Weighted IDF token overlap divided by the query's weighted IDF mass (0..1, 2 dp)."""
    if query.idf_weight_sum <= 0:
        return 0.0
    inter = query.tokens & prior.tokens
    if not inter:
        return 0.0
    overlap = sum(idf.get(t, 0.0) for t in inter)
    return round(overlap / query.idf_weight_sum, 2)


class Neighbour(NamedTuple):
    """Top similar prior bill (U-696 evidence shape)."""

    combined: float
    prior: BillTokenProfile
    match: float
    amount_ratio: Optional[float]
    same_vendor: bool

    @property
    def scc_id(self) -> int:
        return int(self.prior.enriched.bill.label_scc_id)


def _neighbour_amount_ratio(
    query_amount: Decimal,
    prior_amount: Decimal,
) -> Optional[float]:
    if query_amount <= 0 or prior_amount <= 0:
        return None
    return round(float(prior_amount / query_amount), 2)


@dataclass(frozen=True)
class SimilarNeighbourResult:
    scc_id: int
    match: float
    amount_ratio: Optional[float]
    same_vendor: bool
    combined: float


@dataclass(frozen=True)
class SimilarBillsMatchResult:
    neighbours: tuple[SimilarNeighbourResult, ...]
    predicted_scc_id: Optional[int]
    confidence: Optional[Decimal]


@dataclass(frozen=True)
class VisibilityFilter:
    """Scope similar-bill priors to bills the actor may see (None accessible = all)."""

    actor_user_id: Optional[int]
    accessible_project_ids: Optional[frozenset[int]]


def prior_bill_visible(
    *,
    created_by_user_id: int,
    project_ids: tuple[int, ...],
    visible: VisibilityFilter,
) -> bool:
    if visible.accessible_project_ids is None:
        return True
    if (
        visible.actor_user_id is not None
        and created_by_user_id == visible.actor_user_id
    ):
        return True
    if project_ids and visible.accessible_project_ids.intersection(project_ids):
        return True
    return False


def filtered_vote(
    neighbours: Sequence[Neighbour],
    *,
    as_of_catalog: Sequence[Mapping[str, str]],
    scc_by_id: Mapping[int, Any],
    ambiguous_numbers: frozenset[str],
    top_k: int = DEFAULT_TOP_K,
) -> tuple[Optional[int], Optional[Decimal]]:
    """Vote only over neighbours that survive apply_similar_evidence catalog filters.

    Never Suspense / out-of-catalog / ambiguous. Holding codes are excluded only through
    ``as_of_catalog``: callers must pass a catalog that already drops IsHoldingCode rows
    (the bill text-tier catalog query does). Use this - never the raw vote on
    SimilarBillsMatchResult - for anything a user sees."""
    _, _, _, filtered, _ = apply_similar_evidence(
        neighbours,
        [],
        as_of_catalog=as_of_catalog,
        scc_by_id=scc_by_id,
        ambiguous_numbers=ambiguous_numbers,
    )
    return vote_over_filtered(filtered, top_k=top_k)


def vote_over_filtered(
    filtered: Sequence[Neighbour],
    *,
    top_k: int = DEFAULT_TOP_K,
) -> tuple[Optional[int], Optional[Decimal]]:
    """The vote over neighbours ALREADY filtered by apply_similar_evidence (the second
    half of filtered_vote, for callers that also need the filtered list)."""
    if not filtered:
        return None, None
    scored = [
        (n.combined, int(n.prior.enriched.bill.bill_id), n.scc_id) for n in filtered
    ]
    vote = vote_from_scored_neighbours(scored, top_k)
    return vote.predicted_scc_id, vote.confidence


def choosable_scc_numbers(as_of_catalog: Sequence[Mapping[str, str]]) -> frozenset[str]:
    """Normalized numbers of the as-of catalog (build once per decision)."""
    return frozenset(
        normalize_scc_number(str(row["number"]))
        for row in as_of_catalog
        if row.get("number")
    )


def scc_drop_reason(
    scc_id: int,
    *,
    choosable_nums: frozenset[str],
    scc_by_id: Mapping[int, Any],
    ambiguous_numbers: frozenset[str],
) -> Optional[str]:
    """Why a code is not choosable on the bill's as-of catalog (the drop counter key), or
    None when it is: unknown / blank number / not in the catalog, ambiguous, Suspense.
    THE rule for neighbour filtering (apply_similar_evidence) and M0's candidates."""
    ref = scc_by_id.get(scc_id)
    if ref is None:
        return "neighbours_dropped_not_in_as_of_catalog"
    num = normalize_scc_number(ref.number)
    if not num:
        return "neighbours_dropped_not_in_as_of_catalog"
    if num in ambiguous_numbers:
        return "neighbours_dropped_ambiguous"
    if num == normalize_scc_number(SUSPENSE_SCC_NUMBER):
        return "neighbours_dropped_suspense"
    if num not in choosable_nums:
        return "neighbours_dropped_not_in_as_of_catalog"
    return None


def top_neighbours_any_vendor(
    query: BillTokenProfile,
    idf: dict[str, float],
    *,
    top_k: int,
    band_pct: float = DEFAULT_AMOUNT_BAND_PCT,
    cap_ratio: float = DEFAULT_AMOUNT_BAND_CAP_RATIO,
    token_index: dict[str, set[int] | tuple[int, ...]],
    prior_by_id: dict[int, BillTokenProfile],
    prior_filter: Optional[Callable[[Any], bool]] = None,
) -> list[Neighbour]:
    """Top-K earlier bills (any vendor) by combined IDF Jaccard × amount band."""
    as_of = query.enriched.bill.created
    qbill = query.enriched.bill
    candidate_ids: set[int] = set()
    for tok in query.tokens:
        for bid in token_index.get(tok, ()):
            candidate_ids.add(bid)

    scored: list[tuple[float, int, int]] = []
    for bid in candidate_ids:
        prior_prof = prior_by_id.get(bid)
        if prior_prof is None:
            continue
        pb = prior_prof.enriched.bill
        if not is_prior_eligible(pb, qbill, as_of=as_of):
            continue
        if prior_filter is not None and not prior_filter(pb):
            continue
        combined = text_similarity_profiles(
            query, prior_prof, idf, band_pct=band_pct, cap_ratio=cap_ratio
        )
        if combined <= 0:
            continue
        scored.append((combined, bid, pb.label_scc_id))

    # Display fields are pure functions of (query, prior): compute them for the top-K winners only.
    out: list[Neighbour] = []
    for combined, bid, _scc in _top_k_scored(scored, top_k):
        prior_prof = prior_by_id[bid]
        pb = prior_prof.enriched.bill
        out.append(
            Neighbour(
                combined,
                prior_prof,
                token_match_ratio(query, prior_prof, idf),
                _neighbour_amount_ratio(qbill.total_abs_amount, pb.total_abs_amount),
                pb.vendor_id == qbill.vendor_id,
            )
        )
    return out


def similar_bills_block_from_neighbours(
    neighbours: Sequence[Neighbour],
    scc_by_id: Mapping[int, Any],
) -> list[dict[str, Any]]:
    """Privacy-safe similar_bills block (no ids, dates, vendor names, or free text)."""
    out: list[dict[str, Any]] = []
    for n in neighbours:
        ref = scc_by_id.get(n.scc_id)
        if ref is None:
            continue
        out.append(
            {
                "scc_number": ref.number,
                "scc_name": ref.name,
                "match": n.match,
                "amount_ratio": n.amount_ratio,
                "same_vendor": n.same_vendor,
            }
        )
    return out


def apply_similar_evidence(
    neighbours: Sequence[Neighbour],
    candidate_sccs: list[dict[str, str]],
    *,
    as_of_catalog: Sequence[Mapping[str, str]],
    scc_by_id: Mapping[int, Any],
    ambiguous_numbers: frozenset[str],
    choosable_nums: Optional[frozenset[str]] = None,
) -> tuple[
    list[dict[str, str]],
    int,
    list[dict[str, Any]],
    list[Neighbour],
    dict[str, int],
]:
    """
    Filter neighbours to SCCs choosable on the bill's as-of catalog; union into
    candidates; build the model-facing similar_bills block from the same list.
    ``choosable_nums``: choosable_scc_numbers(as_of_catalog), when the caller built it.
    """
    if choosable_nums is None:
        choosable_nums = choosable_scc_numbers(as_of_catalog)
    filtered: list[Neighbour] = []
    drops: dict[str, int] = {
        "neighbours_dropped_not_in_as_of_catalog": 0,
        "neighbours_dropped_suspense": 0,
        "neighbours_dropped_ambiguous": 0,
    }
    for n in neighbours:
        reason = scc_drop_reason(
            n.scc_id,
            choosable_nums=choosable_nums,
            scc_by_id=scc_by_id,
            ambiguous_numbers=ambiguous_numbers,
        )
        if reason is not None:
            drops[reason] += 1
            continue
        filtered.append(n)

    existing = {normalize_scc_number(c["number"]) for c in candidate_sccs}
    added = 0
    merged = list(candidate_sccs)
    for n in filtered:
        ref = scc_by_id.get(n.scc_id)
        if ref is None:
            continue
        num = normalize_scc_number(ref.number)
        if not num or num in existing:
            continue
        merged.append({"number": ref.number, "name": ref.name})
        existing.add(num)
        added += 1

    block = similar_bills_block_from_neighbours(filtered, scc_by_id)
    return merged, added, block, filtered, drops
