"""U-698 decision model — label, as-of priors, candidate set and per-candidate features.

Pure; no I/O. Leakage rules (``docs/design/u698/DESIGN.md`` §2.2) are enforced HERE, not by callers:

- every prior read goes through :class:`AsOfIndex`, which only ever returns bills created
  STRICTLY before the query's ``as_of`` (equal timestamps excluded; the bill itself excluded);
- :func:`build_context` refuses neighbour evidence that is not strictly earlier;
- candidate codes pass the caller-supplied as-of catalog predicate (``choosable``).

No coding-time text (line descriptions, memo) is ever an input: features are counts, shares,
amounts, neighbour scores and (for M1) LLM codes/confidences.
"""
from __future__ import annotations

import math
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable, Iterable, Mapping, Optional, Sequence

# Mirrors scripts/u653b_backtest.MIN_PRIOR_BILLS and bill_suggestion_service.HISTORY_STRONG_RAW_SCORE
# (parity pinned by tests/test_u698_decision_model.py; this package must not import scripts/).
MIN_PRIOR_BILLS = 3
HISTORY_STRONG_RAW_SCORE = 0.60
VOTE_TOP_K = 10
HISTORY_TOP_N = 3
VOTE_TOP_N = 3

PRIOR_CUTOFF_CREATED = "created"
PRIOR_CUTOFF_CODED = "coded"
UNKNOWN_CODED_ADMIT = "admit"
UNKNOWN_CODED_DROP = "drop"


class LeakageError(AssertionError):
    """A feature input was not strictly earlier than the bill being scored."""


# ---------------------------------------------------------------------------------------------
# Label
# ---------------------------------------------------------------------------------------------


def dominant_label(lines: Sequence[tuple[int, int, object]]) -> int:
    """Dominant SCC by ABSOLUTE line amount; ties go to the SCC holding the lowest line id.

    ``lines``: (line_id, scc_id, amount) for coded lines only. Same rule as the U-653b load SQL.
    """
    if not lines:
        raise ValueError("no coded lines")
    agg: dict[int, list] = {}
    for line_id, scc_id, amount in lines:
        amt = abs(Decimal(str(amount if amount is not None else 0)))
        cur = agg.get(scc_id)
        if cur is None:
            agg[scc_id] = [amt, line_id]
        else:
            cur[0] += amt
            cur[1] = min(cur[1], line_id)
    return min(agg.items(), key=lambda kv: (-kv[1][0], kv[1][1]))[0]


def line1_label(lines: Sequence[tuple[int, int, object]]) -> int:
    """The SCC of the lowest-id coded line (reported on the mixed-code stratum only)."""
    if not lines:
        raise ValueError("no coded lines")
    return min(lines, key=lambda ln: ln[0])[1]


# ---------------------------------------------------------------------------------------------
# As-of prior index
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PriorBill:
    bill_id: int
    vendor_id: int
    created: datetime
    label_scc_id: int
    project_id: Optional[int]
    amount: float
    has_pdf: bool = False
    coded_at: Optional[datetime] = None


class AsOfIndex:
    """Strictly-earlier lookups over the labelled population.

    ``cutoff="created"`` (default, the design's rule): a prior counts when ``prior.created <
    as_of``. ``cutoff="coded"`` (Q4 rerun): a prior with a KNOWN coded time must ALSO have been
    coded before ``as_of``; one with an unknown coded time is admitted (``unknown="admit"``,
    default) or dropped (``unknown="drop"``, the sensitivity arm).
    """

    def __init__(
        self,
        bills: Iterable[PriorBill],
        *,
        cutoff: str = PRIOR_CUTOFF_CREATED,
        unknown: str = UNKNOWN_CODED_ADMIT,
    ) -> None:
        if cutoff not in (PRIOR_CUTOFF_CREATED, PRIOR_CUTOFF_CODED):
            raise ValueError(f"unknown cutoff {cutoff!r}")
        if unknown not in (UNKNOWN_CODED_ADMIT, UNKNOWN_CODED_DROP):
            raise ValueError(f"unknown policy {unknown!r}")
        self.cutoff = cutoff
        self.unknown = unknown
        ordered = sorted(bills, key=lambda b: (b.created, b.bill_id))
        self._by_vendor: dict[int, list[PriorBill]] = defaultdict(list)
        self._by_scc_created: dict[int, list[datetime]] = defaultdict(list)
        self._all_created: list[datetime] = []
        for b in ordered:
            self._by_vendor[b.vendor_id].append(b)
            self._by_scc_created[b.label_scc_id].append(b.created)
            self._all_created.append(b.created)
        self._vendor_keys = {v: [b.created for b in lst] for v, lst in self._by_vendor.items()}

    def _admit(self, b: PriorBill, as_of: datetime, exclude_bill_id: Optional[int]) -> bool:
        # The strictly-earlier cut itself is the bisect_left in vendor_priors (the ONE guard).
        if b.bill_id == exclude_bill_id:
            return False
        if self.cutoff == PRIOR_CUTOFF_CODED:
            if b.coded_at is None:
                return self.unknown == UNKNOWN_CODED_ADMIT
            return b.coded_at < as_of
        return True

    def vendor_priors(
        self, vendor_id: int, *, as_of: datetime, exclude_bill_id: Optional[int] = None
    ) -> list[PriorBill]:
        lst = self._by_vendor.get(vendor_id, [])
        k = bisect_left(self._vendor_keys.get(vendor_id, []), as_of)
        return [b for b in lst[:k] if self._admit(b, as_of, exclude_bill_id)]

    def base_rate(self, scc_id: int, *, as_of: datetime) -> float:
        """Share of all strictly-earlier bills labelled ``scc_id`` (created-time cut)."""
        n_all = bisect_left(self._all_created, as_of)
        if n_all == 0:
            return 0.0
        return bisect_left(self._by_scc_created.get(scc_id, []), as_of) / n_all


