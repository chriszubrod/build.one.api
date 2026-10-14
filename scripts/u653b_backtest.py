"""
U-653b: read-only rolling-origin backtest for bill history coding suggestions (U-639 §10).

Loads completed coded Bills in one SELECT (rollback after read), replays vendor×project /
vendor-wide history in memory, calibrates on the calibration window, reports metrics on
the evaluation window. Memo rung is excluded from v1 (production BillCodingSuggester
falls through to memo after history; this harness measures history only).

Usage (repo root):
  ./.venv/bin/python -m scripts.u653b_backtest
  ./.venv/bin/python -m scripts.u653b_backtest --verify-sample 200 --out-dir docs/design/u653b
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from entities.coding_suggestion.business.bill_suggestion_service import (
    BillCodingSuggester,
    MODEL_VERSION,
    _smoothed_share,
)
from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository
from scripts.sync_helper import assert_cli_system_admin
from shared.database import get_connection

MIN_PRIOR_BILLS = 3
DECILE_COUNT = 10

DEFAULT_CAL_SINCE = date(2025, 10, 11)
DEFAULT_CAL_UNTIL = date(2026, 4, 10)
DEFAULT_EVAL_SINCE = date(2026, 4, 11)
DEFAULT_EVAL_UNTIL = date(2026, 10, 10)

# §10.5 proposed thresholds (verdicts only).
THRESHOLD_MODE_A_TOP1_TRAINED_MODEL = Decimal("0.85")
THRESHOLD_PREFILL_LOW_CONFIDENCE = Decimal("0.60")
_LOAD_SQL = """
WITH CodedBills AS (
    SELECT DISTINCT b.[Id] AS [BillId]
    FROM dbo.[Bill] b
    INNER JOIN dbo.[BillLineItem] bli ON bli.[BillId] = b.[Id]
    WHERE b.[IsDraft] = 0
      AND bli.[SubCostCodeId] IS NOT NULL
),
LineAgg AS (
    SELECT
        bli.[BillId],
        bli.[SubCostCodeId],
        SUM(ABS(bli.[Amount])) AS [AbsTotal],
        MIN(bli.[Id]) AS [MinLineId]
    FROM dbo.[BillLineItem] bli
    INNER JOIN CodedBills cb ON cb.[BillId] = bli.[BillId]
    WHERE bli.[SubCostCodeId] IS NOT NULL
    GROUP BY bli.[BillId], bli.[SubCostCodeId]
),
DominantScc AS (
    SELECT
        la.[BillId],
        la.[SubCostCodeId],
        la.[AbsTotal],
        la.[MinLineId],
        ROW_NUMBER() OVER (
            PARTITION BY la.[BillId]
            ORDER BY la.[AbsTotal] DESC, la.[MinLineId] ASC
        ) AS [Rn]
    FROM LineAgg la
),
Dominant AS (
    SELECT
        ds.[BillId],
        ds.[SubCostCodeId] AS [DominantSubCostCodeId],
        ds.[AbsTotal] AS [DominantAbsTotal],
        bli.[ProjectId] AS [DominantProjectId]
    FROM DominantScc ds
    INNER JOIN dbo.[BillLineItem] bli ON bli.[Id] = ds.[MinLineId]
    WHERE ds.[Rn] = 1
),
SccCounts AS (
    SELECT la.[BillId], COUNT(DISTINCT la.[SubCostCodeId]) AS [DistinctSccCount]
    FROM LineAgg la
    GROUP BY la.[BillId]
),
BillTotal AS (
    SELECT bli.[BillId], SUM(ABS(COALESCE(bli.[Amount], 0))) AS [TotalAbsAmount]
    FROM dbo.[BillLineItem] bli
    INNER JOIN CodedBills cb ON cb.[BillId] = bli.[BillId]
    GROUP BY bli.[BillId]
)
SELECT
    b.[Id] AS [BillId],
    b.[VendorId],
    b.[CreatedDatetime],
    d.[DominantSubCostCodeId],
    d.[DominantProjectId],
    bt.[TotalAbsAmount],
    d.[DominantAbsTotal],
    CASE WHEN sc.[DistinctSccCount] > 1 THEN 1 ELSE 0 END AS [MixedCode],
    b.[IntakeSource],
    b.[SourceEmailMessageId],
    COALESCE(u.[IsAgent], 0) AS [CreatorIsAgent],
    qbo_pull.[QboStagingCreated]
