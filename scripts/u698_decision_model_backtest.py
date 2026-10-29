"""
U-698 phase 2: read-only BACKTEST of the trained bill-coding decision model (design:
``docs/design/u698/DESIGN.md``). Nothing here touches the live path.

Pipeline (all priors strictly earlier than the bill scored; outputs are ids / codes / numbers):

1. Load the coded population with the U-653b SQL (``load_bills_from_db``: dominant-by-amount label,
   no description / memo columns), plus line-1 SCC + coded time (Q4) and the SCC catalog (number,
   created, holding flag - never names). PDF tokens come from the U-691 blob reader.
2. Word neighbours: ``u691.collect_top_neighbours_for_sample`` (rolling, ``is_prior_eligible``),
   top 10 raw then the as-of catalog filter (live ``filtered_vote`` semantics). IDF is fit on the
   calibration window (fixed split) or on the dates before each CV fold (rule 4).
3. M0 (non-LLM ranker, ``decision_model.features.M0_FEATURES``): per-candidate L2 logistic
   regression, trained on the first 80 % of each training window's dates; isotonic/Platt
   calibration on the last 20 %; scored on the held-out window.
   - fixed split: train on the calibration window, score the evaluation window;
   - expanding monthly CV (primary): six folds anchored at ``--eval-since``.
   Two training variants (Q3): ``pdf_only`` and ``pdf_plus_history`` (history-only rows carry
   F10_has_text = 0). The variant with the better pooled CV top-1 is kept and named in the report.
4. M1 (LLM stacker, 6 sign-constrained weights) on the U-701b E1 bills: Haiku / Sonnet codes and
   confidences from the committed CSV ($0 spend), 5-fold time-split cross-fitting, out-of-fold
   only. Mode-A strong-history bills are decided by M0 alone (live never calls the LLM there).
5. Metrics per mode (bill + dollar top-1, abstain = wrong), coverage/precision at bars, ECE +
   reliability, strata, strong-history overrides, sign test + bootstrap vs baselines, the
   500-bill table with population reweighting, Q4 coded-time leakage share, and the G1-G5
   wiring-gate verdicts printed PASS / FAIL.

Usage (repo root):
  .venv/bin/python -m scripts.u698_decision_model_backtest          # dry-run: population counts
  .venv/bin/python -m scripts.u698_decision_model_backtest --run    # train + evaluate + write
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import csv
import hashlib
import json
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from entities.coding_suggestion.business.decision_model.features import (
    M0_FEATURES,
    M1_FEATURES,
    M1_SIGNS,
    MIN_PRIOR_BILLS,
    PRIOR_CUTOFF_CODED,
    PRIOR_CUTOFF_CREATED,
    UNKNOWN_CODED_ADMIT,
    UNKNOWN_CODED_DROP,
    AsOfIndex,
    BillContext,
    LlmAnswers,
    NeighbourEvidence,
    PriorBill,
    QueryBill,
    build_context,
    m0_features,
    m1_features,
)
from entities.coding_suggestion.business.decision_model.learn import (
    Decision,
    LogisticModel,
    fit_calibrator,
    fit_logistic,
    rank_candidates,
    score_candidates,
)
from entities.coding_suggestion.business.decision_model import stats as st
from intelligence.cascade.bill_coding import SUSPENSE_SCC_NUMBER, normalize_scc_number
from intelligence.cascade.bill_coding_payload import build_candidate_sccs_for_bill
from scripts.u653b_backtest import (
    DEFAULT_CAL_SINCE,
    DEFAULT_CAL_UNTIL,
    DEFAULT_EVAL_SINCE,
    DEFAULT_EVAL_UNTIL,
    BillRow,
    _consistency_bucket,
    _date_only,
    _parse_dt,
    validate_windows,
)
from scripts.u654a_text_backtest import _json_default, assert_outputs_privacy_safe

MODEL_VERSION = "u698-decision-model-v1"
DEFAULT_OUT_DIR = Path("docs/design/u698")
DEFAULT_BILL_IDS_FROM = Path(
    "docs/design/u701b/u654a_predictions_claude-haiku_20261028T150518Z.csv"
)
NAMED_CASES = {40921: "walker_273811", 40939: "nachos_417821"}
BARS = (0.5, 0.7, 0.8, 0.9)
CV_FOLDS = 6
M1_FOLDS = 5
CAL_FRACTION = 0.20
VARIANT_PDF = "pdf_only"
VARIANT_PDF_HIST = "pdf_plus_history"
VARIANTS = (VARIANT_PDF, VARIANT_PDF_HIST)
LADDER_REFERENCE = {"A": 0.642, "B": 0.644}  # u701b/README.md:24
CODED_TIME_RERUN_SHARE = 0.05  # design §2.2 rule 6
LADDER_PARITY_POINTS = 0.5  # G1 is adjudicated only when the recomputed ladder matches the reference
DEFAULT_LIVE_WEAK_SHARE = 0.68  # U-744 live scorecard: weak / no-history share of live drops
M1_SCHEME_CROSS = "cross_fit"
M1_SCHEME_FORWARD = "forward_chained"

_EXTRA_SQL = """
WITH Line1 AS (
    SELECT bli.[BillId], bli.[SubCostCodeId],
           ROW_NUMBER() OVER (PARTITION BY bli.[BillId] ORDER BY bli.[Id] ASC) AS [Rn]
    FROM dbo.[BillLineItem] bli
    WHERE bli.[SubCostCodeId] IS NOT NULL
)
SELECT b.[Id] AS [BillId],
       b.[CreatedDatetime],
       l1.[SubCostCodeId] AS [Line1SubCostCodeId],
       bcr.[CompletedAt],
       b.[StatusDatetime],
       b.[StatusOrigin]