# ---------------------------------------------------------------------------------------------
# History votes (U-653b semantics, re-expressed over PriorBill)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryCounts:
    counts: Mapping[int, int]
    total: int
    ranked: tuple[int, ...]

    @property
    def available(self) -> bool:
        return self.total >= MIN_PRIOR_BILLS and bool(self.ranked)

    @property
    def top(self) -> Optional[int]:
        return self.ranked[0] if self.available else None

    def count(self, scc_id: int) -> int:
        return int(self.counts.get(scc_id, 0))

    def share(self, scc_id: int) -> float:
        return self.count(scc_id) / (self.total + 2)

    @property
    def top_share(self) -> float:
        return self.share(self.ranked[0]) if self.ranked else 0.0

    @property
    def second_share(self) -> float:
        return self.share(self.ranked[1]) if len(self.ranked) > 1 else 0.0

    @property
    def strong(self) -> bool:
        return self.available and self.top_share >= HISTORY_STRONG_RAW_SCORE

    @property
    def consistency(self) -> Optional[float]:
        """Unsmoothed top/total (U-653b ``_vendor_consistency_share``)."""
        if self.total == 0:
            return None
        return self.count(self.ranked[0]) / self.total


EMPTY_HISTORY = HistoryCounts({}, 0, ())


def history_counts(priors: Sequence[PriorBill], *, project_id: Optional[int] = None) -> HistoryCounts:
    rows = [
        p for p in priors
        if project_id is None or (p.project_id is not None and p.project_id == project_id)
    ]
    if not rows:
        return EMPTY_HISTORY
    counts: dict[int, int] = defaultdict(int)
    for p in rows:
        counts[p.label_scc_id] += 1
    ranked = tuple(c for c, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return HistoryCounts(dict(counts), len(rows), ranked)


def top_project(priors: Sequence[PriorBill]) -> Optional[int]:
    rows = [p for p in priors if p.project_id is not None]
    if len(rows) < MIN_PRIOR_BILLS:
        return None
    counts: dict[int, int] = defaultdict(int)
    for p in rows:
        counts[int(p.project_id)] += 1
    return min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def history_scope(
    priors: Sequence[PriorBill], *, mode: str, project_id: Optional[int]
) -> tuple[HistoryCounts, bool]:
    """The history the ladder reads (U-653b ``_predict``): project-scoped when the project is
    given (Mode A) or predicted from the vendor's dominant project, else vendor-wide.

    Returns (counts, scoped_to_project)."""
    lookup = project_id if mode == "A" else None
    if lookup is None:
        lookup = top_project(priors)
    if lookup is not None:
        scoped = history_counts(priors, project_id=lookup)
        if scoped.available:
            return scoped, True
    return history_counts(priors), False


# ---------------------------------------------------------------------------------------------
# Neighbours, LLM answers, context
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NeighbourEvidence:
    combined: float
    match: float
    scc_id: int
    same_vendor: bool
    prior_bill_id: int
    prior_created: datetime


@dataclass(frozen=True)
class LlmAnswers:
    haiku_scc_id: Optional[int] = None
    haiku_conf: Optional[float] = None
    sonnet_scc_id: Optional[int] = None
    sonnet_conf: Optional[float] = None


NO_LLM = LlmAnswers()


@dataclass(frozen=True)
class QueryBill:
    bill_id: int
    vendor_id: int
    created: datetime
    amount: float
    project_id: Optional[int]  # the given project in Mode A (the bill's labelled project)
    has_text: bool = False
    pdf_token_count: int = 0


@dataclass(frozen=True)
class BillContext:
    bill: QueryBill
    mode: str
    history: HistoryCounts
    scoped_to_project: bool
    vendor_wide: HistoryCounts
    vendor_prior_count: int
    vendor_pdf_prior_count: int
    neighbours: tuple[NeighbourEvidence, ...]
    vote_weights: Mapping[int, float]
    vote_ranked: tuple[int, ...]
    amount_median_by_scc: Mapping[int, float]
    base_rate_by_scc: Mapping[int, float]
    llm: LlmAnswers
    candidates: tuple[int, ...]

    @property
    def vote_top(self) -> Optional[int]:
        return self.vote_ranked[0] if self.vote_ranked else None

    @property
    def baseline_pick(self) -> Optional[int]:
        """history-if-strong-else-filtered-vote (else weak history), the free-path baseline."""
        if self.history.strong:
            return self.history.top
        if self.vote_top is not None:
            return self.vote_top
        return self.history.top


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def build_context(
    index: AsOfIndex,
    bill: QueryBill,
    *,
    mode: str,
    neighbours: Sequence[NeighbourEvidence] = (),
    choosable: Callable[[int], bool] = lambda _c: True,
    llm: LlmAnswers = NO_LLM,
) -> BillContext:
    if mode not in ("A", "B"):
        raise ValueError(f"mode must be A or B, got {mode!r}")
    as_of = bill.created
    for n in neighbours:
        if not n.prior_created < as_of or n.prior_bill_id == bill.bill_id:
            raise LeakageError(f"neighbour {n.prior_bill_id} is not strictly earlier than {bill.bill_id}")
    priors = index.vendor_priors(bill.vendor_id, as_of=as_of, exclude_bill_id=bill.bill_id)
    history, scoped = history_scope(priors, mode=mode, project_id=bill.project_id)
    vendor_wide = history_counts(priors)

    nbrs = tuple(n for n in neighbours if choosable(n.scc_id))[:VOTE_TOP_K]
    weights: dict[int, float] = defaultdict(float)
    for n in nbrs:
        weights[n.scc_id] += n.combined
    vote_ranked = tuple(c for c, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])))

    cands: list[int] = []
    pool = (
        list(history.ranked[:HISTORY_TOP_N] if history.available else ())
        + list(vote_ranked[:VOTE_TOP_N])
        + [llm.haiku_scc_id, llm.sonnet_scc_id]
    )
    for c in pool:
        if c is None or c in cands or not choosable(c):
            continue
        cands.append(int(c))

    amounts: dict[int, list[float]] = defaultdict(list)
    for p in priors:
        if p.label_scc_id in cands and p.amount > 0:
            amounts[p.label_scc_id].append(p.amount)
    return BillContext(
        bill=bill,
        mode=mode,
        history=history,
        scoped_to_project=scoped,
        vendor_wide=vendor_wide,
        vendor_prior_count=len(priors),
        vendor_pdf_prior_count=sum(1 for p in priors if p.has_pdf),
        neighbours=nbrs,
        vote_weights=dict(weights),
        vote_ranked=vote_ranked,
        amount_median_by_scc={c: _median(v) for c, v in amounts.items()},
        base_rate_by_scc={c: index.base_rate(c, as_of=as_of) for c in cands},
        llm=llm,
        candidates=tuple(cands),
    )