FROM dbo.[Bill] b
LEFT JOIN dbo.[User] u ON u.[Id] = b.[CreatedByUserId]
-- dbo-native QBO identity (U-238a; qbo.BillBill was retired by U-355). The
-- staging row is unique on (QboId, RealmId). Never alias a qbo.* Id as a BillId.
OUTER APPLY (
    SELECT qb.[CreatedDatetime] AS [QboStagingCreated]
    FROM qbo.[Bill] qb
    WHERE qb.[QboId] = b.[QboId]
      AND qb.[RealmId] = b.[RealmId]
      AND qb.[CreatedDatetime] <= DATEADD(second, 5, b.[CreatedDatetime])
) qbo_pull
INNER JOIN CodedBills cb ON cb.[BillId] = b.[Id]
INNER JOIN Dominant d ON d.[BillId] = b.[Id]
INNER JOIN BillTotal bt ON bt.[BillId] = b.[Id]
INNER JOIN SccCounts sc ON sc.[BillId] = b.[Id]
WHERE b.[IsDraft] = 0
ORDER BY b.[CreatedDatetime], b.[Id]
"""

@dataclass(frozen=True)
class PriorBillVote:
    bill_id: int
    created: datetime
    dominant_scc_id: int
    dominant_project_id: Optional[int]


@dataclass(frozen=True)
class BillRow:
    bill_id: int
    vendor_id: int
    created: datetime
    label_scc_id: int
    label_project_id: Optional[int]
    total_abs_amount: Decimal
    mixed_code: bool
    intake_source: Optional[str] = None
    source_email_message_id: Optional[int] = None
    creator_is_agent: bool = False
    qbo_staging_created: Optional[datetime] = None


@dataclass
class HistoryVoteCounts:
    top_scc_id: Optional[int]
    top_count: int
    second_count: int
    total_count: int
    ranked_scc_ids: list[int] = field(default_factory=list)


@dataclass
class ModePrediction:
    mode: str
    predicted_scc_id: Optional[int]
    predicted_project_id: Optional[int]
    source: str
    raw_score: Optional[Decimal]
    top3_scc_ids: list[int]
    prior_bill_count: int


@dataclass
class ScoredBill:
    bill: BillRow
    mode_a: ModePrediction
    mode_b: ModePrediction


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time())
    raise TypeError(f"expected datetime, got {type(value)}")


def _date_only(value: datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def dominant_scc_from_line_amounts(
    lines: Sequence[tuple[int, int, Optional[int], float]],
) -> tuple[int, Optional[int], bool]:
    """
    lines: (line_id, scc_id, project_id, amount) with scc_id not null.
    Returns (dominant_scc_id, dominant_project_id, mixed_code).
    """
    agg: dict[int, tuple[Decimal, int]] = {}
    for line_id, scc_id, _project_id, amount in lines:
        abs_amt = abs(Decimal(str(amount if amount is not None else 0)))
        prev = agg.get(scc_id)
        if prev is None:
            agg[scc_id] = (abs_amt, line_id)
        else:
            agg[scc_id] = (prev[0] + abs_amt, min(prev[1], line_id))
    ranked = sorted(
        agg.items(),
        key=lambda item: (-item[1][0], item[1][1]),
    )
    top_scc = ranked[0][0]
    min_line_id = ranked[0][1][1]
    project_by_line = {lid: p for lid, _s, p, _a in lines}
    dom_project = project_by_line.get(min_line_id)
    mixed = len(agg) > 1
    return top_scc, dom_project, mixed


def _count_votes(
    votes: Iterable[PriorBillVote],
    *,
    project_id: Optional[int] = None,
) -> HistoryVoteCounts:
    filtered = [
        v
        for v in votes
        if project_id is None
        or (v.dominant_project_id is not None and v.dominant_project_id == project_id)
    ]
    total = len(filtered)
    if total == 0:
        return HistoryVoteCounts(None, 0, 0, 0, [])

    counts: dict[int, int] = defaultdict(int)
    for v in filtered:
        counts[v.dominant_scc_id] += 1

    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    ranked_ids = [scc_id for scc_id, _ in ranked]
    top_scc, top_count = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0

    top_out = top_scc if total >= MIN_PRIOR_BILLS else None
    return HistoryVoteCounts(top_out, top_count, second_count, total, ranked_ids)


def _count_project_votes(votes: Iterable[PriorBillVote]) -> HistoryVoteCounts:
    filtered = [v for v in votes if v.dominant_project_id is not None]
    total = len(filtered)
    if total == 0:
        return HistoryVoteCounts(None, 0, 0, 0, [])

    counts: dict[int, int] = defaultdict(int)
    for v in filtered:
        counts[int(v.dominant_project_id)] += 1

    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    ranked_ids = [pid for pid, _ in ranked]
    top_id, top_count = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0
    top_out = top_id if total >= MIN_PRIOR_BILLS else None
    return HistoryVoteCounts(top_out, top_count, second_count, total, ranked_ids)


def priors_before(
    vendor_votes: Sequence[PriorBillVote],
    *,
    as_of: datetime,
    exclude_bill_id: int,
) -> list[PriorBillVote]:
    return [
        v
        for v in vendor_votes
        if v.bill_id != exclude_bill_id and v.created < as_of
    ]


def _predict(
    mode: str,
    project_id: Optional[int],
    priors: Sequence[PriorBillVote],
) -> ModePrediction:
    """BillCodingSuggester's history ladder over in-memory priors (memo rung excluded):
    a given project, else the vendor's dominant project; vendor x project history,
    then vendor-wide history, else none."""
    output_project = project_id
    lookup_project = project_id
    if lookup_project is None:
        proj_hist = _count_project_votes(priors)
        if proj_hist.top_scc_id is not None:  # field holds the top project id here
            lookup_project = proj_hist.top_scc_id
            output_project = lookup_project

    def top3(counts: HistoryVoteCounts) -> list[int]:
        return counts.ranked_scc_ids[:3] if counts.total_count >= MIN_PRIOR_BILLS else []

    def predicted(counts: HistoryVoteCounts, source: str) -> ModePrediction:
        return ModePrediction(
            mode=mode,
            predicted_scc_id=counts.top_scc_id,
            predicted_project_id=output_project,
            source=source,
            raw_score=_smoothed_share(counts.top_count, counts.total_count),
            top3_scc_ids=top3(counts),
            prior_bill_count=len(priors),
        )

    if lookup_project is not None:
        hist = _count_votes(priors, project_id=lookup_project)
        if hist.top_scc_id is not None:
            return predicted(hist, "vendor_project_history")

    wide = _count_votes(priors, project_id=None)
    if wide.top_scc_id is not None:
        return predicted(wide, "vendor_history")
    return ModePrediction(
        mode=mode,
        predicted_scc_id=None,
        predicted_project_id=output_project,
        source="none",
        raw_score=None,
        top3_scc_ids=top3(wide),
        prior_bill_count=len(priors),
    )


def predict_mode_a(bill: BillRow, priors: Sequence[PriorBillVote]) -> ModePrediction:
    """Mode A: the Bill's labelled project is given (predicted when it has none)."""
    return _predict("A", bill.label_project_id, priors)