FROM dbo.[Bill] b
INNER JOIN Line1 l1 ON l1.[BillId] = b.[Id] AND l1.[Rn] = 1
LEFT JOIN dbo.[BillCompletionResult] bcr ON bcr.[BillPublicId] = b.[PublicId]
WHERE b.[IsDraft] = 0
"""

_CATALOG_SQL = """
SELECT [Id], [Number], [CreatedDatetime], COALESCE([IsHoldingCode], 0) AS [IsHoldingCode]
FROM dbo.[SubCostCode]
WHERE [Number] IS NOT NULL
"""


# =============================================================================================
# Population
# =============================================================================================


@dataclass(frozen=True)
class CatalogRef:
    """Shape ``build_candidate_sccs_for_bill`` reads; ``name`` is deliberately empty."""

    scc_id: int
    number: str
    created: datetime
    holding: bool = False
    name: str = ""


@dataclass
class Population:
    bills: list[BillRow]
    line1_by_bill: dict[int, int] = field(default_factory=dict)
    coded_at_by_bill: dict[int, Optional[datetime]] = field(default_factory=dict)
    coded_source_by_bill: dict[int, str] = field(default_factory=dict)
    catalog: dict[int, CatalogRef] = field(default_factory=dict)
    pdf_tokens_by_bill_id: dict[int, frozenset[str]] = field(default_factory=dict)
    load_stats: Any = None


def coded_at_for(
    created: datetime,
    completed_at: Optional[datetime],
    status_dt: Optional[datetime],
    status_origin: Optional[str],
    *,
    backfill_as_created: bool = False,
) -> tuple[Optional[datetime], str]:
    """When a bill's coding became visible (Q4). Returns (coded_at or None, source).

    - a completion result row -> its ``CompletedAt``;
    - ``StatusOrigin='completion'`` -> ``StatusDatetime``;
    - ``StatusOrigin='qbo_pull'`` -> ``created`` (the pull creates the bill already coded);
    - ``StatusOrigin='backfill'`` -> ``created`` only with ``backfill_as_created`` (the status
      backfill stamped its own run time, so the true coding time is unknown), else unknown;
    - anything else -> unknown."""
    if completed_at is not None:
        return completed_at, "completion_result"
    if status_origin == "completion" and status_dt is not None:
        return status_dt, "status_completion"
    if status_origin == "qbo_pull":
        return created, "qbo_pull_created"
    if status_origin == "backfill":
        return (created, "backfill_created") if backfill_as_created else (None, "backfill_unknown")
    return None, "unknown"


class Choosable:
    """As-of catalog predicate: the live ``build_candidate_sccs_for_bill`` rules (no suspense,
    no ambiguous number, created strictly before the bill) plus no holding code.

    Ambiguity is evaluated AS OF the bill: a number is ambiguous only when two or more codes
    carrying it (holding codes included, as u654a counts them) were created before the bill."""

    def __init__(self, catalog: Mapping[int, CatalogRef]) -> None:
        suspense = normalize_scc_number(SUSPENSE_SCC_NUMBER)
        by_num: dict[str, list[datetime]] = defaultdict(list)
        for ref in catalog.values():
            num = normalize_scc_number(ref.number)
            if num and num != suspense:
                by_num[num].append(ref.created)
        self._num_created = {n: sorted(v) for n, v in by_num.items()}
        live = {r.scc_id: r for r in catalog.values() if not r.holding}
        normed = {
            sid: CatalogRef(r.scc_id, normalize_scc_number(r.number), r.created, r.holding)
            for sid, r in live.items()
        }
        # The live builder (no ambiguity yet) decides suspense / blank numbers; ambiguity is as-of.
        allowed = build_candidate_sccs_for_bill(
            normed, as_of=datetime.max, ambiguous_numbers=frozenset()
        )
        nums = {row["number"] for row in allowed}
        self._ref = {sid: (r.created, r.number) for sid, r in normed.items() if r.number in nums}

    def at(self, as_of: datetime) -> Callable[[int], bool]:
        ref = self._ref
        num_created = self._num_created

        def ok(c: int) -> bool:
            r = ref.get(c)
            if r is None or not r[0] < as_of:
                return False
            return bisect_left(num_created[r[1]], as_of) == 1

        return ok


def load_population_from_db(
    *, workers: int, timeout: float, backfill_as_created: bool = False
) -> Population:  # pragma: no cover
    """Reads PRODUCTION (read-only, rollback). Never called by tests."""
    from scripts import u691_similar_bills_backtest as u691
    from scripts.u653b_backtest import load_bills_from_db
    from scripts.u654a_text_backtest import load_blob_urls_from_db
    from shared.database import get_connection
    from shared.text_blob_read import make_text_blob_reader

    bills = load_bills_from_db()
    pop = Population(bills=bills)
    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(_EXTRA_SQL)
            for r in cur.fetchall():
                bid = int(r.BillId)
                pop.line1_by_bill[bid] = int(r.Line1SubCostCodeId)
                coded, source = coded_at_for(
                    _parse_dt(r.CreatedDatetime),
                    _parse_dt(r.CompletedAt) if r.CompletedAt is not None else None,
                    _parse_dt(r.StatusDatetime) if r.StatusDatetime is not None else None,
                    r.StatusOrigin,
                    backfill_as_created=backfill_as_created,
                )
                pop.coded_at_by_bill[bid] = coded
                pop.coded_source_by_bill[bid] = source
            cur.execute(_CATALOG_SQL)
            for r in cur.fetchall():
                pop.catalog[int(r.Id)] = CatalogRef(
                    int(r.Id), str(r.Number), _parse_dt(r.CreatedDatetime), bool(r.IsHoldingCode)
                )
        finally:
            conn.rollback()
    coded_ids = {b.bill_id for b in bills}
    urls = {bid: u for bid, u in load_blob_urls_from_db().items() if bid in coded_ids}

    def _progress(done: int, total: int) -> None:
        if done == total or done % max(1, total // 20) == 0:
            print(f"U-698: PDF blob reads {done}/{total}", flush=True)

    pop.pdf_tokens_by_bill_id, pop.load_stats = u691.load_pdf_text_by_bill_id(
        urls,
        read_blob=make_text_blob_reader(),
        max_workers=workers,
        timeout_seconds=timeout,
        progress=_progress,
    )
    return pop


def load_dry_run_inputs() -> tuple[list[BillRow], dict[int, str]]:  # pragma: no cover
    from scripts.u653b_backtest import load_bills_from_db
    from scripts.u654a_text_backtest import load_blob_urls_from_db

    return load_bills_from_db(), load_blob_urls_from_db()


# =============================================================================================
# E1 (U-701b) LLM answers
# =============================================================================================


@dataclass(frozen=True)
class E1Row:
    bill_id: int
    mode: str
    stratum: str
    text_pred: Optional[int]
    llm: LlmAnswers


def _int_or_none(v: Optional[str]) -> Optional[int]:
    v = (v or "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


def _float_or_none(v: Optional[str]) -> Optional[float]:
    v = (v or "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def load_e1_rows(path: Path) -> dict[tuple[int, str], E1Row]:
    """Text-tier rows of the committed E1 CSV, keyed (BillId, mode). Ids / codes / numbers only."""
    out: dict[tuple[int, str], E1Row] = {}
    with Path(path).open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("suggester") or "") != "text":
                continue
            bid = _int_or_none(row.get("BillId"))
            mode = (row.get("mode") or "").strip()
            if bid is None or mode not in ("A", "B"):
                continue
            sonnet_ok = (row.get("escalated") == "1") and (row.get("escalate_valid") == "1")
            out[(bid, mode)] = E1Row(
                bill_id=bid,
                mode=mode,
                stratum=(row.get("stratum") or "").strip(),
                text_pred=_int_or_none(row.get("predicted_scc_id")),
                llm=LlmAnswers(
                    haiku_scc_id=_int_or_none(row.get("primary_scc_id")),
                    haiku_conf=_float_or_none(row.get("primary_confidence")),
                    sonnet_scc_id=_int_or_none(row.get("escalate_scc_id")) if sonnet_ok else None,
                    sonnet_conf=_float_or_none(row.get("escalate_confidence")) if sonnet_ok else None,
                ),
            )
    return out


# =============================================================================================
# Features over the population
# =============================================================================================


def build_index(
    pop: Population, *, cutoff: str = PRIOR_CUTOFF_CREATED, unknown: str = UNKNOWN_CODED_ADMIT
) -> AsOfIndex:
    pdf = pop.pdf_tokens_by_bill_id
    return AsOfIndex(
        (
            PriorBill(
                bill_id=b.bill_id,
                vendor_id=b.vendor_id,
                created=b.created,
                label_scc_id=b.label_scc_id,
                project_id=b.label_project_id,
                amount=float(b.total_abs_amount),
                has_pdf=b.bill_id in pdf,
                coded_at=pop.coded_at_by_bill.get(b.bill_id),
            )
            for b in pop.bills
        ),
        cutoff=cutoff,
        unknown=unknown,
    )


def query_bill(pop: Population, b: BillRow) -> QueryBill:
    toks = pop.pdf_tokens_by_bill_id.get(b.bill_id)
    return QueryBill(
        bill_id=b.bill_id,
        vendor_id=b.vendor_id,
        created=b.created,
        amount=float(b.total_abs_amount),
        project_id=b.label_project_id,
        has_text=toks is not None,
        pdf_token_count=len(toks) if toks else 0,
    )


def neighbour_pass(
    pop: Population,
    *,
    idf_since: Optional[date],
    idf_until: Optional[date],
    idf_before: Optional[date],
    upto: date,
    top_k: int = 10,
    coded_unknown: Optional[str] = None,
) -> dict[int, list[NeighbourEvidence]]:  # pragma: no cover - exercised by the session's run
    """Rolling word neighbours for every PDF bill created on/before ``upto``.

    ``coded_unknown`` None = created-time cut only; "admit" / "drop" = also require a known coded
    time before the query, admitting / dropping neighbours whose coded time is unknown."""
    from scripts import u691_similar_bills_backtest as u691

    cfg = u691.TokenSourceConfig(
        text_source=u691.TEXT_SOURCE_PDF, pdf_tokens_by_bill_id=pop.pdf_tokens_by_bill_id
    )
    enriched = [
        u691.EnrichedBill(bill=b, memo="", line_descriptions="", line_count=0)
        for b in pop.bills
        if b.bill_id in pop.pdf_tokens_by_bill_id and _date_only(b.created) <= upto
    ]
    if idf_before is not None:
        idf = u691.compute_idf_before_date(enriched, before=idf_before, config=cfg)
    else:
        idf = u691.compute_idf_from_corpus(enriched, since=idf_since, until=idf_until, config=cfg)
    ids = frozenset(eb.bill.bill_id for eb in enriched)
    raw = u691.collect_top_neighbours_for_sample(enriched, ids, idf, top_k=top_k, token_config=cfg)
    created = {b.bill_id: b.created for b in pop.bills}
    out: dict[int, list[NeighbourEvidence]] = {}
    for bid, nbrs in raw.items():
        rows = []
        for n in nbrs:
            pb = n.prior.enriched.bill
            if coded_unknown is not None:
                ca = pop.coded_at_by_bill.get(pb.bill_id)
                if ca is None:
                    if coded_unknown == UNKNOWN_CODED_DROP:
                        continue
                elif not ca < created[bid]:
                    continue
            rows.append(
                NeighbourEvidence(
                    combined=float(n.combined),
                    match=float(n.match),
                    scc_id=int(pb.label_scc_id),
                    same_vendor=bool(n.same_vendor),
                    prior_bill_id=int(pb.bill_id),
                    prior_created=pb.created,
                )
            )
        out[bid] = rows
    return out


def make_context(
    pop: Population,
    index: AsOfIndex,
    choosable: Choosable,
    b: BillRow,
    mode: str,
    neighbours: Mapping[int, Sequence[NeighbourEvidence]],
    llm: Optional[LlmAnswers] = None,
) -> BillContext:
    kw = {} if llm is None else {"llm": llm}
    return build_context(
        index,
        query_bill(pop, b),
        mode=mode,
        neighbours=neighbours.get(b.bill_id, ()),
        choosable=choosable.at(b.created),
        **kw,
    )


@dataclass
class Row:
    """One (bill, mode) with its context; label kept beside, never inside, the features."""

    bill: BillRow
    mode: str
    ctx: BillContext
    line1: Optional[int] = None

    @property
    def key(self) -> tuple[int, str]:
        return (self.bill.bill_id, self.mode)


def design_matrix(rows: Iterable[Row]) -> tuple[list[list[float]], list[int]]:
    x: list[list[float]] = []
    y: list[int] = []
    for r in rows:
        for c in r.ctx.candidates:
            x.append(m0_features(r.ctx, c))
            y.append(1 if c == r.bill.label_scc_id else 0)
    return x, y


# =============================================================================================
# M0 fit / score
# =============================================================================================


@dataclass
class M0Fit:
    model: LogisticModel
    calibrator: Any
    train_n: int
    cal_n: int
    cal_pdf_n: int = 0        # PDF (bill, mode) rows in the calibration slice with a candidate
    cal_pdf_correct: int = 0  # ... of which M0's raw arg-max was right (variant selection, P2-c)
    cal_pdf_bill_ids: frozenset = frozenset()  # PDF bills in the calibration slice
    cal_pdf_max_created: Optional[datetime] = None


@dataclass
class Scored:
    row: Row
    decision: Decision
    fold: str
    cand_raw: dict[int, float]  # RAW (uncalibrated) M0 probability per candidate (M1 input)

    @property
    def correct(self) -> bool:
        return self.decision.scc_id is not None and self.decision.scc_id == self.row.bill.label_scc_id


CutKey = tuple  # (created, bill_id)


def pdf_date_cut(rows: Sequence[Row], frac: float = CAL_FRACTION) -> Optional[CutKey]:
    """The fit / calibration cut from the PDF bills of a training window: the (created, bill_id)
    of the first bill in the last ``frac`` of PDF bills. None when there are no PDF bills."""
    bills = sorted({(r.bill.created, r.bill.bill_id) for r in rows if r.ctx.bill.has_text})
    if not bills:
        return None
    return bills[min(len(bills) - 1, int(round(len(bills) * (1 - frac))))]


def split_by_dates(
    rows: Sequence[Row], frac: float = CAL_FRACTION, *, cut: Optional[CutKey] = None
) -> tuple[list[Row], list[Row]]:
    """Fit / calibration split at ONE date cut computed from the PDF bills (``pdf_date_cut``), so
    every variant that shares the PDF rows calibrates on the same PDF bills. Rows before the cut
    fit (history-only rows included); PDF rows at/after it calibrate; history-only rows at/after
    it are dropped. Both modes of a bill land on the same side."""
    if cut is None:
        cut = pdf_date_cut(rows, frac)
    if cut is None:
        return list(rows), []
    fit = [r for r in rows if (r.bill.created, r.bill.bill_id) < cut]
    cal = [r for r in rows if (r.bill.created, r.bill.bill_id) >= cut and r.ctx.bill.has_text]
    return fit, cal


def fit_m0(rows: Sequence[Row], *, l2: float, cut: Optional[CutKey] = None) -> M0Fit:
    """Fit before the PDF date cut; calibrate on the PDF rows after it (ECE / G4 are measured on
    PDF rows, so history-only rows never shape the calibration map)."""
    fit_rows, cal_rows = split_by_dates(rows, cut=cut)
    x, y = design_matrix(fit_rows)
    model = fit_logistic(x, y, M0_FEATURES, l2=l2)
    scores, ok = [], []
    for r in cal_rows:
        if not r.ctx.bill.has_text or not r.ctx.candidates:
            continue
        d, _raw = score_row(model, None, r)
        scores.append(d.raw_prob)
        ok.append(1 if d.scc_id == r.bill.label_scc_id else 0)
    cal_ids = frozenset(r.bill.bill_id for r in cal_rows)
    return M0Fit(model, fit_calibrator(scores, ok), len(fit_rows), len(cal_rows),
                 len(scores), sum(ok), cal_ids,
                 max((r.bill.created for r in cal_rows), default=None))


def score_row(model: LogisticModel, calibrator: Any, r: Row) -> tuple[Decision, dict[int, float]]:
    """One pass over the candidates: the decision (ranked on RAW probability) and the raw map."""
    d, probs = score_candidates(
        model, r.ctx.candidates, lambda c: m0_features(r.ctx, c),
        calibrate=calibrator, contributions=True,
    )
    return d, dict(zip(r.ctx.candidates, probs))


def score_rows(fit: M0Fit, rows: Iterable[Row], fold: str) -> list[Scored]:
    out = []
    for r in rows:
        d, raw = score_row(fit.model, fit.calibrator, r)
        out.append(Scored(r, d, fold, raw))
    return out


# =============================================================================================
# M1 (LLM stacker), out-of-fold on the E1 bills
# =============================================================================================


def m1_eligible(r: Row) -> bool:
    """Layer 2 only where an LLM answer exists; Mode-A strong history is M0-only (live skips LLM)."""
    llm = r.ctx.llm
    if llm.haiku_scc_id is None and llm.sonnet_scc_id is None:
        return False
    return not (r.mode == "A" and r.ctx.history.strong)


def time_folds(bill_created: Mapping[int, datetime], k: int = M1_FOLDS) -> dict[int, int]:
    """Contiguous time blocks: bill id -> fold index 0..k-1."""
    order = sorted(bill_created, key=lambda b: (bill_created[b], b))
    n = len(order)
    return {bid: min(k - 1, i * k // max(n, 1)) for i, bid in enumerate(order)}


def m1_rows_xy(items: Sequence[Scored]) -> tuple[list[list[float]], list[int]]:
    x, y = [], []
    for s in items:
        for c in s.row.ctx.candidates:
            x.append(m1_features(s.row.ctx, c, s.cand_raw.get(c, 0.0)))
            y.append(1 if c == s.row.bill.label_scc_id else 0)
    return x, y


def m1_decide(model: LogisticModel, s: Scored, calibrator: Any = None) -> Decision:
    """Rank on M1's raw output; ties fall back to M0's RAW score. Only the output is calibrated."""
    cands = list(s.row.ctx.candidates)
    xs = [m1_features(s.row.ctx, c, s.cand_raw.get(c, 0.0)) for c in cands]
    return rank_candidates(
        cands,
        [model.prob(x) for x in xs],
        calibrate=calibrator,
        contributions=[model.contributions(x) for x in xs],
        tiebreak=[s.cand_raw.get(c, 0.0) for c in cands],
    )


def m1_fit(
    items: Sequence[Scored], *, l2: float, k: int = M1_FOLDS, scheme: str = M1_SCHEME_CROSS
) -> tuple[dict[tuple[int, str], Decision], dict[tuple[int, str], int], list[dict]]:
    """Out-of-fold M1 over contiguous time blocks.

    - ``cross_fit``: fold f is scored by a model trained on every OTHER block (later ones too);
    - ``forward_chained``: fold f is scored by a model trained on blocks BEFORE f only; fold 0
      has no model (those bills keep M0's pick).
    The output calibrator is fit on the training blocks' own chosen-candidate scores (in-sample to
    M1; it moves probabilities, never the ranking)."""
    eligible = [s for s in items if m1_eligible(s.row)]
    folds = time_folds({s.row.bill.bill_id: s.row.bill.created for s in eligible}, k)
    decisions: dict[tuple[int, str], Decision] = {}
    fold_of: dict[tuple[int, str], int] = {}
    fold_models: list[dict] = []
    for f in range(k):
        if scheme == M1_SCHEME_CROSS:
            train = [s for s in eligible if folds[s.row.bill.bill_id] != f]
        elif scheme == M1_SCHEME_FORWARD:
            train = [s for s in eligible if folds[s.row.bill.bill_id] < f]
        else:
            raise ValueError(f"unknown M1 scheme {scheme!r}")
        test = [s for s in eligible if folds[s.row.bill.bill_id] == f]
        if not test:
            continue
        x, y = m1_rows_xy(train)
        if not x or sum(y) == 0:
            continue
        model = fit_logistic(x, y, M1_FEATURES, l2=l2, signs=M1_SIGNS)
        raw = [m1_decide(model, s) for s in train]
        calibrator = fit_calibrator(
            [d.raw_prob for d in raw if d.scc_id is not None],
            [1 if d.scc_id == s.row.bill.label_scc_id else 0 for d, s in zip(raw, train)
             if d.scc_id is not None],
        )
        fold_models.append({"scheme": scheme, "fold": f,
                            "train_bills": len({s.row.bill.bill_id for s in train}),
                            "model": model.to_json(), "calibrator": calibrator.to_json()})
        for s in test:
            decisions[s.row.key] = m1_decide(model, s, calibrator)
            fold_of[s.row.key] = f
    return decisions, fold_of, fold_models


def m1_cross_fit(items: Sequence[Scored], *, l2: float, k: int = M1_FOLDS):
    return m1_fit(items, l2=l2, k=k, scheme=M1_SCHEME_CROSS)


def m1_forward_chained(items: Sequence[Scored], *, l2: float, k: int = M1_FOLDS):
    return m1_fit(items, l2=l2, k=k, scheme=M1_SCHEME_FORWARD)


# =============================================================================================
# Metrics
# =============================================================================================


def stratum_of(r: Row) -> str:
    vw = r.ctx.vendor_wide
    if vw.total < MIN_PRIOR_BILLS or vw.consistency is None:
        return "cold_start"
    from decimal import Decimal

    return _consistency_bucket(Decimal(str(vw.consistency)))


def live_mix(ok: Sequence[bool], weak: Sequence[bool], weak_share: float) -> dict:
    """Top-1 reweighted to the live strong/weak history mix (U-744: weak = fewer than 3 prior bills
    or top share < 0.60, i.e. NOT ``history.strong``)."""
    w = [o for o, k in zip(ok, weak) if k]
    s_ = [o for o, k in zip(ok, weak) if not k]
    acc_w = (sum(w) / len(w)) if w else None
    acc_s = (sum(s_) / len(s_)) if s_ else None
    val = None
    if acc_w is not None and acc_s is not None:
        val = weak_share * acc_w + (1 - weak_share) * acc_s
    return {"value": val, "weak_share": weak_share, "weak_n": len(w), "strong_n": len(s_),
            "weak_top1": acc_w, "strong_top1": acc_s}


def baseline_asymmetry(scored: Sequence[Scored]) -> dict:
    """P2-d: baseline hits whose label M0 could not pick (not in its as-of-filtered candidates)."""
    hits = [s for s in scored if s.row.ctx.baseline_pick == s.row.bill.label_scc_id]
    blocked = [s for s in hits if s.row.bill.label_scc_id not in s.row.ctx.candidates]
    return {"baseline_hits": len(hits), "label_not_choosable_by_m0": len(blocked),
            "share": (len(blocked) / len(hits)) if hits else None}


def mode_block(scored: Sequence[Scored], *, auto_bar: float, bootstrap: int, seed: int,
               live_weak_share: float = DEFAULT_LIVE_WEAK_SHARE) -> dict:
    ok = [s.correct for s in scored]
    weak = [not s.row.ctx.history.strong for s in scored]
    amts = [float(s.row.bill.total_abs_amount) for s in scored]
    labels = [s.row.bill.label_scc_id for s in scored]
    base = [s.row.ctx.baseline_pick == l for s, l in zip(scored, labels)]
    hist = [s.row.ctx.history.top == l for s, l in zip(scored, labels)]
    probs = [s.decision.prob if s.decision.scc_id is not None else None for s in scored]
    picked = [(s.decision.prob, s.correct) for s in scored if s.decision.scc_id is not None]
    strata: dict[str, list[Scored]] = defaultdict(list)
    for s in scored:
        strata[stratum_of(s.row)].append(s)
        if s.row.bill.mixed_code:
            strata["mixed_code"].append(s)
        if s.row.ctx.vendor_pdf_prior_count == 0:
            strata["cold_start_pdf"].append(s)
        if s.row.mode == "A" and s.row.ctx.history.strong:
            strata["mode_a_strong_history"].append(s)
    strata_out = {}
    for name, items in sorted(strata.items()):
        blk = {
            "n": len(items),
            "m0": st.top1([s.correct for s in items])["bill"],
            "baseline": st.top1([s.row.ctx.baseline_pick == s.row.bill.label_scc_id for s in items])["bill"],
            "history": st.top1([s.row.ctx.history.top == s.row.bill.label_scc_id for s in items])["bill"],
        }
        if name == "mixed_code":
            blk["m0_line1_label"] = st.top1(
                [s.decision.scc_id is not None and s.decision.scc_id == s.row.line1 for s in items]
            )["bill"]
        strata_out[name] = blk
    strong = [s for s in scored if s.row.ctx.history.strong]
    ov = [s for s in strong if s.decision.scc_id != s.row.ctx.history.top]
    return {
        "n": len(scored),
        "m0": st.top1(ok, amts),
        "baseline_hist_strong_else_vote": st.top1(base, amts),
        "history_alone": st.top1(hist, amts),
        "m0_vs_baseline": {
            **st.paired(base, ok),
            "bootstrap_ci_points": st.bootstrap_diff_ci(base, ok, resamples=bootstrap, seed=seed),
        },
        "m0_vs_history": st.paired(hist, ok),
        "live_mix": {"m0": live_mix(ok, weak, live_weak_share),
                     "baseline": live_mix(base, weak, live_weak_share)},
        "baseline_asymmetry": baseline_asymmetry(scored),
        "coverage_at_bars": st.coverage_at_bars(probs, ok, sorted(set(BARS) | {auto_bar})),
        "ece": st.ece([p for p, _ in picked], [c for _, c in picked]),
        "reliability": st.reliability([p for p, _ in picked], [c for _, c in picked]),
        "strata": strata_out,
        "strong_history_overrides": {
            "strong_bills": len(strong),
            "overrides": len(ov),
            "fixed": sum(1 for s in ov if s.correct),
            "broke": sum(1 for s in ov if s.row.ctx.history.top == s.row.bill.label_scc_id),
        },
        "label_in_candidates": (
            sum(1 for s in scored if s.row.bill.label_scc_id in s.row.ctx.candidates) / len(scored)
            if scored else None
        ),
    }


def by_mode(scored: Sequence[Scored], **kw: Any) -> dict[str, dict]:
    return {m: mode_block([s for s in scored if s.row.mode == m], **kw) for m in ("A", "B")}


def coded_time_leakage(pop: Population, index: AsOfIndex, bills: Sequence[BillRow]) -> dict:
    """Q4: among strictly-earlier (created-cut) vendor priors, the share coded after the query.

    The headline share is over priors with a KNOWN coded time; the unknown share is separate.
    ``index`` must be a created-cutoff index."""
    after = unknown = total = 0
    for b in bills:
        for p in index.vendor_priors(b.vendor_id, as_of=b.created, exclude_bill_id=b.bill_id):
            total += 1
            ca = pop.coded_at_by_bill.get(p.bill_id)
            if ca is None:
                unknown += 1
            elif ca >= b.created:
                after += 1
    known = total - unknown
    share = (after / known) if known else None
    sources: dict[str, int] = defaultdict(int)
    for src in pop.coded_source_by_bill.values():
        sources[src] += 1
    return {
        "priors": total,
        "priors_known_coded_time": known,
        "coded_after_query": after,
        "coded_time_unknown": unknown,
        "share_coded_after_of_known": share,
        "share_unknown": (unknown / total) if total else None,
        "coded_time_sources": dict(sorted(sources.items())),
        "rerun_with_coded_cutoff_recommended": bool(share is not None and share > CODED_TIME_RERUN_SHARE),
    }


def add_months(d: date, k: int) -> date:
    m = d.month - 1 + k
    y = d.year + m // 12
    m = m % 12 + 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def cv_folds(eval_since: date, eval_until: date, k: int = CV_FOLDS) -> list[tuple[date, date]]:
    """Monthly expanding folds anchored at eval_since; the last runs to eval_until inclusive."""
    starts = [add_months(eval_since, i) for i in range(k)]
    out = []
    for i, s in enumerate(starts):
        e = starts[i + 1] - timedelta(days=1) if i + 1 < k else eval_until
        out.append((s, e))
    return out


# =============================================================================================
# Run
# =============================================================================================


@dataclass
class Options:
    cal_since: date = DEFAULT_CAL_SINCE
    cal_until: date = DEFAULT_CAL_UNTIL
    eval_since: date = DEFAULT_EVAL_SINCE
    eval_until: date = DEFAULT_EVAL_UNTIL
    l2_m0: float = 1.0
    l2_m1: float = 10.0
    prior_cutoff: str = PRIOR_CUTOFF_CREATED
    variants: tuple[str, ...] = VARIANTS
    bootstrap: int = 1000
    seed: int = 698
    auto_accept_bar: float = 0.9
    skip_fixed_split: bool = False
    live_weak_share: float = DEFAULT_LIVE_WEAK_SHARE
    unknown_coded: str = UNKNOWN_CODED_ADMIT
    backfill_as_created: bool = False


def _in(d: datetime, lo: date, hi: date) -> bool:
    return lo <= _date_only(d) <= hi


def run_backtest(
    pop: Population,
    e1: Mapping[tuple[int, str], E1Row],
    opts: Options,
    *,
    neighbours_fn: Callable[..., dict[int, list[NeighbourEvidence]]] = neighbour_pass,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    validate_windows(opts.cal_since, opts.cal_until, opts.eval_since, opts.eval_until)
    t0 = time.time()
    index = build_index(pop, cutoff=opts.prior_cutoff, unknown=opts.unknown_coded)
    choosable = Choosable(pop.catalog)
    coded_unknown = opts.unknown_coded if opts.prior_cutoff == PRIOR_CUTOFF_CODED else None
    pdf = pop.pdf_tokens_by_bill_id
    bills = sorted(pop.bills, key=lambda b: (b.created, b.bill_id))
    line1 = pop.line1_by_bill

    def rows_for(sel: Iterable[BillRow], nb: Mapping[int, Sequence[NeighbourEvidence]],
                 idx: AsOfIndex = index) -> list[Row]:
        out = []
        for b in sel:
            for m in ("A", "B"):
                out.append(Row(b, m, make_context(pop, idx, choosable, b, m, nb),
                               line1.get(b.bill_id)))
        return out

    # History-only rows never change with IDF: build once.
    hist_only_rows = rows_for([b for b in bills if b.bill_id not in pdf and _date_only(b.created) <= opts.eval_until], {})
    log(f"U-698: {len(hist_only_rows)} history-only (bill, mode) rows")

    results: dict[str, Any] = {"variants": {}}
    oof: dict[str, list[Scored]] = {v: [] for v in opts.variants}
    oof_nopdf: dict[str, list[Scored]] = {v: [] for v in opts.variants}
    e1_scored: dict[str, list[Scored]] = {v: [] for v in opts.variants}
    fold_meta: dict[str, list[dict]] = {v: [] for v in opts.variants}
    last_fit: dict[str, M0Fit] = {}
    cal_ids: dict[str, list[frozenset]] = {v: [] for v in opts.variants}

    for fi, (f_start, f_end) in enumerate(cv_folds(opts.eval_since, opts.eval_until)):
        log(f"U-698: CV fold {fi} {f_start}..{f_end}: neighbour pass")
        nb = neighbours_fn(pop, idf_since=None, idf_until=None, idf_before=f_start, upto=f_end,
                           coded_unknown=coded_unknown)
        pdf_rows = rows_for([b for b in bills if b.bill_id in pdf and _date_only(b.created) <= f_end], nb)
        train_pdf = [r for r in pdf_rows if _date_only(r.bill.created) < f_start]
        test_pdf = [r for r in pdf_rows if _in(r.bill.created, f_start, f_end)]
        train_hist = [r for r in hist_only_rows if _date_only(r.bill.created) < f_start]
        test_hist = [r for r in hist_only_rows if _in(r.bill.created, f_start, f_end)]
        e1_rows = [
            Row(r.bill, r.mode,
                make_context(pop, index, choosable, r.bill, r.mode, nb, llm=e1[r.key].llm), r.line1)
            for r in test_pdf if r.key in e1
        ]
        cut = pdf_date_cut(train_pdf)  # ONE cut for both variants (same calibration PDF bills)
        for v in opts.variants:
            train = train_pdf + (train_hist if v == VARIANT_PDF_HIST else [])
            fit = fit_m0(train, l2=opts.l2_m0, cut=cut)
            last_fit[v] = fit
            cal_ids[v].append(fit.cal_pdf_bill_ids)
            fold = f"cv{fi}"
            oof[v].extend(score_rows(fit, test_pdf, fold))
            oof_nopdf[v].extend(score_rows(fit, test_hist, fold))
            e1_scored[v].extend(score_rows(fit, e1_rows, fold))
            fold_meta[v].append({"fold": fold, "start": f_start, "end": f_end,
                                 "train_rows": fit.train_n, "cal_rows": fit.cal_n,
                                 "cal_pdf_rows": fit.cal_pdf_n, "cal_pdf_correct": fit.cal_pdf_correct,
                                 "cal_pdf_bills": len(fit.cal_pdf_bill_ids),
                                 "cal_pdf_max_created": fit.cal_pdf_max_created,
                                 "calibrator": fit.calibrator.to_json().get("kind"),
                                 "test_pdf": len(test_pdf), "converged": fit.model.converged,
                                 "iterations": fit.model.iterations})
        log(f"U-698: fold {fi} done ({time.time() - t0:.0f}s)")

    common = {"auto_bar": opts.auto_accept_bar, "bootstrap": opts.bootstrap, "seed": opts.seed,
              "live_weak_share": opts.live_weak_share}
    for v in opts.variants:
        sel = selection_folds(fold_meta[v])
        cal_n = sum(f["cal_pdf_rows"] for f in sel)
        cal_ok = sum(f["cal_pdf_correct"] for f in sel)
        results["variants"][v] = {
            "folds": fold_meta[v],
            "selection_cal_slice_pdf_top1": (cal_ok / cal_n) if cal_n else None,
            "selection_cal_slice_pdf_rows": cal_n,
            "selection_cal_slice_max_created": max(
                (f["cal_pdf_max_created"] for f in sel if f["cal_pdf_max_created"] is not None),
                default=None),
            "cv_pdf_eval": by_mode(oof[v], **common),
            "cv_non_pdf_eval": by_mode(oof_nopdf[v], **common),
        }

    chosen = select_variant(results["variants"], opts.variants)
    results["chosen_variant"] = chosen
    results["chosen_rule"] = (
        "higher top-1 (both modes) on fold 0's calibration slice only: the last 20 % of the PDF "
        "bills before eval_since, the SAME PDF bills for both variants. Tie keeps pdf_only. The "
        "choice then applies to every fold, the fixed split, M1 and the gates; the other "
        "variant's CV numbers are informational. Out-of-fold eval numbers are never used."
    )

    def fixed_split(idx: AsOfIndex, unknown: Optional[str]) -> dict:
        nb = neighbours_fn(pop, idf_since=opts.cal_since, idf_until=opts.cal_until, idf_before=None,
                           upto=opts.eval_until, coded_unknown=unknown)
        pdf_rows = rows_for([b for b in bills if b.bill_id in pdf and _date_only(b.created) <= opts.eval_until],
                            nb, idx)
        train = [r for r in pdf_rows if _in(r.bill.created, opts.cal_since, opts.cal_until)]
        if chosen == VARIANT_PDF_HIST:
            hist = hist_only_rows if idx is index else rows_for(
                [r.bill for r in hist_only_rows if r.mode == "A"], {}, idx)
            train += [r for r in hist if _in(r.bill.created, opts.cal_since, opts.cal_until)]
        test = [r for r in pdf_rows if _in(r.bill.created, opts.eval_since, opts.eval_until)]
        fit = fit_m0(train, l2=opts.l2_m0)
        out = by_mode(score_rows(fit, test, "fixed"), **common)
        out["converged"] = fit.model.converged
        return out

    if not opts.skip_fixed_split:
        log("U-698: fixed split neighbour pass")
        results["fixed_split"] = fixed_split(index, coded_unknown)
        if opts.prior_cutoff == PRIOR_CUTOFF_CODED and opts.unknown_coded == UNKNOWN_CODED_ADMIT:
            log("U-698: sensitivity - fixed split with unknown-coded priors dropped")
            drop_idx = build_index(pop, cutoff=PRIOR_CUTOFF_CODED, unknown=UNKNOWN_CODED_DROP)
            sens = fixed_split(drop_idx, UNKNOWN_CODED_DROP)
            results["sensitivity_drop_unknown_coded_fixed_split"] = {
                m: {"m0": sens[m]["m0"], "admit_unknown_m0": results["fixed_split"][m]["m0"]}
                for m in ("A", "B")
            }

    # ---- the 500-bill table (M1) ----
    e1_items = e1_scored[chosen]
    m1x_dec, m1x_fold, m1x_models = m1_cross_fit(e1_items, l2=opts.l2_m1)
    m1f_dec, m1f_fold, m1f_models = m1_forward_chained(e1_items, l2=opts.l2_m1)
    m0_by_key = {s.row.key: s for s in oof[chosen]}
    table = {}
    per_bill_rows = []
    for m in ("A", "B"):
        items = [s for s in e1_items if s.row.mode == m]
        ladder_ok, vote_ok, m0_ok, m1f_ok, m1x_ok, strata_, weak = [], [], [], [], [], [], []
        for s in items:
            lbl = s.row.bill.label_scc_id
            e = e1[s.row.key]
            ladder = s.row.ctx.history.top if s.row.ctx.history.strong else e.text_pred
            m0_s = m0_by_key.get(s.row.key)
            m0_pick = m0_s.decision.scc_id if m0_s else None
            m1f_pick = m1f_dec[s.row.key].scc_id if s.row.key in m1f_dec else m0_pick
            m1x_pick = m1x_dec[s.row.key].scc_id if s.row.key in m1x_dec else m0_pick
            ladder_ok.append(ladder == lbl)
            vote_ok.append(s.row.ctx.baseline_pick == lbl)
            m0_ok.append(m0_pick == lbl)
            m1f_ok.append(m1f_pick is not None and m1f_pick == lbl)
            m1x_ok.append(m1x_pick is not None and m1x_pick == lbl)
            strata_.append(e.stratum)
            weak.append(not s.row.ctx.history.strong)
            per_bill_rows.append((s, ladder, m0_pick, m1f_pick, m1f_fold.get(s.row.key)))
        pop_share = population_stratum_shares([x for x in oof[chosen] if x.row.mode == m])
        ladder_top1 = st.top1(ladder_ok)["bill"]
        table[m] = {
            "n": len(items),
            "ladder_recomputed": ladder_top1,
            "ladder_reference": LADDER_REFERENCE[m],
            "ladder_parity_points": (
                round(100 * (ladder_top1 - LADDER_REFERENCE[m]), 2) if ladder_top1 is not None else None
            ),
            "hist_strong_else_vote": st.top1(vote_ok)["bill"],
            "m0": st.top1(m0_ok)["bill"],
            "m1_forward": st.top1(m1f_ok)["bill"],
            "m1_cross_fit": st.top1(m1x_ok)["bill"],
            "m1_forward_vs_ladder": {
                **st.paired(ladder_ok, m1f_ok),
                "bootstrap_ci_points": st.bootstrap_diff_ci(ladder_ok, m1f_ok, resamples=opts.bootstrap,
                                                            seed=opts.seed),
            },
            "m1_cross_fit_vs_ladder": {
                **st.paired(ladder_ok, m1x_ok),
                "bootstrap_ci_points": st.bootstrap_diff_ci(ladder_ok, m1x_ok, resamples=opts.bootstrap,
                                                            seed=opts.seed),
            },
            "m0_vs_ladder": st.paired(ladder_ok, m0_ok),
            "reweighted": {
                "ladder": reweighted(ladder_ok, strata_, pop_share),
                "m1_forward": reweighted(m1f_ok, strata_, pop_share),
                "m1_cross_fit": reweighted(m1x_ok, strata_, pop_share),
                "m0": reweighted(m0_ok, strata_, pop_share),
                "population_stratum_shares": pop_share,
            },
            "live_mix": {
                "ladder": live_mix(ladder_ok, weak, opts.live_weak_share),
                "m0": live_mix(m0_ok, weak, opts.live_weak_share),
                "m1_forward": live_mix(m1f_ok, weak, opts.live_weak_share),
                "m1_cross_fit": live_mix(m1x_ok, weak, opts.live_weak_share),
            },
            "baseline_asymmetry": baseline_asymmetry(items),
            "m1_eligible": sum(1 for s in items if m1_eligible(s.row)),
            "m1_forward_scored": sum(1 for s in items if s.row.key in m1f_dec),
        }
    results["e1_table"] = table
    results["m1_fold_models"] = m1x_models + m1f_models
    x_all, y_all = m1_rows_xy([s for s in e1_items if m1_eligible(s.row)])
    results["m1_all_bills_weights_for_review"] = (
        fit_logistic(x_all, y_all, M1_FEATURES, l2=opts.l2_m1, signs=M1_SIGNS).to_json()
        if x_all and sum(y_all) else None
    )
    results["non_converged_fits"] = non_converged(results)

    eval_pdf_bills = [b for b in bills if b.bill_id in pdf and _in(b.created, opts.eval_since, opts.eval_until)]
    created_index = index if opts.prior_cutoff == PRIOR_CUTOFF_CREATED else build_index(pop)
    results["coded_time_leakage"] = coded_time_leakage(pop, created_index, eval_pdf_bills)
    results["named_cases"] = named_cases(oof[chosen], e1_items, m1f_dec)
    results["gates"] = gate_check(results, opts)
    results["runtime_seconds"] = round(time.time() - t0, 1)
    results["_artefacts"] = {
        "final_m0": last_fit[chosen],
        "oof": oof[chosen],
        "e1_rows": per_bill_rows,
        "cal_ids": cal_ids,
    }
    return results


def selection_folds(folds: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Variant selection reads fold 0 only: its training window (and so its calibration slice)
    lies entirely before eval_since."""
    return list(folds[:1])