# ---------------------------------------------------------------------------------------------
# Feature vectors
# ---------------------------------------------------------------------------------------------

M0_FEATURES: tuple[str, ...] = (
    "F1_hist_rank1",
    "F1_hist_rank2",
    "F1_hist_rank3",
    "F2_hist_share",
    "F3_hist_log_total",
    "F4_hist_margin",
    "F5_vendor_consistency",
    "F5_vendor_lt50",
    "F6_proj_over_vendor",
    "F7_vote_share",
    "F8_best_combined",
    "F8_best_match",
    "F9_nbr_count_c",
    "F9_same_vendor_share_c",
    "F10_has_text",
    "F10_nbr_count",
    "F10_top_combined",
    "F11_hist_and_vote_top",
    "F11_n_sources",
    "F12_cold_vendor",
    "F12_cold_vendor_pdf",
    "F13_log_amount",
    "F13_amount_ratio",
    "F13_has_amount_prior",
    "F14_mode_a",
    "F14_pdf_len",
    "F15_base_rate",
    "X_share_x_lt50",
    "X_vote_x_hist_disagree",
)


def m0_features(ctx: BillContext, c: int) -> list[float]:
    h = ctx.history
    rank = list(h.ranked[:HISTORY_TOP_N]).index(c) + 1 if h.available and c in h.ranked[:HISTORY_TOP_N] else 0
    hist_share = h.share(c) if h.available else 0.0
    cons = ctx.vendor_wide.consistency
    lt50 = 1.0 if (cons is not None and cons < 0.5 and ctx.vendor_wide.total >= MIN_PRIOR_BILLS) else 0.0
    vw_share = ctx.vendor_wide.share(c)
    proj_over = 0.0
    if ctx.mode == "A" and ctx.scoped_to_project and vw_share > 0:
        proj_over = min(h.share(c) / vw_share, 5.0)
    total_w = sum(ctx.vote_weights.values())
    vote_share = ctx.vote_weights.get(c, 0.0) / total_w if total_w > 0 else 0.0
    nbr_c = [n for n in ctx.neighbours if n.scc_id == c]
    best_comb = max((n.combined for n in nbr_c), default=0.0)
    best_match = max((n.match for n in nbr_c), default=0.0)
    same_vendor_share = (sum(1 for n in nbr_c if n.same_vendor) / len(nbr_c)) if nbr_c else 0.0
    hist_top1 = 1.0 if h.top == c else 0.0
    vote_top1 = 1.0 if ctx.vote_top == c else 0.0
    med = ctx.amount_median_by_scc.get(c)
    amt = max(ctx.bill.amount, 0.0)
    if med and med > 0 and amt > 0:
        amount_ratio = max(-3.0, min(3.0, math.log(amt / med)))
        has_amount_prior = 1.0
    else:
        amount_ratio, has_amount_prior = 0.0, 0.0
    disagree = 1.0 if (h.top is not None and ctx.vote_top is not None and h.top != ctx.vote_top) else 0.0
    return [
        1.0 if rank == 1 else 0.0,
        1.0 if rank == 2 else 0.0,
        1.0 if rank == 3 else 0.0,
        hist_share,
        math.log1p(h.total),
        (h.top_share - h.second_share) if h.available else 0.0,
        cons if cons is not None else 0.0,
        lt50,
        proj_over,
        vote_share,
        best_comb,
        best_match,
        len(nbr_c) / VOTE_TOP_K,
        same_vendor_share,
        1.0 if ctx.bill.has_text else 0.0,
        len(ctx.neighbours) / VOTE_TOP_K,
        ctx.neighbours[0].combined if ctx.neighbours else 0.0,
        hist_top1 * vote_top1,
        hist_top1 + vote_top1,
        1.0 if ctx.vendor_prior_count == 0 else 0.0,
        1.0 if ctx.vendor_pdf_prior_count == 0 else 0.0,
        math.log1p(amt),
        amount_ratio,
        has_amount_prior,
        1.0 if ctx.mode == "A" else 0.0,
        math.log1p(ctx.bill.pdf_token_count),
        ctx.base_rate_by_scc.get(c, 0.0),
        hist_share * lt50,
        vote_share * disagree,
    ]