def predict_mode_b(bill: BillRow, priors: Sequence[PriorBillVote]) -> ModePrediction:
    """Mode B: the project is always predicted."""
    return _predict("B", None, priors)


def score_bills_rolling(bills: Sequence[BillRow]) -> list[ScoredBill]:
    vendor_accum: dict[int, list[PriorBillVote]] = defaultdict(list)
    out: list[ScoredBill] = []

    global_order = sorted(bills, key=lambda b: (b.created, b.bill_id))
    for bill in global_order:
        priors = priors_before(
            vendor_accum[bill.vendor_id],
            as_of=bill.created,
            exclude_bill_id=bill.bill_id,
        )
        mode_a = predict_mode_a(bill, priors)
        mode_b = predict_mode_b(bill, priors)
        out.append(ScoredBill(bill=bill, mode_a=mode_a, mode_b=mode_b))
        vendor_accum[bill.vendor_id].append(
            PriorBillVote(
                bill_id=bill.bill_id,
                created=bill.created,
                dominant_scc_id=bill.label_scc_id,
                dominant_project_id=bill.label_project_id,
            )
        )
    return out


def derive_bill_origin(bill: BillRow) -> str:
    """U-639 §8 origin precedence (first match wins)."""
    if bill.intake_source == "bill_folder":
        return "sharepoint_folder"
    if bill.source_email_message_id is not None:
        return "email"
    if bill.qbo_staging_created is not None:
        return "qbo_pull"
    if bill.creator_is_agent:
        return "agent"
    return "person"


def load_bills_from_db() -> list[BillRow]:
    rows: list[BillRow] = []
    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(_LOAD_SQL)
            for row in cur.fetchall():
                qbo_created = row.QboStagingCreated
                rows.append(
                    BillRow(
                        bill_id=int(row.BillId),
                        vendor_id=int(row.VendorId),
                        created=_parse_dt(row.CreatedDatetime),
                        label_scc_id=int(row.DominantSubCostCodeId),
                        label_project_id=(
                            int(row.DominantProjectId)
                            if row.DominantProjectId is not None
                            else None
                        ),
                        total_abs_amount=Decimal(
                            str(
                                row.TotalAbsAmount
                                if row.TotalAbsAmount is not None
                                else 0
                            )
                        ),
                        mixed_code=bool(row.MixedCode),
                        intake_source=(
                            str(row.IntakeSource)
                            if row.IntakeSource is not None
                            else None
                        ),
                        source_email_message_id=(
                            int(row.SourceEmailMessageId)
                            if row.SourceEmailMessageId
                            is not None
                            else None
                        ),
                        creator_is_agent=bool(
                            row.CreatorIsAgent
                        ),
                        qbo_staging_created=(
                            _parse_dt(qbo_created)
                            if qbo_created is not None
                            else None
                        ),
                    )
                )
        finally:
            conn.rollback()
    return rows


@dataclass
class CalibrationMap:
    """Score-bin edges and per-bin calibrated confidence (observed accuracy)."""

    bin_edges: list[float]
    bin_confidence: list[Optional[float]]

    def to_json(self) -> dict[str, Any]:
        return {
            "bin_edges": self.bin_edges,
            "bin_confidence": [
                float(c) if c is not None else None for c in self.bin_confidence
            ],
            "model_version": MODEL_VERSION,
        }

    def calibrate(self, raw_score: Optional[Decimal]) -> Optional[Decimal]:
        """Bin i holds the scores in (upper[i-1], upper[i]], where upper[i] =
        bin_edges[i + 1] (the bin's largest fitted score). Below the first edge
        -> first bin; above the last -> last bin."""
        if raw_score is None:
            return None
        if not self.bin_confidence:
            return None
        x = float(raw_score)
        idx = len(self.bin_confidence) - 1
        for i, upper in enumerate(self.bin_edges[1:]):
            if x <= upper:
                idx = i
                break
        val = self.bin_confidence[idx]
        return Decimal(str(val)) if val is not None else None