def select_variant(variants: Mapping[str, Mapping[str, Any]], names: Sequence[str]) -> str:
    """P2-c: choose on the training windows' calibration slices, never on out-of-fold eval."""
    return max(names, key=lambda v: (variants[v]["selection_cal_slice_pdf_top1"] or 0.0, v == VARIANT_PDF))


def non_converged(res: Mapping[str, Any]) -> list[dict]:
    out = []
    for v, blk in res["variants"].items():
        for f in blk["folds"]:
            if not f["converged"]:
                out.append({"model": "m0", "variant": v, "fold": f["fold"]})
    for fm in res.get("m1_fold_models", []):
        if not fm["model"]["converged"]:
            out.append({"model": "m1", "scheme": fm["scheme"], "fold": fm["fold"]})
    rv = res.get("m1_all_bills_weights_for_review")
    if rv and not rv["converged"]:
        out.append({"model": "m1", "scheme": "all_bills_review", "fold": None})
    return out


def population_stratum_shares(scored: Sequence[Scored]) -> dict[str, float]:
    counts: dict[str, int] = defaultdict(int)
    for s in scored:
        counts["mixed_code" if s.row.bill.mixed_code else stratum_of(s.row)] += 1
    n = sum(counts.values())
    return {k: v / n for k, v in sorted(counts.items())} if n else {}


