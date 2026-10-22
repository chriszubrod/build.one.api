"""
U-714: read-only rolling-origin backtest for expense line coding suggestions (U-715 sizing).

Loads completed coded expense lines in SELECT-only batches (rollback after read), replays
vendor history as-of each line's ExpenseDate (minus a coding-lag buffer), and compares:
  - engine: production CodingSuggestionEngine ladder (hints + as-of vendor modal SCC)
  - bill_style: U-653b vendor×project / vendor-wide history with smoothed-share confidence

Each suggester is evaluated with memo/description signals ON and OFF (leakage probe).

Usage (repo root; prod read-only — EM only):
  ./.venv/bin/python -m scripts.u714_expense_coding_backtest
  ./.venv/bin/python -m scripts.u714_expense_coding_backtest --out-dir docs/design/u714/results
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from bisect import bisect_left, bisect_right, insort
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

from entities.coding_suggestion.business.hint_extractor import extract_hints
from scripts.sync_helper import assert_cli_system_admin
from scripts.u653b_backtest import (
    BillRow,
    CalibrationMap,
    DECILE_COUNT,
    MIN_PRIOR_BILLS,
    PriorBillVote,
    _weighted_accuracy,
    predict_mode_b,
    validate_windows,
)
from shared.database import get_connection

MODEL_VERSION = "u714-expense-v1"
_PROJECT_CODE_CONFIDENCE = Decimal("0.95")
CONFIDENT_WRONG_THRESHOLDS = (Decimal("0.60"), Decimal("0.70"), Decimal("0.85"))

# Default: 6-month evaluation ending today (2026-10-22), calibration immediately before.
DEFAULT_EVAL_UNTIL = date(2026, 10, 22)
DEFAULT_EVAL_SINCE = date(2026, 4, 23)
DEFAULT_CAL_UNTIL = date(2026, 4, 22)
DEFAULT_CAL_SINCE = date(2025, 10, 23)
DEFAULT_CODING_LAG_DAYS = 7

# Segment keys always emitted in metrics (manual may be n=0 when all lines are QBO-pulled).
_METRICS_SEGMENT_ORDER = (
    "all",
    "qbo_pulled",
    "manual",
    "card",
    "vendor_has_history_yes",
    "vendor_has_history_no",
)

_LOAD_LINES_SQL = """
SELECT
    eli.[Id] AS [LineId],
    eli.[ExpenseId],
    e.[VendorId],
    e.[ExpenseDate],
    e.[CreatedDatetime],
    e.[StatusDatetime],
    eli.[SubCostCodeId] AS [LabelSccId],
    eli.[ProjectId] AS [LabelProjectId],
    -- QBO identity lives on dbo.Expense.QboId (U-238a); the old qbo.PurchaseExpense
    -- mapping table was retired by U-354, so there is no second source to consult.
    CASE
        WHEN e.[QboId] IS NOT NULL AND LTRIM(RTRIM(e.[QboId])) <> '' THEN 1
        ELSE 0
    END AS [IsQboPulled],
    0 AS [QboPulledViaMappingOnly],
    qp.[PaymentType]
FROM dbo.[ExpenseLineItem] eli
INNER JOIN dbo.[Expense] e ON e.[Id] = eli.[ExpenseId]
INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = eli.[SubCostCodeId]
LEFT JOIN qbo.[Purchase] qp
    ON qp.[QboId] = e.[QboId] AND qp.[RealmId] = e.[RealmId]
WHERE e.[IsDraft] = 0
  AND eli.[IsDraft] = 0
  AND eli.[SubCostCodeId] IS NOT NULL
  AND ISNULL(scc.[IsHoldingCode], 0) = 0
ORDER BY e.[CreatedDatetime], eli.[Id]
"""

_LOAD_MEMO_SQL = """
SELECT eli.[Id] AS [LineId], e.[Memo], eli.[Description]
FROM dbo.[ExpenseLineItem] eli
INNER JOIN dbo.[Expense] e ON e.[Id] = eli.[ExpenseId]
INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = eli.[SubCostCodeId]
WHERE e.[IsDraft] = 0
  AND eli.[IsDraft] = 0
  AND eli.[SubCostCodeId] IS NOT NULL
  AND ISNULL(scc.[IsHoldingCode], 0) = 0
"""

_LOAD_PROJECTS_SQL = """
SELECT [Id], [Abbreviation], [Name], [Status]
FROM dbo.[Project]
"""

_LOAD_SCC_SQL = """
SELECT [Id], [Number], [Name], [Aliases], [IsHoldingCode], [QboId]
FROM dbo.[SubCostCode]
"""

_ATTACHMENT_STATS_SQL = """
SELECT
    a.[ExtractionStatus],
    COUNT(*) AS [Cnt],
    SUM(CASE WHEN att_hash.[AttachmentId] IS NOT NULL THEN 1 ELSE 0 END) AS [WithTokenHash],
    SUM(CASE WHEN a.[ExtractionStatus] = N'completed' THEN ISNULL(att_hash.[TokenCount], 0) ELSE NULL END) AS [TokenCountSum],
    COUNT(CASE WHEN a.[ExtractionStatus] = N'completed' AND att_hash.[TokenCount] IS NOT NULL THEN 1 END) AS [CompletedWithTokenCount]