def fit_calibration(
    scored: Sequence[ScoredBill],
    *,
    mode: str,
    since: date,
    until: date,
) -> CalibrationMap:
    points: list[tuple[float, int]] = []
    for sb in scored:
        d = _date_only(sb.bill.created)
        if d < since or d > until:
            continue
        pred = sb.mode_a if mode == "A" else sb.mode_b
        if pred.raw_score is None or pred.predicted_scc_id is None:
            continue
        correct = 1 if pred.predicted_scc_id == sb.bill.label_scc_id else 0
        points.append((float(pred.raw_score), correct))

    if not points:
        return CalibrationMap([0.0, 1.0], [None])

    by_score: dict[float, list[int]] = defaultdict(list)
    for score, correct in points:
        by_score[score].append(correct)

    # Greedy over DISTINCT scores: a score value is never split across bins,
    # and every bin is non-empty. A bin closes once it holds >= ceil(n / 10)
    # observations; the last bin takes whatever remains.
    sorted_scores = sorted(by_score.keys())
    target = -(-len(points) // DECILE_COUNT)
    bins: list[list[float]] = []
    chunk: list[float] = []
    count = 0
    for s in sorted_scores:
        chunk.append(s)
        count += len(by_score[s])
        if count >= target and len(bins) < DECILE_COUNT - 1:
            bins.append(chunk)
            chunk, count = [], 0
    if chunk:
        bins.append(chunk)

    edges = [bins[0][0]]
    confidences: list[Optional[float]] = []
    for chunk in bins:
        edges.append(chunk[-1])
        all_c = sum(c for s in chunk for c in by_score[s])
        all_n = sum(len(by_score[s]) for s in chunk)
        confidences.append(all_c / all_n)
    return CalibrationMap(edges, confidences)


@dataclass
class ModeMetrics:
    coverage: Decimal
    top1_bill_accuracy: Optional[Decimal]
    top1_dollar_accuracy: Optional[Decimal]
    top3_bill_accuracy: Optional[Decimal]
    top3_dollar_accuracy: Optional[Decimal]
    project_accuracy: Optional[Decimal]
    reliability: list[tuple[int, Optional[Decimal], Optional[Decimal]]]
    cold_start_count: int
    cold_start_with_suggestion: int
    mixed_code_count: int
    mixed_code_top1_accuracy: Optional[Decimal]
    top_error_vendor_ids: list[int]
    verdict_trained_model: str
    verdict_prefill: str
    verdict_prefill_low_confidence_share: Optional[Decimal]
    verdict_m4: str
    verdict_embeddings: str


def _weighted_accuracy(
    pairs: Sequence[tuple[bool, Decimal]],
) -> Optional[Decimal]:
    if not pairs:
        return None
    num = sum(Decimal("1") if ok else Decimal("0") for ok, _ in pairs)
    den = Decimal(len(pairs))
    return num / den


def _dollar_accuracy(
    pairs: Sequence[tuple[bool, Decimal]],
) -> Optional[Decimal]:
    if not pairs:
        return None
    total = sum(amt for _, amt in pairs)
    if total == 0:
        return None
    hit = sum(amt for ok, amt in pairs if ok)
    return hit / total


def compute_mode_metrics(
    scored: Sequence[ScoredBill],
    *,
    mode: str,
    eval_since: date,
    eval_until: date,
    calibration: CalibrationMap,
) -> ModeMetrics:
    eval_rows: list[ScoredBill] = []
    for sb in scored:
        d = _date_only(sb.bill.created)
        if eval_since <= d <= eval_until:
            eval_rows.append(sb)

    top1_pairs: list[tuple[bool, Decimal]] = []
    top3_pairs: list[tuple[bool, Decimal]] = []
    project_pairs: list[tuple[bool, Decimal]] = []
    with_suggestion = 0
    cold_start = 0
    cold_with = 0
    mixed_count = 0
    mixed_top1: list[tuple[bool, Decimal]] = []
    errors_by_vendor: dict[int, int] = defaultdict(int)
    cal_conf_buckets: dict[int, list[tuple[bool, Decimal, Decimal]]] = defaultdict(
        list
    )
    low_conf = 0
    with_cal = 0

    for sb in eval_rows:
        pred = sb.mode_a if mode == "A" else sb.mode_b
        if pred.prior_bill_count < MIN_PRIOR_BILLS:
            cold_start += 1
        if pred.predicted_scc_id is not None:
            with_suggestion += 1
            if pred.prior_bill_count < MIN_PRIOR_BILLS:
                cold_with += 1
        amt = sb.bill.total_abs_amount
        if pred.predicted_scc_id is not None:
            ok1 = pred.predicted_scc_id == sb.bill.label_scc_id
            top1_pairs.append((ok1, amt))
            if not ok1:
                errors_by_vendor[sb.bill.vendor_id] += 1
            ok3 = sb.bill.label_scc_id in pred.top3_scc_ids
            top3_pairs.append((ok3, amt))
            cal = calibration.calibrate(pred.raw_score)
            if cal is not None:
                with_cal += 1
                if cal < THRESHOLD_PREFILL_LOW_CONFIDENCE:
                    low_conf += 1
                bin_idx = min(
                    DECILE_COUNT - 1,
                    int(float(cal) * DECILE_COUNT),
                )
                cal_conf_buckets[bin_idx].append((ok1, amt, cal))
        if mode == "B" and pred.predicted_project_id is not None:
            project_pairs.append(
                (
                    pred.predicted_project_id == sb.bill.label_project_id,
                    amt,
                )
            )
        if sb.bill.mixed_code:
            mixed_count += 1
            if pred.predicted_scc_id is not None:
                mixed_top1.append(
                    (pred.predicted_scc_id == sb.bill.label_scc_id, amt)
                )

    n = len(eval_rows)
    coverage = Decimal(with_suggestion) / Decimal(n) if n else Decimal("0")
    top1 = _weighted_accuracy(top1_pairs)
    top1_d = _dollar_accuracy(top1_pairs)
    top3 = _weighted_accuracy(top3_pairs)
    top3_d = _dollar_accuracy(top3_pairs)
    proj_acc = _weighted_accuracy(project_pairs) if mode == "B" else None

    reliability: list[tuple[int, Optional[Decimal], Optional[Decimal]]] = []
    for dec in range(DECILE_COUNT):
        chunk = cal_conf_buckets.get(dec, [])
        mean_cal: Optional[Decimal] = None
        if chunk:
            mean_cal = sum(row[2] for row in chunk) / Decimal(len(chunk))
        reliability.append(
            (
                dec + 1,
                _weighted_accuracy([(ok, amt) for ok, amt, _ in chunk]),
                mean_cal,
            )
        )

    top_vendors = sorted(errors_by_vendor.items(), key=lambda x: (-x[1], x[0]))[:20]
    verdict_tm = "n/a"
    if top1 is not None:
        if top1 >= THRESHOLD_MODE_A_TOP1_TRAINED_MODEL:
            verdict_tm = (
                f"Mode {mode} top-1 {top1:.1%} >= {THRESHOLD_MODE_A_TOP1_TRAINED_MODEL:.0%}: "
                "trained model (D5) not indicated by accuracy alone"
            )
        else:
            verdict_tm = (
                f"Mode {mode} top-1 {top1:.1%} < {THRESHOLD_MODE_A_TOP1_TRAINED_MODEL:.0%}: "
                "review 50 misses per D5 before pursuing a trained model"
            )

    low_share = (
        Decimal(low_conf) / Decimal(with_cal) if with_cal else None
    )
    verdict_prefill = (
        f"{low_share:.1%} of suggested Bills fall below {THRESHOLD_PREFILL_LOW_CONFIDENCE:.0%} "
        "calibrated confidence (style as low-confidence per D6)"
        if low_share is not None
        else "no suggestions to assess pre-fill styling"
    )

    return ModeMetrics(
        coverage=coverage,
        top1_bill_accuracy=top1,
        top1_dollar_accuracy=top1_d,
        top3_bill_accuracy=top3,
        top3_dollar_accuracy=top3_d,
        project_accuracy=proj_acc,
        reliability=reliability,
        cold_start_count=cold_start,
        cold_start_with_suggestion=cold_with,
        mixed_code_count=mixed_count,
        mixed_code_top1_accuracy=_weighted_accuracy(mixed_top1),
        top_error_vendor_ids=[vid for vid, _ in top_vendors],
        verdict_trained_model=verdict_tm,
        verdict_prefill=verdict_prefill,
        verdict_prefill_low_confidence_share=low_share,
        verdict_m4="M4 text suggester not in v1 harness; rule not evaluated",
        verdict_embeddings="Embeddings (D7) not in v1 harness; rule not evaluated",
    )


def predict_with_suggester(
    suggester: BillCodingSuggester,
    bill: BillRow,
    *,
    mode: str,
) -> ModePrediction:
    """The live BillCodingSuggester (production ladder) at the Bill's own cutoff."""
    suggestion = suggester.suggest(
        vendor_id=bill.vendor_id,
        project_id=bill.label_project_id if mode == "A" else None,
        memo=None,
        as_of=bill.created,
        exclude_bill_id=bill.bill_id,
    )
    return ModePrediction(
        mode=mode,
        predicted_scc_id=suggestion.sub_cost_code_id,
        predicted_project_id=suggestion.project_id,
        source=suggestion.source,
        raw_score=suggestion.raw_score,
        top3_scc_ids=[],
        prior_bill_count=-1,
    )


def _raw_scores_equal(
    a: Optional[Decimal], b: Optional[Decimal]
) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if a == b:
        return True
    return round(a, 4) == round(b, 4)


def _preds_equal(a: ModePrediction, b: ModePrediction) -> bool:
    if a.predicted_scc_id != b.predicted_scc_id:
        return False
    if a.source != b.source:
        return False
    return _raw_scores_equal(a.raw_score, b.raw_score)


def validate_windows(
    cal_since: date,
    cal_until: date,
    eval_since: date,
    eval_until: date,
) -> None:
    if cal_since > cal_until:
        raise SystemExit(
            "U-653b: calibration window is reversed (--cal-since after --cal-until)."
        )
    if eval_since > eval_until:
        raise SystemExit(
            "U-653b: evaluation window is reversed (--eval-since after --eval-until)."
        )
    if cal_until >= eval_since:
        raise SystemExit(
            "U-653b: calibration must finish strictly before evaluation starts "
            f"(cal-until {cal_until} must be < eval-since {eval_since}; "
            f"cal {cal_since}..{cal_until}, eval {eval_since}..{eval_until})."
        )


def _vendor_consistency_share(votes: Sequence[PriorBillVote]) -> Optional[Decimal]:
    wide = _count_votes(votes, project_id=None)
    if wide.total_count == 0:
        return None
    return Decimal(wide.top_count) / Decimal(wide.total_count)


def _consistency_bucket(share: Decimal) -> str:
    if share >= Decimal("0.9"):
        return ">=90%"
    if share >= Decimal("0.7"):
        return "70-90%"
    if share >= Decimal("0.5"):
        return "50-70%"
    return "<50%"


@dataclass
class BreakdownMetrics:
    vendor_consistency: dict[str, tuple[int, Optional[Decimal]]]
    origin: dict[str, tuple[int, Optional[Decimal]]]
    origin_note: str


def compute_breakdown_metrics(
    scored: Sequence[ScoredBill],
    *,
    mode: str,
    eval_since: date,
    eval_until: date,
    all_bills: Sequence[BillRow],
) -> BreakdownMetrics:
    vendor_votes: dict[int, list[PriorBillVote]] = defaultdict(list)
    for b in all_bills:
        vendor_votes[b.vendor_id].append(
            PriorBillVote(
                bill_id=b.bill_id,
                created=b.created,
                dominant_scc_id=b.label_scc_id,
                dominant_project_id=b.label_project_id,
            )
        )

    vc_buckets: dict[str, list[tuple[bool, Decimal]]] = defaultdict(list)
    origin_buckets: dict[str, list[tuple[bool, Decimal]]] = defaultdict(list)

    for sb in scored:
        d = _date_only(sb.bill.created)
        if not (eval_since <= d <= eval_until):
            continue
        pred = sb.mode_a if mode == "A" else sb.mode_b
        if pred.predicted_scc_id is None:
            continue
        ok = pred.predicted_scc_id == sb.bill.label_scc_id
        amt = sb.bill.total_abs_amount
        priors = priors_before(
            vendor_votes[sb.bill.vendor_id],
            as_of=sb.bill.created,
            exclude_bill_id=sb.bill.bill_id,
        )
        share = _vendor_consistency_share(priors)
        if share is not None:
            vc_buckets[_consistency_bucket(share)].append((ok, amt))
        origin_key = derive_bill_origin(sb.bill)
        origin_buckets[origin_key].append((ok, amt))

    vc_out = {
        k: (len(v), _weighted_accuracy(v))
        for k, v in sorted(vc_buckets.items())
    }
    origin_out = {
        k: (len(v), _weighted_accuracy(v))
        for k, v in sorted(origin_buckets.items())
    }
    # The loader has one query and no fallback: every provenance column exists
    # whenever it succeeds, so all-NULL provenance is a real 'person', never
    # 'unavailable'.
    origin_note = (
        "Origin from §8 precedence (IntakeSource, email, QBO pull timing, agent creator)."
    )
    return BreakdownMetrics(
        vendor_consistency=vc_out,
        origin=origin_out,
        origin_note=origin_note,
    )


def verify_sample_against_procs(
    scored: Sequence[ScoredBill],
    *,
    sample_size: int,
    suggester: Optional[BillCodingSuggester] = None,
) -> None:
    if sample_size <= 0:
        return
    # The repo is the history service, so the orchestration under test is the
    # production ladder while reads stay unscoped (the CLI is a system caller).
    suggester = suggester or BillCodingSuggester(
        coding_suggestion_service=CodingSuggestionRepository()
    )
    step = max(1, len(scored) // sample_size)
    sample = [scored[i] for i in range(0, len(scored), step)][:sample_size]

    for sb in sample:
        bill = sb.bill
        proc_a = predict_with_suggester(suggester, bill, mode="A")
        if not _preds_equal(sb.mode_a, proc_a):
            raise RuntimeError(
                f"Mode A mismatch BillId={bill.bill_id} vendor={bill.vendor_id} "
                f"memory=({sb.mode_a.predicted_scc_id},{sb.mode_a.source},{sb.mode_a.raw_score}) "
                f"proc=({proc_a.predicted_scc_id},{proc_a.source},{proc_a.raw_score})"
            )
        proc_b = predict_with_suggester(suggester, bill, mode="B")
        if sb.mode_b.predicted_project_id != proc_b.predicted_project_id:
            raise RuntimeError(
                f"Mode B project mismatch BillId={bill.bill_id} "
                f"memory={sb.mode_b.predicted_project_id} proc={proc_b.predicted_project_id}"
            )
        if not _preds_equal(sb.mode_b, proc_b):
            raise RuntimeError(
                f"Mode B mismatch BillId={bill.bill_id} vendor={bill.vendor_id} "
                f"memory=({sb.mode_b.predicted_scc_id},{sb.mode_b.source},{sb.mode_b.raw_score}) "
                f"proc=({proc_b.predicted_scc_id},{proc_b.source},{proc_b.raw_score})"
            )


def write_outputs(
    *,
    out_dir: Path,
    stamp: str,
    scored: Sequence[ScoredBill],
    cal_a: CalibrationMap,
    cal_b: CalibrationMap,
    metrics_a: ModeMetrics,
    metrics_b: ModeMetrics,
    breakdown_a: BreakdownMetrics,
    breakdown_b: BreakdownMetrics,
    cal_since: date,
    cal_until: date,
    eval_since: date,
    eval_until: date,
    bill_count: int,
) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"u653b_report_{stamp}.md"
    csv_path = out_dir / f"u653b_predictions_{stamp}.csv"
    cal_path = out_dir / f"u653b_calibration_{stamp}.json"

    cal_json = {
        "mode_a": cal_a.to_json(),
        "mode_b": cal_b.to_json(),
        "calibration_window": {"since": str(cal_since), "until": str(cal_until)},
        "evaluation_window": {"since": str(eval_since), "until": str(eval_until)},
    }
    cal_path.write_text(json.dumps(cal_json, indent=2, sort_keys=True), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "BillId",
                "VendorId",
                "mode",
                "predicted_scc_id",
                "label_scc_id",
                "raw_score",
                "calibrated_confidence",
                "correct",
            ]
        )
        for sb in scored:
            d = _date_only(sb.bill.created)
            if not (eval_since <= d <= eval_until):
                continue
            for mode_key, pred, cal in (
                ("A", sb.mode_a, cal_a),
                ("B", sb.mode_b, cal_b),
            ):
                cal_conf = cal.calibrate(pred.raw_score)
                correct = ""
                if pred.predicted_scc_id is not None:
                    correct = (
                        "1"
                        if pred.predicted_scc_id == sb.bill.label_scc_id
                        else "0"
                    )
                writer.writerow(
                    [
                        sb.bill.bill_id,
                        sb.bill.vendor_id,
                        mode_key,
                        pred.predicted_scc_id if pred.predicted_scc_id is not None else "",
                        sb.bill.label_scc_id,
                        str(pred.raw_score) if pred.raw_score is not None else "",
                        str(cal_conf) if cal_conf is not None else "",
                        correct,
                    ]
                )

    def _fmt_pct(v: Optional[Decimal]) -> str:
        return f"{v:.2%}" if v is not None else "n/a"

    lines = [
        f"# U-653b backtest report ({stamp})",
        "",
        f"- ModelVersion: `{MODEL_VERSION}`",
        f"- Bills loaded: {bill_count}",
        f"- Calibration window: {cal_since} .. {cal_until}",
        f"- Evaluation window: {eval_since} .. {eval_until}",
        "- Memo rung: **excluded** from v1 (history suggester only).",
        "- Known residual: labels are current DB codes, not point-in-time (§10.2).",
        "",
        "## Mode A (project given = dominant project on the Bill)",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Coverage | {_fmt_pct(metrics_a.coverage)} |",
        f"| Top-1 accuracy (Bill-weighted) | {_fmt_pct(metrics_a.top1_bill_accuracy)} |",
        f"| Top-1 accuracy (dollar-weighted) | {_fmt_pct(metrics_a.top1_dollar_accuracy)} |",
        f"| Top-3 accuracy (Bill-weighted) | {_fmt_pct(metrics_a.top3_bill_accuracy)} |",
        f"| Top-3 accuracy (dollar-weighted) | {_fmt_pct(metrics_a.top3_dollar_accuracy)} |",
        f"| Cold-start Bills (< {MIN_PRIOR_BILLS} priors) | {metrics_a.cold_start_count} |",
        f"| Mixed-code Bills | {metrics_a.mixed_code_count} |",
        f"| Mixed-code top-1 accuracy | {_fmt_pct(metrics_a.mixed_code_top1_accuracy)} |",
        "",
        "### Reliability (evaluation, calibrated-confidence decile → accuracy)",
        "",
        "| Decile | Mean calibrated conf | Observed accuracy |",
        "|--------|---------------------|-------------------|",
    ]
    for dec, acc, mean_conf in metrics_a.reliability:
        lines.append(
            f"| {dec} | {_fmt_pct(mean_conf)} | {_fmt_pct(acc)} |"
        )
    lines.extend(
        [
            "",
            f"Top error vendors (ids): {', '.join(str(v) for v in metrics_a.top_error_vendor_ids) or 'none'}",
            "",
            "### §10.5 verdicts (Mode A, history only)",
            "",
            f"- Trained model (D5): {metrics_a.verdict_trained_model}",
            f"- Pre-fill styling (D6): {metrics_a.verdict_prefill}",
            f"- M4 cost: {metrics_a.verdict_m4}",
            f"- Embeddings: {metrics_a.verdict_embeddings}",
            "",
            "### Vendor consistency breakdown (Mode A, top-1)",
            "",
            "| Bucket | Bills | Top-1 accuracy |",
            "|--------|-------|----------------|",
        ]
    )
    for bucket, (cnt, acc) in breakdown_a.vendor_consistency.items():
        lines.append(f"| {bucket} | {cnt} | {_fmt_pct(acc)} |")
    lines.extend(
        [
            "",
            f"### Origin breakdown (Mode A) — {breakdown_a.origin_note}",
            "",
            "| Origin | Bills | Top-1 accuracy |",
            "|--------|-------|----------------|",
        ]
    )
    for origin, (cnt, acc) in breakdown_a.origin.items():
        lines.append(f"| {origin} | {cnt} | {_fmt_pct(acc)} |")
    lines.extend(
        [
            "",
            "## Mode B (predict project + SCC)",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Project top-1 accuracy | {_fmt_pct(metrics_b.project_accuracy)} |",
            f"| Coverage | {_fmt_pct(metrics_b.coverage)} |",
            f"| Top-1 accuracy (Bill-weighted) | {_fmt_pct(metrics_b.top1_bill_accuracy)} |",
            f"| Top-1 accuracy (dollar-weighted) | {_fmt_pct(metrics_b.top1_dollar_accuracy)} |",
            f"| Top-3 accuracy (Bill-weighted) | {_fmt_pct(metrics_b.top3_bill_accuracy)} |",
            f"| Mixed-code Bills | {metrics_b.mixed_code_count} |",
            f"| Mixed-code top-1 accuracy | {_fmt_pct(metrics_b.mixed_code_top1_accuracy)} |",
            "",
            "### Reliability (Mode B, evaluation, calibrated-confidence decile → accuracy)",
            "",
            "| Decile | Mean calibrated conf | Observed accuracy |",
            "|--------|---------------------|-------------------|",
        ]
    )
    for dec, acc, mean_conf in metrics_b.reliability:
        lines.append(f"| {dec} | {_fmt_pct(mean_conf)} | {_fmt_pct(acc)} |")
    lines.extend(
        [
            "",
            f"Top error vendors (ids): {', '.join(str(v) for v in metrics_b.top_error_vendor_ids) or 'none'}",
            "",
            "### Vendor consistency breakdown (Mode B, top-1)",
            "",
            "| Bucket | Bills | Top-1 accuracy |",
            "|--------|-------|----------------|",
        ]
    )
    for bucket, (cnt, acc) in breakdown_b.vendor_consistency.items():
        lines.append(f"| {bucket} | {cnt} | {_fmt_pct(acc)} |")
    lines.extend(
        [
            "",
            f"### Origin breakdown (Mode B) — {breakdown_b.origin_note}",
            "",
            "| Origin | Bills | Top-1 accuracy |",
            "|--------|-------|----------------|",
        ]
    )
    for origin, (cnt, acc) in breakdown_b.origin.items():
        lines.append(f"| {origin} | {cnt} | {_fmt_pct(acc)} |")
    lines.extend(
        [
            "",
            f"- Trained model (D5): {metrics_b.verdict_trained_model}",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path, csv_path, cal_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="U-653b read-only bill history backtest (U-639 §10)."
    )
    ap.add_argument(
        "--cal-since",
        type=date.fromisoformat,
        default=DEFAULT_CAL_SINCE,
        help="Calibration window start (inclusive, date)",
    )
    ap.add_argument(
        "--cal-until",
        type=date.fromisoformat,
        default=DEFAULT_CAL_UNTIL,
        help="Calibration window end (inclusive, date)",
    )
    ap.add_argument(
        "--eval-since",
        "--since",
        type=date.fromisoformat,
        default=DEFAULT_EVAL_SINCE,
        dest="eval_since",
        help="Evaluation window start (inclusive)",
    )
    ap.add_argument(
        "--eval-until",
        "--until",
        type=date.fromisoformat,
        default=DEFAULT_EVAL_UNTIL,
        dest="eval_until",
        help="Evaluation window end (inclusive)",
    )
    ap.add_argument(
        "--verify-sample",
        type=int,
        default=200,
        help="After scoring, compare this many Bills to live history procs (0=skip)",
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("docs/design/u653b"),
        help="Directory for report, CSV, and calibration JSON",
    )
    args = ap.parse_args(argv)

    validate_windows(
        args.cal_since,
        args.cal_until,
        args.eval_since,
        args.eval_until,
    )

    bills = load_bills_from_db()
    scored = score_bills_rolling(bills)

    if args.verify_sample:
        verify_sample_against_procs(scored, sample_size=args.verify_sample)

    cal_a = fit_calibration(
        scored, mode="A", since=args.cal_since, until=args.cal_until
    )
    cal_b = fit_calibration(
        scored, mode="B", since=args.cal_since, until=args.cal_until
    )
    metrics_a = compute_mode_metrics(
        scored,
        mode="A",
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        calibration=cal_a,
    )
    metrics_b = compute_mode_metrics(
        scored,
        mode="B",
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        calibration=cal_b,
    )
    breakdown_a = compute_breakdown_metrics(
        scored,
        mode="A",
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        all_bills=bills,
    )
    breakdown_b = compute_breakdown_metrics(
        scored,
        mode="B",
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        all_bills=bills,
    )

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    report_path, csv_path, cal_path = write_outputs(
        out_dir=args.out_dir,
        stamp=stamp,
        scored=scored,
        cal_a=cal_a,
        cal_b=cal_b,
        metrics_a=metrics_a,
        metrics_b=metrics_b,
        breakdown_a=breakdown_a,
        breakdown_b=breakdown_b,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        bill_count=len(bills),
    )

    print(
        f"U-653b: {len(bills)} bills scored; "
        f"Mode A eval top-1={metrics_a.top1_bill_accuracy} coverage={metrics_a.coverage}; "
        f"report={report_path.name}"
    )
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