def reweighted(ok: Sequence[bool], strata: Sequence[str], shares: Mapping[str, float]) -> Optional[float]:
    by: dict[str, list[bool]] = defaultdict(list)
    for o, s in zip(ok, strata):
        by[s].append(o)
    num = den = 0.0
    for s, w in shares.items():
        if by.get(s):
            num += w * (sum(by[s]) / len(by[s]))
            den += w
    return (num / den) if den else None


def named_cases(oof: Sequence[Scored], e1_items: Sequence[Scored], m1: Mapping) -> dict:
    out = {}
    for bid, tag in NAMED_CASES.items():
        rec: dict[str, Any] = {"bill_id": bid}
        for m in ("A", "B"):
            m0 = next((s for s in oof if s.row.key == (bid, m)), None)
            in_e1 = any(s.row.key == (bid, m) for s in e1_items)
            d1 = m1.get((bid, m))
            rec[m] = {
                "present": m0 is not None,
                "label_scc_id": m0.row.bill.label_scc_id if m0 else None,
                "m0_scc_id": m0.decision.scc_id if m0 else None,
                "m0_correct": m0.correct if m0 else None,
                "in_e1": in_e1,
                "m1_scc_id": d1.scc_id if d1 else None,
                "m1_correct": (d1.scc_id == m0.row.bill.label_scc_id) if (d1 and m0) else None,
            }
        out[tag] = rec
    return out