FROM dbo.[ExpenseLineItemAttachment] elia
INNER JOIN dbo.[Attachment] a ON a.[Id] = elia.[AttachmentId]
LEFT JOIN dbo.[AttachmentTextTokenHash] att_hash ON att_hash.[AttachmentId] = a.[Id]
GROUP BY a.[ExtractionStatus]
"""

_TOKEN_COUNT_BUCKETS_SQL = """
SELECT
    CASE
        WHEN att_hash.[TokenCount] IS NULL THEN N'no_hash'
        WHEN att_hash.[TokenCount] < 50 THEN N'lt_50'
        WHEN att_hash.[TokenCount] < 200 THEN N'50_199'
        WHEN att_hash.[TokenCount] < 1000 THEN N'200_999'
        ELSE N'gte_1000'
    END AS [Bucket],
    COUNT(*) AS [Cnt]
FROM dbo.[ExpenseLineItemAttachment] elia
INNER JOIN dbo.[Attachment] a ON a.[Id] = elia.[AttachmentId]
LEFT JOIN dbo.[AttachmentTextTokenHash] att_hash ON att_hash.[AttachmentId] = a.[Id]
WHERE a.[ExtractionStatus] = N'completed'
GROUP BY
    CASE
        WHEN att_hash.[TokenCount] IS NULL THEN N'no_hash'
        WHEN att_hash.[TokenCount] < 50 THEN N'lt_50'
        WHEN att_hash.[TokenCount] < 200 THEN N'50_199'
        WHEN att_hash.[TokenCount] < 1000 THEN N'200_999'
        ELSE N'gte_1000'
    END