M1_FEATURES: tuple[str, ...] = (
    "M0_logit",
    "L1_is_haiku",
    "L1_haiku_conf_c",
    "L2_is_sonnet",
    "L2_sonnet_conf_c",
    "L_n_sources",
)
# Every M1 weight must be >= 0 (design §3.1: sign-constrained).
M1_SIGNS: tuple[int, ...] = (1, 1, 1, 1, 1, 1)


def _logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def m1_features(ctx: BillContext, c: int, m0_raw_prob: float) -> list[float]:
    """``m0_raw_prob`` is M0's RAW (uncalibrated) probability for ``c``: an isotonic step would
    tie candidates inside one block and erase M0's ordering. Only M1's output is calibrated."""
    llm = ctx.llm
    is_h = 1.0 if llm.haiku_scc_id is not None and llm.haiku_scc_id == c else 0.0
    is_s = 1.0 if llm.sonnet_scc_id is not None and llm.sonnet_scc_id == c else 0.0
    n_src = (
        (1.0 if ctx.history.top == c else 0.0)
        + (1.0 if ctx.vote_top == c else 0.0)
        + is_h
        + is_s
    )
    return [
        _logit(m0_raw_prob),
        is_h,
        is_h * float(llm.haiku_conf or 0.0),
        is_s,
        is_s * float(llm.sonnet_conf or 0.0),
        n_src,
    ]