def _status(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def gate_check(res: Mapping[str, Any], opts: Options) -> dict[str, dict]:
    g: dict[str, dict] = {}
    t = res["e1_table"]
    fwd = {m: t[m]["m1_forward_vs_ladder"] for m in ("A", "B")}
    cross = {m: t[m]["m1_cross_fit_vs_ladder"] for m in ("A", "B")}

    def lift_ok(p: Mapping) -> bool:
        return (p["diff_points"] or 0) >= 3.0 and p["sign_test_p"] is not None and p["sign_test_p"] < 0.05

    a, b = fwd["A"], fwd["B"]
    g1 = (lift_ok(a) and (b["diff_points"] or 0) >= -0.5) or (lift_ok(b) and (a["diff_points"] or 0) >= -0.5)
    parity = {m: t[m]["ladder_parity_points"] for m in ("A", "B")}
    parity_ok = all(v is not None and abs(v) <= LADDER_PARITY_POINTS for v in parity.values())
    g["G1"] = {
        "status": _status(bool(g1)) if parity_ok else "UNADJUDICATED",
        "pass": bool(g1) and parity_ok,
        "basis": "m1_forward_chained",
        "diff_points": {m: fwd[m]["diff_points"] for m in "AB"},
        "p": {m: fwd[m]["sign_test_p"] for m in "AB"},
        "cross_fit_diff_points": {m: cross[m]["diff_points"] for m in "AB"},
        "cross_fit_p": {m: cross[m]["sign_test_p"] for m in "AB"},
        "ladder_recomputed": {m: t[m]["ladder_recomputed"] for m in "AB"},
        "ladder_reference": dict(LADDER_REFERENCE),
        "ladder_parity_points": parity,
        "parity_tolerance_points": LADDER_PARITY_POINTS,
    }
    cv = res["variants"][res["chosen_variant"]]["cv_pdf_eval"]
    g2 = all(
        (cv[m]["m0_vs_baseline"]["diff_points"] or 0) >= 2.0
        and (cv[m]["m0_vs_baseline"]["sign_test_p"] or 1.0) < 0.05
        for m in ("A", "B")
    )
    g["G2"] = {"status": _status(g2), "pass": g2,
               "diff_points": {m: cv[m]["m0_vs_baseline"]["diff_points"] for m in "AB"},
               "p": {m: cv[m]["m0_vs_baseline"]["sign_test_p"] for m in "AB"}}
    sa = cv["A"]["strata"].get("mode_a_strong_history")
    ov = cv["A"]["strong_history_overrides"]
    g3 = bool(
        sa and sa["m0"] is not None and sa["history"] is not None
        and (sa["m0"] - sa["history"]) * 100 >= -1.0
        and ov["fixed"] >= 1.5 * ov["broke"]
    )
    g["G3"] = {"status": _status(g3), "pass": g3, "stratum": sa, "overrides": ov}
    bar = opts.auto_accept_bar
    g4_parts = {}
    for m in ("A", "B"):
        e = cv[m]["ece"]
        at = next((r for r in cv[m]["coverage_at_bars"] if abs(r["bar"] - bar) < 1e-9), None)
        prec = at["precision"] if at else None
        g4_parts[m] = {"ece": e, "precision_at_bar": prec}
    g4 = all(
        p["ece"] is not None and p["ece"] <= 0.05
        and p["precision_at_bar"] is not None and p["precision_at_bar"] >= bar - 0.03
        for p in g4_parts.values()
    )
    g["G4"] = {"status": _status(g4), "pass": g4, "bar": bar, **g4_parts}
    nacho = res["named_cases"].get("nachos_417821", {})
    present = [m for m in ("A", "B") if nacho.get(m, {}).get("present")]
    ok = bool(present) and all(
        (nacho[m]["m1_correct"] if nacho[m]["m1_correct"] is not None else nacho[m]["m0_correct"])
        for m in present
    )
    g["G5"] = {"status": _status(ok), "pass": ok, "nacho_present_modes": present,
               "note": "FAIL means decided wrong or not in the population: explain by hand"}
    return g


# =============================================================================================
# Outputs
# =============================================================================================


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def data_hash(bills: Sequence[BillRow]) -> str:
    h = hashlib.sha256()
    for b in sorted(bills, key=lambda x: x.bill_id):
        h.update(f"{b.bill_id}:{b.label_scc_id}:{b.created.isoformat()}\n".encode())
    return h.hexdigest()


def _expl(d: Decision) -> list[str]:
    out = [f"{n}:{v:+.3f}" for n, v in d.top_contributions]
    return out + [""] * (3 - len(out))


PRED_COLUMNS = [
    "BillId", "mode", "fold", "label_scc_id", "line1_scc_id", "mixed_code", "has_text",
    "history_scc_id", "history_strong", "vote_scc_id", "baseline_scc_id",
    "m0_scc_id", "m0_prob", "m0_runner_up", "m0_correct", "expl_1", "expl_2", "expl_3",
    "in_e1", "ladder_scc_id", "m1_forward_scc_id", "m1_forward_fold", "m1_forward_correct",
]


def write_outputs(res: Mapping[str, Any], pop: Population, opts: Options, out_dir: Path,
                  stamp: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    art = res["_artefacts"]
    e1_by_key = {s.row.key: (ladder, m1, f) for s, ladder, _m0, m1, f in art["e1_rows"]}
    pred = out_dir / f"u698_predictions_{stamp}.csv"
    with pred.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PRED_COLUMNS)
        for s in art["oof"]:
            r, d = s.row, s.decision
            e = e1_by_key.get(r.key)
            w.writerow([
                r.bill.bill_id, r.mode, s.fold, r.bill.label_scc_id, r.line1 or "",
                int(r.bill.mixed_code), int(r.ctx.bill.has_text),
                r.ctx.history.top or "", int(r.ctx.history.strong), r.ctx.vote_top or "",
                r.ctx.baseline_pick or "", d.scc_id or "", f"{d.prob:.4f}", d.runner_up or "",
                int(s.correct), *_expl(d),
                int(e is not None), (e[0] or "") if e else "", (e[1] or "") if e else "",
                "" if not e or e[2] is None else e[2],
                "" if not e else int(e[1] is not None and e[1] == r.bill.label_scc_id),
            ])
    fit: M0Fit = art["final_m0"]
    model = out_dir / f"u698_model_{stamp}.json"
    model.write_text(json.dumps({
        "model_version": MODEL_VERSION,
        "variant": res["chosen_variant"],
        "m0": fit.model.to_json(),
        "m0_calibration": fit.calibrator.to_json(),
        "m1_review_weights": res["m1_all_bills_weights_for_review"],
        "windows": {"cal": [opts.cal_since, opts.cal_until], "eval": [opts.eval_since, opts.eval_until]},
        "trained_through": res["variants"][res["chosen_variant"]]["folds"][-1]["start"],
        "data_hash": data_hash(pop.bills),
        "git_sha": _git_sha(),
    }, indent=2, default=_json_ser), encoding="utf-8")
    summ = out_dir / f"u698_summary_{stamp}.json"
    public = {k: v for k, v in res.items() if not k.startswith("_")}
    summ.write_text(json.dumps({
        "model_version": MODEL_VERSION,
        "stamp": stamp,
        "options": opts.__dict__,
        "population": {"bills": len(pop.bills), "pdf_bills": len(pop.pdf_tokens_by_bill_id),
                       "rows": {v: {m: blk["cv_pdf_eval"][m]["n"] for m in "AB"}
                                for v, blk in res["variants"].items()}},
        **public,
    }, indent=2, default=_json_ser), encoding="utf-8")
    report = out_dir / f"u698_report_{stamp}.md"
    report.write_text(render_report(res, stamp), encoding="utf-8")
    paths = [pred, model, summ, report]
    for p in paths:
        assert_outputs_privacy_safe(p)
    return paths


def _json_ser(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    return _json_default(o)


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}"


def _p(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.3g}"


def _pts(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:+.1f}"


def render_report(res: Mapping[str, Any], stamp: str) -> str:
    v = res["chosen_variant"]
    cv = res["variants"][v]["cv_pdf_eval"]
    lines = [f"# U-698 decision model backtest ({stamp})", "",
             f"Chosen variant: `{v}`. Numbers are percent top-1, abstain = wrong.", "",
             f"Selection rule: {res['chosen_rule']}", "",
             "## CV pooled, PDF eval population", "",
             "| mode | n | m0 | baseline | history | diff | p | fixed | broke | livemix_m0 | livemix_base | base_blocked |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in ("A", "B"):
        b = cv[m]
        pr = b["m0_vs_baseline"]
        lines.append(f"| {m} | {b['n']} | {_pct(b['m0']['bill'])} | "
                     f"{_pct(b['baseline_hist_strong_else_vote']['bill'])} | "
                     f"{_pct(b['history_alone']['bill'])} | {_pts(pr['diff_points'])} | "
                     f"{_p(pr['sign_test_p'])} | {pr['fixed']} | {pr['broke']} | "
                     f"{_pct(b['live_mix']['m0']['value'])} | {_pct(b['live_mix']['baseline']['value'])} | "
                     f"{_pct(b['baseline_asymmetry']['share'])} |")
    lines += ["", "## Variants (Q3)", "",
              "| variant | selection_cal_slice | cv_modeA | cv_modeB |", "|---|---|---|---|"]
    for name, blk in res["variants"].items():
        c = blk["cv_pdf_eval"]
        lines.append(f"| {name} | {_pct(blk['selection_cal_slice_pdf_top1'])} | "
                     f"{_pct(c['A']['m0']['bill'])} | {_pct(c['B']['m0']['bill'])} |")
    lines += ["", "## E1 table (500 bills)", "",
              "| mode | n | ladder | ladder_ref | parity | vote | m0 | m1_fwd | m1_xfit | diff_fwd | p_fwd | diff_xfit | p_xfit |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in ("A", "B"):
        t = res["e1_table"][m]
        f_, x_ = t["m1_forward_vs_ladder"], t["m1_cross_fit_vs_ladder"]
        lines.append(f"| {m} | {t['n']} | {_pct(t['ladder_recomputed'])} | {_pct(t['ladder_reference'])} | "
                     f"{_pts(t['ladder_parity_points'])} | {_pct(t['hist_strong_else_vote'])} | "
                     f"{_pct(t['m0'])} | {_pct(t['m1_forward'])} | {_pct(t['m1_cross_fit'])} | "
                     f"{_pts(f_['diff_points'])} | {_p(f_['sign_test_p'])} | "
                     f"{_pts(x_['diff_points'])} | {_p(x_['sign_test_p'])} |")
    lines += ["", "| mode | rw_ladder | rw_m0 | rw_m1_fwd | rw_m1_xfit | live_ladder | live_m0 | live_m1_fwd | live_m1_xfit |",
              "|---|---|---|---|---|---|---|---|---|"]
    for m in ("A", "B"):
        t = res["e1_table"][m]
        rw, lm = t["reweighted"], t["live_mix"]
        lines.append(f"| {m} | {_pct(rw['ladder'])} | {_pct(rw['m0'])} | {_pct(rw['m1_forward'])} | "
                     f"{_pct(rw['m1_cross_fit'])} | {_pct(lm['ladder']['value'])} | {_pct(lm['m0']['value'])} | "
                     f"{_pct(lm['m1_forward']['value'])} | {_pct(lm['m1_cross_fit']['value'])} |")
    lk = res["coded_time_leakage"]
    lines += ["", "## Gates", "", "| gate | verdict |", "|---|---|"]
    for k, gv in res["gates"].items():
        lines.append(f"| {k} | {gv['status']} |")
    g1 = res["gates"]["G1"]
    lines += ["", f"G1 basis: forward-chained M1 (cross-fit shown beside it). Ladder parity points: "
              f"A {_pts(g1['ladder_parity_points']['A'])}, B {_pts(g1['ladder_parity_points']['B'])} "
              f"(tolerance {g1['parity_tolerance_points']}; outside it G1 is UNADJUDICATED).", "",
              f"Q4 coded-time leakage: share of priors with a KNOWN coded time that were coded after "
              f"the query = {_pct(lk['share_coded_after_of_known'])} %; priors with unknown coded time "
              f"= {_pct(lk['share_unknown'])} %; coded-time rerun recommended: "
              f"{lk['rerun_with_coded_cutoff_recommended']}.", ""]
    sens = res.get("sensitivity_drop_unknown_coded_fixed_split")
    if sens:
        lines += [f"Sensitivity (coded cutoff, unknown-coded priors dropped vs admitted, fixed split M0): "
                  f"A {_pct(sens['A']['m0']['bill'])} vs {_pct(sens['A']['admit_unknown_m0']['bill'])}, "
                  f"B {_pct(sens['B']['m0']['bill'])} vs {_pct(sens['B']['admit_unknown_m0']['bill'])}.", ""]
    nc = res.get("non_converged_fits") or []
    lines += [f"Non-converged fits: {len(nc)}"
              + ("" if not nc else " (" + ", ".join(
                  f"{x['model']}:{x.get('variant') or x.get('scheme')}:{x['fold']}" for x in nc) + ")"), ""]
    return "\n".join(lines)


# =============================================================================================
# CLI
# =============================================================================================


def dry_run_counts(bills: Sequence[BillRow], blob_urls: Mapping[int, str],
                   e1_ids: Iterable[int], opts: Options) -> dict[str, int]:
    coded = {b.bill_id for b in bills}
    linked = {bid for bid in blob_urls if bid in coded}
    e1 = set(e1_ids)
    return {
        "coded_bills": len(bills),
        "pdf_linked_bills": len(linked),
        "cal_window_bills": sum(1 for b in bills if _in(b.created, opts.cal_since, opts.cal_until)),
        "cal_window_pdf_linked": sum(1 for b in bills if b.bill_id in linked
                                     and _in(b.created, opts.cal_since, opts.cal_until)),
        "eval_window_bills": sum(1 for b in bills if _in(b.created, opts.eval_since, opts.eval_until)),
        "eval_window_pdf_linked": sum(1 for b in bills if b.bill_id in linked
                                      and _in(b.created, opts.eval_since, opts.eval_until)),
        "e1_ids": len(e1),
        "e1_ids_in_population": len(e1 & coded),
        "named_cases_in_population": sum(1 for bid in NAMED_CASES if bid in coded),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--run", action="store_true", help="train + evaluate (default: dry-run counts)")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--bill-ids-from", type=Path, default=DEFAULT_BILL_IDS_FROM)
    p.add_argument("--cal-since", type=date.fromisoformat, default=DEFAULT_CAL_SINCE)
    p.add_argument("--cal-until", type=date.fromisoformat, default=DEFAULT_CAL_UNTIL)
    p.add_argument("--eval-since", type=date.fromisoformat, default=DEFAULT_EVAL_SINCE)
    p.add_argument("--eval-until", type=date.fromisoformat, default=DEFAULT_EVAL_UNTIL)
    p.add_argument("--l2-m0", type=float, default=1.0)
    p.add_argument("--l2-m1", type=float, default=10.0)
    p.add_argument("--prior-cutoff", choices=(PRIOR_CUTOFF_CREATED, PRIOR_CUTOFF_CODED),
                   default=PRIOR_CUTOFF_CREATED)
    p.add_argument("--variants", default=",".join(VARIANTS))
    p.add_argument("--bootstrap", type=int, default=1000)
    p.add_argument("--seed", type=int, default=698)
    p.add_argument("--auto-accept-bar", type=float, default=0.9)
    p.add_argument("--skip-fixed-split", action="store_true")
    p.add_argument("--live-weak-share", type=float, default=DEFAULT_LIVE_WEAK_SHARE,
                   help="share of live drops with weak / no history (U-744; default 0.68)")
    p.add_argument("--unknown-coded", choices=(UNKNOWN_CODED_ADMIT, UNKNOWN_CODED_DROP),
                   default=UNKNOWN_CODED_ADMIT,
                   help="with --prior-cutoff coded: admit (default) or drop priors with unknown coded time")
    p.add_argument("--backfill-coded-at-created", action="store_true",
                   help="treat StatusOrigin=backfill bills as coded at creation (default: unknown)")
    p.add_argument("--pdf-workers", type=int, default=8)
    p.add_argument("--pdf-timeout", type=float, default=120.0)
    return p


def options_from_args(a: argparse.Namespace) -> Options:
    variants = tuple(v.strip() for v in a.variants.split(",") if v.strip())
    bad = [v for v in variants if v not in VARIANTS]
    if bad or not variants:
        raise SystemExit(f"--variants must be from {VARIANTS}")
    if not 0.0 <= a.live_weak_share <= 1.0:
        raise SystemExit("--live-weak-share must be in [0, 1]")
    return Options(a.cal_since, a.cal_until, a.eval_since, a.eval_until, a.l2_m0, a.l2_m1,
                   a.prior_cutoff, variants, a.bootstrap, a.seed, a.auto_accept_bar,
                   a.skip_fixed_split, a.live_weak_share, a.unknown_coded,
                   a.backfill_coded_at_created)


def main(
    argv: Optional[list[str]] = None,
    *,
    dry_loader: Callable[[], tuple[list[BillRow], dict[int, str]]] = load_dry_run_inputs,
    pop_loader: Callable[..., Population] = load_population_from_db,
    runner: Callable[..., dict[str, Any]] = run_backtest,
) -> int:
    a = build_arg_parser().parse_args(argv)
    opts = options_from_args(a)
    validate_windows(opts.cal_since, opts.cal_until, opts.eval_since, opts.eval_until)
    e1 = load_e1_rows(a.bill_ids_from)
    if not a.run:
        bills, urls = dry_loader()
        counts = dry_run_counts(bills, urls, {bid for bid, _m in e1}, opts)
        print("U-698 dry-run (no blob reads, no training):")
        for k, v in counts.items():
            print(f"  {k}: {v}")
        print("Re-run with --run to train and evaluate.")
        return 0
    pop = pop_loader(workers=a.pdf_workers, timeout=a.pdf_timeout,
                     backfill_as_created=opts.backfill_as_created)
    res = runner(pop, e1, opts)
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    paths = write_outputs(res, pop, opts, a.out_dir, stamp)
    g1 = res["gates"]["G1"]
    print(f"Ladder recomputed vs reference: {g1['ladder_recomputed']} vs {g1['ladder_reference']} "
          f"(parity points {g1['ladder_parity_points']})")
    for k, gv in res["gates"].items():
        print(f"{k}: {gv['status']}")
    for p in paths:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    from scripts.sync_helper import assert_cli_system_admin

    assert_cli_system_admin()
    sys.exit(main())