"""


@dataclass(frozen=True)
class ExpenseLineRow:
    line_id: int
    expense_id: int
    vendor_id: int
    expense_date: datetime
    created: datetime
    label_scc_id: int
    label_project_id: Optional[int]
    is_qbo_pulled: bool
    qbo_pulled_via_mapping_only: bool
    payment_type: Optional[str]
    memo: Optional[str] = field(repr=False, compare=False)
    description: Optional[str] = field(repr=False, compare=False)

    def segment_qbo(self) -> str:
        return "qbo_pulled" if self.is_qbo_pulled else "manual"

    def segment_card(self) -> bool:
        if not self.is_qbo_pulled or not self.payment_type:
            return False
        return self.payment_type.strip().lower() == "creditcard"


@dataclass(frozen=True)
class LinePrediction:
    suggester: str
    use_memo: bool
    predicted_scc_id: Optional[int]
    predicted_project_id: Optional[int]
    source: str
    raw_score: Optional[Decimal]
    confidence: Optional[Decimal]
    prior_line_count: int


@dataclass(frozen=True)
class ExpensePriorVote:
    line_id: int
    expense_id: int
    expense_date: datetime
    created: datetime
    dominant_scc_id: int
    dominant_project_id: Optional[int]


def _prior_vote_sort_key(v: ExpensePriorVote) -> tuple[date, datetime, int]:
    return (_date_only(v.expense_date), v.created, v.line_id)


def _prior_cutoff_date(eval_expense_date: date, *, coding_lag_days: int) -> date:
    return eval_expense_date - timedelta(days=coding_lag_days)


def priors_as_of_expense_date(
    vendor_votes: Sequence[ExpensePriorVote],
    *,
    eval_expense_date: date,
    coding_lag_days: int,
    exclude_line_id: int,
    exclude_expense_id: int,
) -> list[ExpensePriorVote]:
    """
    Prior lines on earlier purchases count toward vendor history.

    Inclusion: prior ExpenseDate <= (eval ExpenseDate − coding_lag_days), except when
    coding_lag_days=0 same-calendar-day priors are excluded (strictly earlier dates only).
    Same-expense sibling lines never count (by expense id).
    """
    if not vendor_votes:
        return []
    if coding_lag_days == 0:
        end = bisect_left(vendor_votes, eval_expense_date, key=lambda v: _date_only(v.expense_date))
    else:
        cutoff = _prior_cutoff_date(eval_expense_date, coding_lag_days=coding_lag_days)
        end = bisect_right(vendor_votes, cutoff, key=lambda v: _date_only(v.expense_date))
    return [
        v
        for v in vendor_votes[:end]
        if v.line_id != exclude_line_id and v.expense_id != exclude_expense_id
    ]


def _insert_vendor_prior(votes: list[ExpensePriorVote], vote: ExpensePriorVote) -> None:
    insort(votes, vote, key=_prior_vote_sort_key)


@dataclass
class ScoredLine:
    line: ExpenseLineRow
    predictions: dict[tuple[str, bool], LinePrediction]
    vendor_has_history: bool


@dataclass(frozen=True)
class ProjectRef:
    id: int
    abbreviation: Optional[str]
    name: str
    status: str


@dataclass(frozen=True)
class SubCostCodeRef:
    id: int
    number: str
    name: str
    aliases: Optional[str]
    is_holding: bool
    qbo_id: Optional[str]


@dataclass
class ReferenceData:
    projects: list[ProjectRef]
    sub_cost_codes: list[SubCostCodeRef]
    holding_ids: frozenset[int]

    @classmethod
    def from_rows(
        cls,
        projects: Sequence[Any],
        sccs: Sequence[Any],
    ) -> ReferenceData:
        proj_refs = [
            ProjectRef(
                id=int(r.Id),
                abbreviation=str(r.Abbreviation) if r.Abbreviation is not None else None,
                name=str(r.Name),
                status=str(r.Status),
            )
            for r in projects
        ]
        scc_refs = [
            SubCostCodeRef(
                id=int(r.Id),
                number=str(r.Number),
                name=str(r.Name),
                aliases=str(r.Aliases) if r.Aliases is not None else None,
                is_holding=bool(r.IsHoldingCode),
                qbo_id=str(r.QboId) if r.QboId is not None else None,
            )
            for r in sccs
        ]
        holding = frozenset(s.id for s in scc_refs if s.is_holding)
        return cls(projects=proj_refs, sub_cost_codes=scc_refs, holding_ids=holding)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, datetime.min.time())
    raise TypeError(f"expected datetime, got {type(value)}")


def _date_only(value: datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _normalize_scc_hint(hint: str) -> Optional[str]:
    hint = hint.strip()
    if "." not in hint:
        return None
    left, _, right = hint.partition(".")
    if len(right) == 1:
        right = "0" + right
    return f"{left}.{right}"


def find_project_for_address(
    refs: ReferenceData,
    address_hint: str,
) -> list[tuple[int, Decimal]]:
    """In-memory top of FindProjectForInvoice (address strategies only)."""
    address_norm = address_hint.strip()
    if not address_norm:
        return []

    first_space = address_norm.find(" ")
    second_space = (
        address_norm.find(" ", first_space + 1) if first_space >= 0 else -1
    )
    first_token = address_norm[:first_space] if first_space >= 0 else address_norm
    two_tokens = (
        address_norm[:second_space] if second_space >= 0 else address_norm
    )

    address_lower = address_norm.lower()
    two_lower = two_tokens.lower() if two_tokens else ""
    first_lower = first_token.lower() if first_token else ""

    candidates: dict[int, Decimal] = {}
    name_by_id = {p.id: p.name for p in refs.projects}

    def add(pid: int, conf: Decimal) -> None:
        prev = candidates.get(pid)
        if prev is None or conf > prev:
            candidates[pid] = conf

    for p in refs.projects:
        name_lower = p.name.lower()
        if address_norm and address_lower in name_lower:
            add(p.id, Decimal("0.85"))
        if two_tokens and two_lower in name_lower:
            add(p.id, Decimal("0.75"))
        if first_token and first_lower in name_lower:
            add(p.id, Decimal("0.65"))

    ranked = sorted(
        candidates.items(),
        key=lambda x: (-x[1], name_by_id.get(x[0], "")),
    )
    return ranked[:5]


def find_sub_cost_code_for_hint(
    refs: ReferenceData,
    hint: str,
) -> list[tuple[int, Decimal]]:
    """In-memory top of FindSubCostCodeForReply."""
    hint_norm = hint.strip()
    if not hint_norm:
        return []
    padded = _normalize_scc_hint(hint_norm)
    hint_lower = hint_norm.lower()

    candidates: dict[int, Decimal] = {}
    number_by_id = {s.id: s.number for s in refs.sub_cost_codes}

    def add(scc_id: int, conf: Decimal) -> None:
        prev = candidates.get(scc_id)
        if prev is None or conf > prev:
            candidates[scc_id] = conf

    for s in refs.sub_cost_codes:
        if s.number == hint_norm:
            add(s.id, Decimal("1.00"))
        if padded and s.number == padded:
            add(s.id, Decimal("0.95"))
        if s.aliases:
            for part in s.aliases.split("|"):
                if part.strip() == hint_norm:
                    add(s.id, Decimal("0.90"))
            if hint_lower in s.aliases.lower():
                add(s.id, Decimal("0.80"))
        if hint_lower in s.name.lower():
            add(s.id, Decimal("0.75"))

    ranked = sorted(
        candidates.items(),
        key=lambda x: (-x[1], number_by_id.get(x[0], "")),
    )
    return ranked[:3]


def read_project_by_abbreviation(refs: ReferenceData, abbrev: str) -> Optional[int]:
    key = abbrev.strip().lower()
    for p in refs.projects:
        if p.status != "active":
            continue
        if p.abbreviation and p.abbreviation.lower() == key:
            return p.id
    return None


def vendor_dominant_scc_as_of(
    priors: Sequence[PriorBillVote],
    holding_ids: frozenset[int],
) -> Optional[tuple[int, int, int]]:
    """Modal SCC over prior lines (>= MIN_PRIOR_BILLS), excluding holding codes."""
    counts: dict[int, int] = defaultdict(int)
    for v in priors:
        if v.dominant_scc_id in holding_ids:
            continue
        counts[v.dominant_scc_id] += 1
    if not counts:
        return None
    total = sum(counts.values())
    if total < MIN_PRIOR_BILLS:
        return None
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    top_scc, top_count = ranked[0]
    return top_scc, top_count, total


def memo_text_for_line(line: ExpenseLineRow, *, use_memo: bool) -> Optional[str]:
    if not use_memo:
        return None
    return line.memo


def predict_engine(
    line: ExpenseLineRow,
    priors: Sequence[PriorBillVote],
    refs: ReferenceData,
    *,
    use_memo: bool,
) -> LinePrediction:
    """CodingSuggestionEngine.suggest_for_item without DB or QBO resolver calls."""
    memo_text = memo_text_for_line(line, use_memo=use_memo)
    hints = extract_hints(memo_text)

    project_id: Optional[int] = None
    project_confidence: Optional[Decimal] = None
    project_source: Optional[str] = None

    if hints.get("project_hint"):
        pid = read_project_by_abbreviation(refs, hints["project_hint"])
        if pid is not None:
            project_id = pid
            project_confidence = _PROJECT_CODE_CONFIDENCE
            project_source = "code"

    if project_id is None and hints.get("address_hint"):
        ranked = find_project_for_address(refs, hints["address_hint"])
        if ranked:
            project_id = ranked[0][0]
            project_confidence = ranked[0][1]
            project_source = "address"

    sub_cost_code_id: Optional[int] = None
    scc_confidence: Optional[Decimal] = None
    scc_source: Optional[str] = None

    if hints.get("scc_hint"):
        ranked = find_sub_cost_code_for_hint(refs, hints["scc_hint"])
        if ranked:
            sub_cost_code_id = ranked[0][0]
            scc_confidence = ranked[0][1]
            scc_source = "scc_shorthand"

    if sub_cost_code_id is None:
        hist = vendor_dominant_scc_as_of(priors, refs.holding_ids)
        if hist is not None:
            sub_cost_code_id, top_count, total = hist
            scc_confidence = Decimal(top_count) / Decimal(total)
            scc_source = "vendor_history"

    if sub_cost_code_id is not None and sub_cost_code_id in refs.holding_ids:
        sub_cost_code_id = None
        scc_confidence = None
        scc_source = None

    if sub_cost_code_id is not None:
        scc_ref = next((s for s in refs.sub_cost_codes if s.id == sub_cost_code_id), None)
        if scc_ref is None or not scc_ref.qbo_id:
            sub_cost_code_id = None
            scc_confidence = None
            scc_source = None

    sources: list[str] = []
    confidences: list[Decimal] = []
    if project_source:
        sources.append(project_source)
    if scc_source:
        sources.append(scc_source)
    if project_confidence is not None:
        confidences.append(project_confidence)
    if scc_confidence is not None:
        confidences.append(scc_confidence)

    if project_id is None and sub_cost_code_id is None:
        return LinePrediction(
            suggester="engine",
            use_memo=use_memo,
            predicted_scc_id=None,
            predicted_project_id=None,
            source="none",
            raw_score=None,
            confidence=None,
            prior_line_count=len(priors),
        )

    overall = min(confidences) if confidences else None
    raw = scc_confidence if scc_confidence is not None else overall
    return LinePrediction(
        suggester="engine",
        use_memo=use_memo,
        predicted_scc_id=sub_cost_code_id,
        predicted_project_id=project_id,
        source="+".join(sources) if sources else "none",
        raw_score=raw,
        confidence=overall,
        prior_line_count=len(priors),
    )


def predict_bill_style(
    line: ExpenseLineRow,
    priors: Sequence[PriorBillVote],
    *,
    use_memo: bool,
) -> LinePrediction:
    """U-653b Mode B history ladder at expense-line grain (memo ignored)."""
    stub = BillRow(
        bill_id=line.line_id,
        vendor_id=line.vendor_id,
        created=line.created,
        label_scc_id=line.label_scc_id,
        label_project_id=line.label_project_id,
        total_abs_amount=Decimal("0"),
        mixed_code=False,
    )
    mp = predict_mode_b(stub, priors)
    return LinePrediction(
        suggester="bill_style",
        use_memo=use_memo,
        predicted_scc_id=mp.predicted_scc_id,
        predicted_project_id=mp.predicted_project_id,
        source=mp.source,
        raw_score=mp.raw_score,
        confidence=mp.raw_score,
        prior_line_count=len(priors),
    )


def prior_vote_from_line(line: ExpenseLineRow) -> ExpensePriorVote:
    return ExpensePriorVote(
        line_id=line.line_id,
        expense_id=line.expense_id,
        expense_date=line.expense_date,
        created=line.created,
        dominant_scc_id=line.label_scc_id,
        dominant_project_id=line.label_project_id,
    )


def _prior_votes_for_predict(
    priors: Sequence[ExpensePriorVote],
) -> list[PriorBillVote]:
    return [
        PriorBillVote(
            bill_id=v.line_id,
            created=v.expense_date,
            dominant_scc_id=v.dominant_scc_id,
            dominant_project_id=v.dominant_project_id,
        )
        for v in priors
    ]


def score_lines_rolling(
    lines: Sequence[ExpenseLineRow],
    refs: ReferenceData,
    *,
    coding_lag_days: int = DEFAULT_CODING_LAG_DAYS,
) -> list[ScoredLine]:
    vendor_accum: dict[int, list[ExpensePriorVote]] = defaultdict(list)
    out: list[ScoredLine] = []

    for line in sorted(
        lines, key=lambda r: (_date_only(r.expense_date), r.created, r.line_id)
    ):
        priors = priors_as_of_expense_date(
            vendor_accum[line.vendor_id],
            eval_expense_date=_date_only(line.expense_date),
            coding_lag_days=coding_lag_days,
            exclude_line_id=line.line_id,
            exclude_expense_id=line.expense_id,
        )
        prior_bills = _prior_votes_for_predict(priors)
        has_hist = vendor_has_history(prior_bills)
        preds: dict[tuple[str, bool], LinePrediction] = {}
        for use_memo in (True, False):
            preds[("engine", use_memo)] = predict_engine(
                line, prior_bills, refs, use_memo=use_memo
            )
            preds[("bill_style", use_memo)] = predict_bill_style(
                line, prior_bills, use_memo=use_memo
            )
        out.append(
            ScoredLine(
                line=line,
                predictions=preds,
                vendor_has_history=has_hist,
            )
        )
        _insert_vendor_prior(
            vendor_accum[line.vendor_id], prior_vote_from_line(line)
        )
    return out


def vendor_has_history(priors: Sequence[PriorBillVote]) -> bool:
    return len(priors) >= MIN_PRIOR_BILLS


def segment_keys(line: ExpenseLineRow, *, vendor_has_history: bool) -> list[str]:
    keys = ["all", line.segment_qbo()]
    if line.segment_card():
        keys.append("card")
    keys.append(
        "vendor_has_history_yes" if vendor_has_history else "vendor_has_history_no"
    )
    return keys


def fit_calibration_from_scored(
    scored: Sequence[ScoredLine],
    *,
    suggester: str,
    use_memo: bool,
    since: date,
    until: date,
) -> CalibrationMap:
    points: list[tuple[float, int]] = []
    for sl in scored:
        d = _date_only(sl.line.expense_date)
        if d < since or d > until:
            continue
        pred = sl.predictions[(suggester, use_memo)]
        if pred.raw_score is None or pred.predicted_scc_id is None:
            continue
        correct = 1 if pred.predicted_scc_id == sl.line.label_scc_id else 0
        points.append((float(pred.raw_score), correct))

    if not points:
        return CalibrationMap([0.0, 1.0], [None])

    by_score: dict[float, list[int]] = defaultdict(list)
    for score, correct in points:
        by_score[score].append(correct)

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
class SegmentMetrics:
    segment: str
    n_eval: int
    coverage: Optional[Decimal]
    scc_top1_accuracy: Optional[Decimal]
    project_coverage: Optional[Decimal]
    project_accuracy: Optional[Decimal]
    both_correct: Optional[Decimal]
    reliability: list[tuple[int, int, Optional[Decimal], Optional[Decimal]]]
    confident_wrong: dict[str, Optional[Decimal]]


def compute_segment_metrics(
    scored: Sequence[ScoredLine],
    *,
    suggester: str,
    use_memo: bool,
    eval_since: date,
    eval_until: date,
    calibration: CalibrationMap,
) -> dict[str, SegmentMetrics]:
    buckets: dict[str, list[ScoredLine]] = defaultdict(list)

    for sl in scored:
        d = _date_only(sl.line.expense_date)
        if not (eval_since <= d <= eval_until):
            continue
        for seg in segment_keys(sl.line, vendor_has_history=sl.vendor_has_history):
            buckets[seg].append(sl)

    out: dict[str, SegmentMetrics] = {}
    for seg, rows in sorted(buckets.items()):
        out[seg] = _metrics_for_rows(
            seg,
            rows,
            suggester=suggester,
            use_memo=use_memo,
            calibration=calibration,
        )
    for seg in _METRICS_SEGMENT_ORDER:
        if seg not in out:
            out[seg] = SegmentMetrics(
                segment=seg,
                n_eval=0,
                coverage=None,
                scc_top1_accuracy=None,
                project_coverage=None,
                project_accuracy=None,
                both_correct=None,
                reliability=[],
                confident_wrong={str(t): None for t in CONFIDENT_WRONG_THRESHOLDS},
            )
    return out


def _metrics_for_rows(
    segment: str,
    rows: Sequence[ScoredLine],
    *,
    suggester: str,
    use_memo: bool,
    calibration: CalibrationMap,
) -> SegmentMetrics:
    n = len(rows)
    covered = 0
    project_predicted = 0
    scc_pairs: list[tuple[bool, Decimal]] = []
    proj_pairs: list[tuple[bool, Decimal]] = []
    both_pairs: list[tuple[bool, Decimal]] = []
    cal_buckets: dict[int, list[tuple[bool, Decimal, Decimal]]] = defaultdict(list)
    conf_wrong: dict[Decimal, list[bool]] = {t: [] for t in CONFIDENT_WRONG_THRESHOLDS}

    for sl in rows:
        pred = sl.predictions[(suggester, use_memo)]
        if pred.predicted_scc_id is None:
            continue
        covered += 1
        scc_ok = pred.predicted_scc_id == sl.line.label_scc_id
        scc_pairs.append((scc_ok, Decimal("1")))

        proj_ok = pred.predicted_project_id == sl.line.label_project_id
        both_pairs.append((scc_ok and proj_ok, Decimal("1")))
        if pred.predicted_project_id is not None:
            project_predicted += 1
            proj_pairs.append((proj_ok, Decimal("1")))

        cal = calibration.calibrate(pred.raw_score)
        conf = cal if cal is not None else pred.confidence
        if conf is not None:
            bin_idx = min(DECILE_COUNT - 1, int(float(conf) * DECILE_COUNT))
            cal_buckets[bin_idx].append((scc_ok, Decimal("1"), conf))
            for thr in CONFIDENT_WRONG_THRESHOLDS:
                if conf >= thr:
                    conf_wrong[thr].append(not scc_ok)

    coverage = Decimal(covered) / Decimal(n) if n else None
    project_coverage = (
        Decimal(project_predicted) / Decimal(covered) if covered else None
    )
    reliability: list[tuple[int, int, Optional[Decimal], Optional[Decimal]]] = []
    for dec in range(DECILE_COUNT):
        chunk = cal_buckets.get(dec, [])
        mean_cal: Optional[Decimal] = None
        if chunk:
            mean_cal = sum(row[2] for row in chunk) / Decimal(len(chunk))
        reliability.append(
            (
                dec + 1,
                len(chunk),
                mean_cal,
                _weighted_accuracy([(ok, wt) for ok, wt, _ in chunk]),
            )
        )

    cw_out: dict[str, Optional[Decimal]] = {}
    for thr in CONFIDENT_WRONG_THRESHOLDS:
        flags = conf_wrong[thr]
        cw_out[str(thr)] = (
            Decimal(sum(1 for x in flags if x)) / Decimal(len(flags))
            if flags
            else None
        )

    return SegmentMetrics(
        segment=segment,
        n_eval=n,
        coverage=coverage,
        scc_top1_accuracy=_weighted_accuracy(scc_pairs),
        project_coverage=project_coverage,
        project_accuracy=_weighted_accuracy(proj_pairs),
        both_correct=_weighted_accuracy(both_pairs),
        reliability=reliability,
        confident_wrong=cw_out,
    )


def compute_all_metrics(
    scored: Sequence[ScoredLine],
    *,
    eval_since: date,
    eval_until: date,
    cal_since: date,
    cal_until: date,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for suggester in ("engine", "bill_style"):
        for use_memo in (True, False):
            cal = fit_calibration_from_scored(
                scored,
                suggester=suggester,
                use_memo=use_memo,
                since=cal_since,
                until=cal_until,
            )
            segs = compute_segment_metrics(
                scored,
                suggester=suggester,
                use_memo=use_memo,
                eval_since=eval_since,
                eval_until=eval_until,
                calibration=cal,
            )
            key = f"{suggester}_memo_{'on' if use_memo else 'off'}"
            result[key] = {
                "calibration": {
                    "bin_edges": cal.bin_edges,
                    "bin_confidence": [
                        float(c) if c is not None else None for c in cal.bin_confidence
                    ],
                    "model_version": MODEL_VERSION,
                },
                "segments": {
                    name: {
                        "n_eval": m.n_eval,
                        "coverage": float(m.coverage) if m.coverage is not None else None,
                        "scc_top1_accuracy": (
                            float(m.scc_top1_accuracy)
                            if m.scc_top1_accuracy is not None
                            else None
                        ),
                        "project_coverage": (
                            float(m.project_coverage)
                            if m.project_coverage is not None
                            else None
                        ),
                        "project_accuracy": (
                            float(m.project_accuracy)
                            if m.project_accuracy is not None
                            else None
                        ),
                        "both_correct": (
                            float(m.both_correct) if m.both_correct is not None else None
                        ),
                        "reliability": [
                            {
                                "decile": d,
                                "n": n_bin,
                                "mean_calibrated_confidence": (
                                    float(mean_cal) if mean_cal is not None else None
                                ),
                                "observed_accuracy": (
                                    float(acc) if acc is not None else None
                                ),
                            }
                            for d, n_bin, mean_cal, acc in m.reliability
                        ],
                        "confident_wrong_rate": m.confident_wrong,
                    }
                    for name, m in segs.items()
                },
            }
    return result


def _percentile_days(values: Sequence[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
    return ordered[idx]


def data_quality_metadata(
    *,
    status_datetimes: Sequence[datetime],
    created_datetimes: Sequence[datetime],
    qbo_segment_counts: tuple[int, int],
) -> dict[str, Any]:
    """StatusDatetime lag stats are diagnostic only — not used as the scoring clock."""
    lags = [
        (st - cr).total_seconds() / 86400.0
        for st, cr in zip(status_datetimes, created_datetimes, strict=True)
    ]
    qbo_n, manual_n = qbo_segment_counts
    return {
        "scoring_clock": {
            "field": "ExpenseDate",
            "coding_lag_days_default": DEFAULT_CODING_LAG_DAYS,
            "status_datetime_not_used_as_clock": True,
            "note": (
                "Expense.StatusDatetime was mass-stamped by U-357 (2026-09-16); "
                "Expense.CreatedDatetime clusters on initial import (2026-01-30). "
                "Neither is a reliable completion/import clock for rolling-origin history."
            ),
        },
        "data_quality_status_datetime_minus_created_days": {
            "p50": _percentile_days(lags, 0.50),
            "p90": _percentile_days(lags, 0.90),
            "n": len(lags),
            "label": "Backfill/import artifact — not used for prior inclusion",
        },
        "segment_counts_loaded": {
            "qbo_pulled": qbo_n,
            "manual": manual_n,
            "note": (
                "Prod corpus may be all QBO-pulled; manual segment still reported at n=0 in metrics."
            ),
        },
    }


def load_corpus_from_db() -> tuple[list[ExpenseLineRow], ReferenceData, dict[str, Any]]:
    lines: list[ExpenseLineRow] = []
    memo_by_line: dict[int, tuple[Optional[str], Optional[str]]] = {}
    projects: list[Any] = []
    sccs: list[Any] = []
    attachment_stats: list[Any] = []
    token_buckets: list[Any] = []
    qbo_reclassified_via_mapping = 0
    status_datetimes: list[datetime] = []
    created_datetimes: list[datetime] = []

    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            cur.execute(_LOAD_MEMO_SQL)
            for row in cur.fetchall():
                memo_by_line[int(row.LineId)] = (
                    str(row.Memo) if row.Memo is not None else None,
                    str(row.Description) if row.Description is not None else None,
                )

            cur.execute(_LOAD_LINES_SQL)
            for row in cur.fetchall():
                lid = int(row.LineId)
                memo, desc = memo_by_line.get(lid, (None, None))
                created = _parse_dt(row.CreatedDatetime)
                if row.StatusDatetime is not None:
                    status_datetimes.append(_parse_dt(row.StatusDatetime))
                    created_datetimes.append(created)
                via_map = bool(row.QboPulledViaMappingOnly)
                if via_map:
                    qbo_reclassified_via_mapping += 1
                lines.append(
                    ExpenseLineRow(
                        line_id=lid,
                        expense_id=int(row.ExpenseId),
                        vendor_id=int(row.VendorId),
                        expense_date=_parse_dt(row.ExpenseDate),
                        created=created,
                        label_scc_id=int(row.LabelSccId),
                        label_project_id=(
                            int(row.LabelProjectId)
                            if row.LabelProjectId is not None
                            else None
                        ),
                        is_qbo_pulled=bool(row.IsQboPulled),
                        qbo_pulled_via_mapping_only=via_map,
                        payment_type=(
                            str(row.PaymentType)
                            if row.PaymentType is not None
                            else None
                        ),
                        memo=memo,
                        description=desc,
                    )
                )

            cur.execute(_LOAD_PROJECTS_SQL)
            projects = cur.fetchall()
            cur.execute(_LOAD_SCC_SQL)
            sccs = cur.fetchall()
            cur.execute(_ATTACHMENT_STATS_SQL)
            attachment_stats = cur.fetchall()
            cur.execute(_TOKEN_COUNT_BUCKETS_SQL)
            token_buckets = cur.fetchall()
        finally:
            conn.rollback()

    refs = ReferenceData.from_rows(projects, sccs)
    att_payload = _attachment_stats_payload(attachment_stats, token_buckets)
    qbo_n = sum(1 for ln in lines if ln.segment_qbo() == "qbo_pulled")
    manual_n = len(lines) - qbo_n
    att_payload["run_metadata"] = {
        "qbo_pulled_reclassified_via_purchase_expense_mapping": (
            qbo_reclassified_via_mapping
        ),
        **data_quality_metadata(
            status_datetimes=status_datetimes,
            created_datetimes=created_datetimes,
            qbo_segment_counts=(qbo_n, manual_n),
        ),
    }
    return lines, refs, att_payload


def _attachment_stats_payload(
    rows: Sequence[Any],
    token_buckets: Sequence[Any],
) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    with_hash = 0
    completed_with_token = 0
    for row in rows:
        status = str(row.ExtractionStatus)
        cnt = int(row.Cnt)
        by_status[status] = by_status.get(status, 0) + cnt
        with_hash += int(row.WithTokenHash)
        completed_with_token += int(row.CompletedWithTokenCount)
    bucket_counts = {str(r.Bucket): int(r.Cnt) for r in token_buckets}
    length_note = (
        "Completed extractions: TokenCount from AttachmentTextTokenHash is a token-count "
        "proxy, not character length. Character/word length requires reading ExtractedTextBlobUrl."
    )
    return {
        "extraction_status_counts": by_status,
        "expense_attachments_with_token_hash": with_hash,
        "completed_extractions_with_token_count": completed_with_token,
        "completed_token_count_buckets": bucket_counts,
        "length_proxy_note": length_note,
    }


def _git_sha() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _json_default(value: Any) -> Any:
    """Decimal (metric arithmetic) and dates serialize as plain JSON numbers / ISO strings."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_outputs(
    *,
    out_dir: Path,
    stamp: str,
    scored: Sequence[ScoredLine],
    metrics: dict[str, Any],
    attachment_feasibility: dict[str, Any],
    cal_since: date,
    cal_until: date,
    eval_since: date,
    eval_until: date,
    line_count: int,
    git_sha: Optional[str],
    run_metadata: Optional[dict[str, Any]] = None,
    write_lines: bool = False,
    coding_lag_days: int = DEFAULT_CODING_LAG_DAYS,
) -> tuple[Path, Optional[Path], Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"u714_summary_{stamp}.md"
    csv_path: Optional[Path] = None
    if write_lines:
        csv_path = out_dir / f"u714_predictions_{stamp}.csv"
    json_path = out_dir / f"u714_metrics_{stamp}.json"
    att_path = out_dir / f"u714_attachment_feasibility_{stamp}.json"

    meta = {
        "model_version": MODEL_VERSION,
        "line_count": line_count,
        "calibration_window": {"since": str(cal_since), "until": str(cal_until)},
        "evaluation_window": {"since": str(eval_since), "until": str(eval_until)},
        "git_sha": git_sha,
        "rolling_origin": {
            "score_as_of": "Expense.ExpenseDate per line",
            "coding_lag_days": coding_lag_days,
            "prior_inclusion": (
                "Prior line counts when prior ExpenseDate <= (evaluated ExpenseDate "
                "− coding_lag_days); buffer 0 uses strictly earlier ExpenseDate. "
                "Same-expense lines never count. CreatedDatetime is tie-order only."
            ),
            "evaluation_window_filters": "ExpenseDate",
        },
        "run_metadata": run_metadata or {},
        "metrics": metrics,
    }
    json_path.write_text(json.dumps(meta, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    att_path.write_text(
        json.dumps(attachment_feasibility, indent=2, sort_keys=True, default=_json_default), encoding="utf-8"
    )

    if csv_path is not None:
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                [
                    "LineId",
                    "ExpenseId",
                    "VendorId",
                    "suggester",
                    "memo_on",
                    "predicted_scc_id",
                    "predicted_project_id",
                    "label_scc_id",
                    "label_project_id",
                    "raw_score",
                    "scc_correct",
                    "project_correct",
                ]
            )
            for sl in scored:
                d = _date_only(sl.line.expense_date)
                if not (eval_since <= d <= eval_until):
                    continue
                for (suggester, use_memo), pred in sl.predictions.items():
                    scc_correct = ""
                    proj_correct = ""
                    if pred.predicted_scc_id is not None:
                        scc_correct = (
                            "1"
                            if pred.predicted_scc_id == sl.line.label_scc_id
                            else "0"
                        )
                    if (
                        pred.predicted_project_id is not None
                        and sl.line.label_project_id is not None
                    ):
                        proj_correct = (
                            "1"
                            if pred.predicted_project_id == sl.line.label_project_id
                            else "0"
                        )
                    writer.writerow(
                        [
                            sl.line.line_id,
                            sl.line.expense_id,
                            sl.line.vendor_id,
                            suggester,
                            "1" if use_memo else "0",
                            pred.predicted_scc_id or "",
                            pred.predicted_project_id or "",
                            sl.line.label_scc_id,
                            sl.line.label_project_id or "",
                            str(pred.raw_score) if pred.raw_score is not None else "",
                            scc_correct,
                            proj_correct,
                        ]
                    )

    def fmt(v: Optional[float]) -> str:
        return f"{v:.2%}" if v is not None else "n/a"

    lines = [
        f"# U-714 expense coding backtest ({stamp})",
        "",
        f"- ModelVersion: `{MODEL_VERSION}`",
        f"- Lines loaded: {line_count}",
        f"- Git SHA: `{git_sha or 'unknown'}`",
        f"- Calibration: {cal_since} .. {cal_until}",
        f"- Evaluation: {eval_since} .. {eval_until} (filter: **ExpenseDate**)",
        "",
        "**Rolling-origin:** each line scored as of its **`ExpenseDate`**; vendor priors "
        f"are earlier lines with ExpenseDate at least **`{coding_lag_days}` days** before "
        "(same-expense siblings excluded). **`StatusDatetime` / `CreatedDatetime` are not "
        "used as the history clock** (U-357 backfill / import stamps). Labels reflect "
        "current coded state (U-653b residual).",
        "",
        "**Dataset note:** in production measurement, every completed expense line loaded "
        "for this backtest is **QBO-pulled**; the **`manual` segment is reported at n=0** "
        "when no manual rows fall in the evaluation window.",
        "",
        "**Memo probe:** `bill_style` ignores memo by design — memo on/off rows are "
        "identical for that suggester (not evidence of absent leakage). Only `engine` "
        "applies the `SubCostCode.QboId` writable filter.",
        "",
        "## Metrics (by suggester × memo × segment)",
        "",
    ]
    if run_metadata:
        lines.append("## Run metadata")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(run_metadata, indent=2, sort_keys=True, default=_json_default))
        lines.append("```")
        lines.append("")

    for key, block in sorted(metrics.items()):
        lines.append(f"### {key}")
        lines.append("")
        lines.append(
            "| Segment | N | Coverage | SCC top-1 | Proj cov | Project | Both |"
        )
        lines.append(
            "|---------|---|----------|-----------|----------|---------|------|"
        )
        for seg_name in _METRICS_SEGMENT_ORDER:
            seg = block["segments"][seg_name]
            lines.append(
                f"| {seg_name} | {seg['n_eval']} | {fmt(seg['coverage'])} | "
                f"{fmt(seg['scc_top1_accuracy'])} | "
                f"{fmt(seg.get('project_coverage'))} | "
                f"{fmt(seg['project_accuracy'])} | "
                f"{fmt(seg['both_correct'])} |"
            )
        lines.append("")

    lines.extend(
        [
            "## Attachment text feasibility (aggregates)",
            "",
            json.dumps(attachment_feasibility, indent=2, default=_json_default),
            "",
            "**Card segment:** `qbo.Purchase.PaymentType = 'CreditCard'` (QBO API values: Cash, Check, CreditCard).",
            "",
            "**Address matching:** in-memory subset of FindProjectForInvoice (address strategies only; no exact_name on hint).",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path, csv_path, json_path, att_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="U-714 expense coding backtest (read-only).")
    ap.add_argument("--cal-since", type=date.fromisoformat, default=DEFAULT_CAL_SINCE)
    ap.add_argument("--cal-until", type=date.fromisoformat, default=DEFAULT_CAL_UNTIL)
    ap.add_argument(
        "--eval-since",
        type=date.fromisoformat,
        default=DEFAULT_EVAL_SINCE,
    )
    ap.add_argument(
        "--eval-until",
        type=date.fromisoformat,
        default=DEFAULT_EVAL_UNTIL,
    )
    ap.add_argument(
        "--out-dir",
        type=Path,
        default=Path("docs/design/u714/results"),
    )
    ap.add_argument(
        "--write-lines",
        action="store_true",
        help="Write per-line predictions CSV (ids only; default off).",
    )
    ap.add_argument(
        "--coding-lag-days",
        type=int,
        default=DEFAULT_CODING_LAG_DAYS,
        help=(
            "Days between purchase (ExpenseDate) and coding; priors must be at least "
            "this many days earlier (default 7). Use 0 for strictly earlier ExpenseDate."
        ),
    )
    args = ap.parse_args(argv)
    if args.coding_lag_days < 0:
        ap.error("--coding-lag-days must be >= 0")

    validate_windows(
        args.cal_since,
        args.cal_until,
        args.eval_since,
        args.eval_until,
    )

    lines, refs, att = load_corpus_from_db()
    run_metadata = dict(att.pop("run_metadata", {}))
    scored = score_lines_rolling(lines, refs, coding_lag_days=args.coding_lag_days)
    metrics = compute_all_metrics(
        scored,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
    )

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    report_path, _, _, _ = write_outputs(
        out_dir=args.out_dir,
        stamp=stamp,
        scored=scored,
        metrics=metrics,
        attachment_feasibility=att,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        line_count=len(lines),
        git_sha=_git_sha(),
        run_metadata=run_metadata,
        write_lines=args.write_lines,
        coding_lag_days=args.coding_lag_days,
    )

    print(f"U-714: {len(lines)} lines scored; report={report_path.name}")
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
