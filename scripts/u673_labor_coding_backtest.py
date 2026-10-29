"""
U-673 Phase A: read-only rolling-origin backtest for ContractLabor line cost-code suggestions.

Label grain: dbo.ContractLaborLineItem on a ContractLabor in approved/completed (legacy
ready/billed accepted too) with a non-NULL, non-holding SubCostCodeId.

INPUT TEXT (leakage rule): apply_reviewer_decision REPLACES a line's Description on approval
with reviewer text that can carry the code label. So the input text is REBUILT from
dbo.TimeLog.Note (the work TimeLogs of the line's TimeEntry, filtered to the line's ProjectId, ordered by ClockIn,
joined with '; ' exactly like AggregateTimeEntryOnSubmit). The TimeEntry is
COALESCE(line.SourceTimeEntryId, ContractLabor.SourceTimeEntryId): lines a PM splits off in
PUT /{id}/bill carry no line-level SourceTimeEntryId (CreateContractLaborLineItem takes none), but
their ContractLabor does (one CL per TimeEntry since migration 009). Notes stay project-scoped; a
split-off line on a project the worker never clocked into gets no text (no cross-project fallback,
which would hand it another project's narrative). The stored ContractLaborLineItem.Description is
read ONLY by the leakage probe. Excel-era lines (neither line nor CL has a SourceTimeEntryId) have
no input text; they are a reported segment, not the headline.

Signals
  S0  vendor dominant SCC (>= 3 priors) and vendor x project variant (vendor fallback), as-of.
  S1  phrase precedent: IDF-weighted token overlap with earlier coded lines on the SAME project
      within N workdays (N = 3 / 7 / 14), weighted 1 / (1 + workday distance); score per SCC;
      confidence = top score share. Abstain -> S0 vendor x project fallback.
  S2  multi-activity: split the note into segments (';', newline, ',', ' and '), S1 per segment;
      >= 2 segments resolving to different SCCs => an N-way split with hours in proportion to
      segment counts.
  S3  label-over-number on the reply corpora (labor Review.Comments, bill Review.Comments).

As-of rule (coding lag, default 2 days): a prior line counts only when its LineDate <=
query LineDate - lag AND, when both have a coded-at time (first final, non-declined Review on the
ContractLabor), the prior was coded strictly before the query. Same ContractLabor never counts.

Outputs (ids / codes / counts / scores only): u673_report_<stamp>.md + .json under --out-dir; every
output passes assert_outputs_privacy_safe before the run reports success.

Usage (repo root; prod read-only - EM only):
  .venv/bin/python -m scripts.u673_labor_coding_backtest                 # dry-run: counts only
  .venv/bin/python -m scripts.u673_labor_coding_backtest --run --out-dir docs/design/u673
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from entities.coding_suggestion.business.similar_bills import (
    compute_idf_from_doc_token_sets,
    idf_weighted_jaccard,
    tokenize_bill_text,
)
from scripts.u653b_backtest import MIN_PRIOR_BILLS, validate_windows

MODEL_VERSION = "u673-labor-v1"
DEFAULT_EVAL_SINCE = date(2026, 6, 1)
DEFAULT_EVAL_UNTIL = date(2026, 10, 15)
DEFAULT_CAL_SINCE = date(2026, 6, 1)
DEFAULT_CAL_UNTIL = date(2026, 8, 15)
DEFAULT_CODING_LAG_DAYS = 2
WINDOWS_WORKDAYS = (3, 7, 14)
CONFIDENT_WRONG_THRESHOLDS = (Decimal("0.60"), Decimal("0.70"), Decimal("0.85"))
FIT_THRESHOLD_GRID = tuple(Decimal(x) / Decimal(100) for x in range(50, 100, 5))
MAX_CONFIDENT_WRONG = Decimal("0.05")
PHASE_B_MIN_LIFT = Decimal("0.15")
NAMED_SLICE = ("period_2026_09_16_30", date(2026, 9, 16), date(2026, 9, 30))
LABEL_MATCH_MIN = 0.5
MIN_SUPPORT = 20  # shown rows needed to fit a threshold (cal) or judge the Phase B bar (holdout)

# Labor narratives are full of filler the bill stop-list does not cover.
LABOR_STOP_WORDS = frozenset(
    "we our us i me my he she they them their his her you your this that these those "
    "today yesterday tomorrow day days morning afternoon all also some more rest "
    "work worked working did do done doing got get getting go went going help helped "
    "helping started start continue continued continuing finished finish finishing "
    "out up off over into onto then there here back still just again lot lots "
    "hours hour hrs hr time".split()
)

_SEGMENT_RE = re.compile(r";|\n|\r|,|\band\b", re.IGNORECASE)
_REPLY_RE = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,2})(?![\d.])\s*[-:]?\s*([A-Za-z][A-Za-z '&/\-]{1,60})?")

STATUSES_LABELLED = ("approved", "completed", "ready", "billed")

# ---------------------------------------------------------------------------
# SQL (SELECT only; every batch is rolled back)
# ---------------------------------------------------------------------------

_LINES_SQL = """
SELECT
    li.[Id] AS [LineId],
    li.[ContractLaborId],
    COALESCE(li.[LineDate], cl.[WorkDate]) AS [LineDate],
    li.[ProjectId],
    li.[SubCostCodeId] AS [LabelSccId],
    li.[Hours],
    li.[SourceTimeEntryId],
    cl.[SourceTimeEntryId] AS [ClSourceTimeEntryId],
    COALESCE(cl.[VendorId], cl.[BillVendorId]) AS [VendorId]
FROM dbo.[ContractLaborLineItem] li
INNER JOIN dbo.[ContractLabor] cl ON cl.[Id] = li.[ContractLaborId]
INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = li.[SubCostCodeId]
WHERE cl.[Status] IN (N'approved', N'completed', N'ready', N'billed')
  AND li.[SubCostCodeId] IS NOT NULL
  AND ISNULL(scc.[IsHoldingCode], 0) = 0
"""

# Leakage probe ONLY.
_STORED_DESCRIPTION_SQL = """
SELECT li.[Id] AS [LineId], li.[Description]
FROM dbo.[ContractLaborLineItem] li
INNER JOIN dbo.[ContractLabor] cl ON cl.[Id] = li.[ContractLaborId]
WHERE cl.[Status] IN (N'approved', N'completed', N'ready', N'billed')
  AND li.[SubCostCodeId] IS NOT NULL
"""

_TIMELOG_NOTES_SQL = """
SELECT tl.[TimeEntryId], tl.[ProjectId], tl.[ClockIn], tl.[Note]
FROM dbo.[TimeLog] tl
WHERE (tl.[LogType] IS NULL OR tl.[LogType] = N'work')
  AND tl.[TimeEntryId] IN (
      SELECT li.[SourceTimeEntryId] FROM dbo.[ContractLaborLineItem] li
      WHERE li.[SourceTimeEntryId] IS NOT NULL
      UNION
      SELECT cl.[SourceTimeEntryId] FROM dbo.[ContractLabor] cl
      WHERE cl.[SourceTimeEntryId] IS NOT NULL)
"""

_CODED_AT_SQL = """
SELECT r.[ContractLaborId], MIN(r.[CreatedDatetime]) AS [CodedAt]
FROM dbo.[Review] r
INNER JOIN dbo.[ReviewStatus] rs ON rs.[Id] = r.[ReviewStatusId]
WHERE r.[ContractLaborId] IS NOT NULL AND rs.[IsFinal] = 1 AND rs.[IsDeclined] = 0
GROUP BY r.[ContractLaborId]
"""

_SCC_SQL = """
SELECT [Id], [Number], [Name], [Aliases]
FROM dbo.[SubCostCode]
"""

_LABOR_REPLIES_SQL = """
SELECT r.[Id] AS [ReviewId], r.[ContractLaborId] AS [ParentId], r.[Comments],
       r.[CreatedDatetime], cl.[WorkDate]
FROM dbo.[Review] r
INNER JOIN dbo.[ContractLabor] cl ON cl.[Id] = r.[ContractLaborId]
WHERE r.[ContractLaborId] IS NOT NULL AND r.[Comments] IS NOT NULL
"""

_BILL_REPLIES_SQL = """
SELECT r.[Id] AS [ReviewId], r.[BillId] AS [ParentId], r.[Comments], r.[CreatedDatetime]
FROM dbo.[Review] r
WHERE r.[BillId] IS NOT NULL AND r.[Comments] IS NOT NULL
"""

_BILL_REPLY_LABELS_SQL = """
SELECT DISTINCT bli.[BillId] AS [ParentId], bli.[SubCostCodeId]
FROM dbo.[BillLineItem] bli
WHERE bli.[SubCostCodeId] IS NOT NULL
  AND bli.[BillId] IN (SELECT r.[BillId] FROM dbo.[Review] r
                       WHERE r.[BillId] IS NOT NULL AND r.[Comments] IS NOT NULL)
"""

_POPULATION_COUNTS_SQL = """
SELECT
    COUNT(*) AS [Lines],
    SUM(CASE WHEN COALESCE(li.[SourceTimeEntryId], cl.[SourceTimeEntryId]) IS NOT NULL THEN 1 ELSE 0 END) AS [TimeLogSourced],
    SUM(CASE WHEN COALESCE(li.[SourceTimeEntryId], cl.[SourceTimeEntryId]) IS NULL THEN 1 ELSE 0 END) AS [ExcelEra],
    SUM(CASE WHEN li.[SourceTimeEntryId] IS NULL AND cl.[SourceTimeEntryId] IS NOT NULL THEN 1 ELSE 0 END) AS [SplitOff],
    COUNT(DISTINCT li.[ProjectId]) AS [Projects],
    COUNT(DISTINCT li.[ContractLaborId]) AS [ContractLabors],
    MIN(COALESCE(li.[LineDate], cl.[WorkDate])) AS [MinDate],
    MAX(COALESCE(li.[LineDate], cl.[WorkDate])) AS [MaxDate]
FROM dbo.[ContractLaborLineItem] li
INNER JOIN dbo.[ContractLabor] cl ON cl.[Id] = li.[ContractLaborId]
INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = li.[SubCostCodeId]
WHERE cl.[Status] IN (N'approved', N'completed', N'ready', N'billed')
  AND li.[SubCostCodeId] IS NOT NULL
  AND ISNULL(scc.[IsHoldingCode], 0) = 0
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaborLine:
    line_id: int
    contract_labor_id: int
    line_date: date
    project_id: Optional[int]
    vendor_id: Optional[int]
    label_scc_id: int
    hours: Decimal
    source_time_entry_id: Optional[int]
    coded_at: Optional[datetime] = None
    cl_source_time_entry_id: Optional[int] = None
    # Free text: never serialized. note_text = rebuilt TimeLog notes (input);
    # stored_description = ContractLaborLineItem.Description (leakage probe only).
    note_text: Optional[str] = field(default=None, repr=False, compare=False)
    stored_description: Optional[str] = field(default=None, repr=False, compare=False)

    @property
    def time_entry_id(self) -> Optional[int]:
        """Line-level lineage, else the parent CL's (PM split-off lines carry only the latter)."""
        if self.source_time_entry_id is not None:
            return self.source_time_entry_id
        return self.cl_source_time_entry_id

    @property
    def is_timelog_sourced(self) -> bool:
        return self.time_entry_id is not None

    @property
    def group_key(self) -> tuple[int, Optional[int], date]:
        return (self.contract_labor_id, self.project_id, self.line_date)


@dataclass(frozen=True)
class SccRef:
    id: int
    number: str
    name: str
    aliases: Optional[str] = None


@dataclass(frozen=True)
class Prediction:
    ranked: tuple[int, ...]  # SCC ids, best first
    confidence: Optional[Decimal]
    source: str
    split: tuple[tuple[int, Decimal], ...] = ()  # (scc, share) when an N-way split is predicted

    @property
    def top1(self) -> Optional[int]:
        return self.ranked[0] if self.ranked else None


ABSTAIN = Prediction(ranked=(), confidence=None, source="none")


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def tokenize_labor_text(text: Optional[str]) -> frozenset[str]:
    """Bill tokenizer (U-691) minus labor filler words. No change to the shared tokenizer."""
    if not text:
        return frozenset()
    return frozenset(t for t in tokenize_bill_text(text) if t not in LABOR_STOP_WORDS)


def rebuild_note_text(logs: Iterable[tuple[datetime, Optional[str]]]) -> Optional[str]:
    """Mirror AggregateTimeEntryOnSubmit: trimmed non-empty notes ordered by ClockIn, '; '-joined."""
    parts = [
        n.strip() for _, n in sorted(logs, key=lambda x: x[0]) if n is not None and n.strip()
    ]
    return "; ".join(parts) if parts else None


def input_text(line: LaborLine, *, probe: bool) -> Optional[str]:
    """The ONLY place a signal reads text. Stored Description is reachable only with probe=True."""
    if probe:
        return line.stored_description
    return line.note_text


def split_segments(text: Optional[str]) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in _SEGMENT_RE.split(text) if s and s.strip()]


# ---------------------------------------------------------------------------
# As-of priors
# ---------------------------------------------------------------------------


def workdays_between(earlier: date, later: date) -> int:
    """Mon-Fri days in (earlier, later]; 0 when later <= earlier."""
    if later <= earlier:
        return 0
    full_weeks, rem = divmod((later - earlier).days, 7)
    n = full_weeks * 5
    d = earlier
    for _ in range(rem):
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def prior_is_visible(prior: LaborLine, query: LaborLine, *, coding_lag_days: int) -> bool:
    if prior.line_id == query.line_id or prior.contract_labor_id == query.contract_labor_id:
        return False
    if prior.line_date > query.line_date - timedelta(days=coding_lag_days):
        return False
    if coding_lag_days == 0 and prior.line_date >= query.line_date:
        return False
    if prior.coded_at is not None and query.coded_at is not None:
        if prior.coded_at >= query.coded_at:
            return False
    return True


def visible_priors(
    candidates: Sequence[LaborLine], query: LaborLine, *, coding_lag_days: int
) -> list[LaborLine]:
    return [p for p in candidates if prior_is_visible(p, query, coding_lag_days=coding_lag_days)]


# ---------------------------------------------------------------------------
# S0
# ---------------------------------------------------------------------------


def _dominant(priors: Sequence[LaborLine]) -> Optional[Prediction]:
    counts = Counter(p.label_scc_id for p in priors)
    total = sum(counts.values())
    if total < MIN_PRIOR_BILLS:
        return None
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return Prediction(
        ranked=tuple(s for s, _ in ranked[:3]),
        confidence=Decimal(ranked[0][1]) / Decimal(total),
        source="",
    )


def predict_s0_vendor(vendor_priors: Sequence[LaborLine]) -> Prediction:
    p = _dominant(vendor_priors)
    return ABSTAIN if p is None else Prediction(p.ranked, p.confidence, "s0_vendor")


def predict_s0_vendor_project(
    vendor_priors: Sequence[LaborLine], project_id: Optional[int]
) -> Prediction:
    vp = _dominant([p for p in vendor_priors if p.project_id == project_id])
    if vp is not None:
        return Prediction(vp.ranked, vp.confidence, "s0_vendor_project")
    return predict_s0_vendor(vendor_priors)


# ---------------------------------------------------------------------------
# S1 / S2
# ---------------------------------------------------------------------------


def s1_scores(
    query_tokens: frozenset[str],
    query_date: date,
    project_priors: Sequence[tuple[LaborLine, frozenset[str], float]],
    idf: dict[str, float],
    *,
    window_workdays: int,
) -> dict[int, float]:
    if not query_tokens:
        return {}
    wq = sum(idf.get(t, 0.0) for t in query_tokens)
    scores: dict[int, float] = defaultdict(float)
    for prior, toks, w_prior in project_priors:
        if not toks:
            continue
        if (query_date - prior.line_date).days > window_workdays * 2 + 4:
            continue  # cheap calendar prefilter; exact workday test below
        dist = workdays_between(prior.line_date, query_date)
        if dist > window_workdays:
            continue
        sim = idf_weighted_jaccard(query_tokens, wq, toks, w_prior, idf)
        if sim <= 0:
            continue
        scores[prior.label_scc_id] += sim / (1.0 + dist)
    return dict(scores)


def _ranked_from_scores(scores: Mapping[int, float]) -> tuple[tuple[int, ...], Optional[Decimal]]:
    total = sum(scores.values())
    if total <= 0:
        return (), None
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    conf = Decimal(str(round(ranked[0][1] / total, 6)))
    return tuple(s for s, _ in ranked), conf


def predict_s1(
    text: Optional[str],
    query_date: date,
    project_priors: Sequence[tuple[LaborLine, frozenset[str], float]],
    idf: dict[str, float],
    *,
    window_workdays: int,
    fallback: Prediction,
) -> Prediction:
    scores = s1_scores(
        tokenize_labor_text(text), query_date, project_priors, idf, window_workdays=window_workdays
    )
    ranked, conf = _ranked_from_scores(scores)
    if not ranked:
        return Prediction(fallback.ranked, fallback.confidence, f"fallback:{fallback.source}")
    return Prediction(ranked[:3], conf, "s1")


def predict_s1_s2(
    text: Optional[str],
    query_date: date,
    project_priors: Sequence[tuple[LaborLine, frozenset[str], float]],
    idf: dict[str, float],
    *,
    window_workdays: int,
    whole: Prediction,
) -> Prediction:
    """S1 per segment; >= 2 distinct resolved SCCs => split, else the whole-note S1 prediction."""
    resolved: list[tuple[int, Decimal]] = []
    for seg in split_segments(text):
        ranked, conf = _ranked_from_scores(
            s1_scores(
                tokenize_labor_text(seg), query_date, project_priors, idf,
                window_workdays=window_workdays,
            )
        )
        if ranked and conf is not None:
            resolved.append((ranked[0], conf))
    distinct = Counter(s for s, _ in resolved)
    if len(distinct) < 2:
        return whole
    n = Decimal(sum(distinct.values()))
    order = sorted(distinct.items(), key=lambda x: (-x[1], x[0]))
    split = tuple((s, Decimal(c) / n) for s, c in order)
    ranked = tuple(s for s, _ in order)
    for s in whole.ranked:
        if s not in ranked:
            ranked += (s,)
    return Prediction(ranked, min(c for _, c in resolved), "s2_split", split)


# ---------------------------------------------------------------------------
# Rolling scorer
# ---------------------------------------------------------------------------


def signal_keys() -> list[str]:
    keys = ["s0_vendor", "s0_vendor_project"]
    for n in WINDOWS_WORKDAYS:
        keys += [f"s1_w{n}", f"s1s2_w{n}"]
    return keys


@dataclass
class ScoredLine:
    line: LaborLine
    predictions: dict[str, Prediction]
    project_history_depth: int
    has_note: bool


def score_lines(
    lines: Sequence[LaborLine],
    *,
    coding_lag_days: int = DEFAULT_CODING_LAG_DAYS,
    probe: bool = False,
    score_since: Optional[date] = None,
) -> list[ScoredLine]:
    """Score every line (optionally only those dated >= score_since) against as-of priors."""
    texts = {ln.line_id: input_text(ln, probe=probe) for ln in lines}
    tokens = {lid: tokenize_labor_text(t) for lid, t in texts.items()}
    # Label-free IDF over every input document (unsupervised; documented limit).
    idf = compute_idf_from_doc_token_sets([t for t in tokens.values() if t])
    weights = {lid: sum(idf.get(t, 0.0) for t in toks) for lid, toks in tokens.items()}
    by_vendor: dict[Optional[int], list[LaborLine]] = defaultdict(list)
    by_project: dict[Optional[int], list[LaborLine]] = defaultdict(list)
    for ln in lines:
        by_vendor[ln.vendor_id].append(ln)
        by_project[ln.project_id].append(ln)

    out: list[ScoredLine] = []
    for q in sorted(lines, key=lambda r: (r.line_date, r.line_id)):
        if score_since is not None and q.line_date < score_since:
            continue
        vpri = visible_priors(by_vendor[q.vendor_id], q, coding_lag_days=coding_lag_days) if q.vendor_id is not None else []
        ppri = visible_priors(by_project[q.project_id], q, coding_lag_days=coding_lag_days) if q.project_id is not None else []
        ppri_tok = [(p, tokens[p.line_id], weights[p.line_id]) for p in ppri]
        preds: dict[str, Prediction] = {
            "s0_vendor": predict_s0_vendor(vpri),
            "s0_vendor_project": predict_s0_vendor_project(vpri, q.project_id),
        }
        text = texts[q.line_id]
        for n in WINDOWS_WORKDAYS:
            whole = predict_s1(
                text, q.line_date, ppri_tok, idf, window_workdays=n,
                fallback=preds["s0_vendor_project"],
            )
            preds[f"s1_w{n}"] = whole
            preds[f"s1s2_w{n}"] = predict_s1_s2(
                text, q.line_date, ppri_tok, idf, window_workdays=n, whole=whole
            )
        out.append(
            ScoredLine(
                line=q,
                predictions=preds,
                project_history_depth=len(ppri),
                has_note=bool(tokens[q.line_id]),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def depth_bucket(n: int) -> str:
    if n == 0:
        return "depth_0"
    if n < 10:
        return "depth_1_9"
    if n < 50:
        return "depth_10_49"
    return "depth_50_plus"


def segment_keys(sl: ScoredLine) -> list[str]:
    keys = ["all", "timelog" if sl.line.is_timelog_sourced else "excel"]
    if sl.line.is_timelog_sourced:
        keys.append("timelog_has_note" if sl.has_note else "timelog_empty_note")
        keys.append(f"timelog_{depth_bucket(sl.project_history_depth)}")
    _, s, u = NAMED_SLICE
    if s <= sl.line.line_date <= u:
        keys.append(NAMED_SLICE[0])
        if sl.line.is_timelog_sourced:
            keys.append(f"{NAMED_SLICE[0]}_timelog")
    return keys


def group_labels(scored: Sequence[ScoredLine]) -> dict[tuple, set[int]]:
    """Group truth: every line of a (ContractLabor, Project, LineDate), split-offs included.
    The ONE source used by line_correct (via metrics) and split_metrics."""
    g: dict[tuple, set[int]] = defaultdict(set)
    for sl in scored:
        g[sl.line.group_key].add(sl.line.label_scc_id)
    return dict(g)


def line_correct(pred: Prediction, label: int, group_labels: set[int]) -> bool:
    """Top-1 for a single-code prediction; for a split, label in the split set AND no spurious code."""
    if pred.split:
        codes = {s for s, _ in pred.split}
        return label in codes and codes <= group_labels
    return pred.top1 == label


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 6) if den else None


def metrics_for_rows(
    rows: Sequence[ScoredLine], key: str, groups: Mapping[tuple, set[int]]
) -> dict[str, Any]:
    n = len(rows)
    covered = top1 = top3 = 0
    hours_total = Decimal("0")
    hours_ok = Decimal("0")
    cw: dict[Decimal, list[bool]] = {t: [] for t in CONFIDENT_WRONG_THRESHOLDS}
    for sl in rows:
        pred = sl.predictions[key]
        h = sl.line.hours or Decimal("0")
        hours_total += h
        if pred.top1 is None:
            continue
        covered += 1
        ok = line_correct(pred, sl.line.label_scc_id, groups[sl.line.group_key])
        top1 += ok
        top3 += sl.line.label_scc_id in pred.ranked[:3]
        if ok:
            hours_ok += h
        if pred.confidence is not None:
            for t in CONFIDENT_WRONG_THRESHOLDS:
                if pred.confidence >= t:
                    cw[t].append(not ok)
    return {
        "n": n,
        "coverage": _rate(covered, n),
        "top1": _rate(top1, n),
        "top3": _rate(top3, n),
        "top1_hours": float(round(hours_ok / hours_total, 6)) if hours_total else None,
        "confident_wrong": {
            str(t): {"n": len(v), "rate": _rate(sum(v), len(v))} for t, v in cw.items()
        },
    }


def in_window(sl: ScoredLine, since: date, until: date) -> bool:
    return since <= sl.line.line_date <= until


def compute_signal_metrics(
    scored: Sequence[ScoredLine], groups: Mapping[tuple, set[int]], *, since: date, until: date
) -> dict[str, dict[str, Any]]:
    rows = [sl for sl in scored if in_window(sl, since, until)]
    buckets: dict[str, list[ScoredLine]] = defaultdict(list)
    for sl in rows:
        for k in segment_keys(sl):
            buckets[k].append(sl)
    return {
        key: {seg: metrics_for_rows(r, key, groups) for seg, r in sorted(buckets.items())}
        for key in signal_keys()
    }


def choose_threshold(
    scored: Sequence[ScoredLine],
    key: str,
    groups: Mapping[tuple, set[int]],
    *,
    cal_since: date,
    cal_until: date,
) -> Optional[Decimal]:
    """Lowest grid threshold whose confident-wrong rate <= 5% on CALIBRATION rows only, with at
    least MIN_SUPPORT calibration rows shown at that threshold."""
    rows = [sl for sl in scored if in_window(sl, cal_since, cal_until) and sl.line.is_timelog_sourced]
    for t in FIT_THRESHOLD_GRID:
        flags = [
            not line_correct(sl.predictions[key], sl.line.label_scc_id, groups[sl.line.group_key])
            for sl in rows
            if sl.predictions[key].top1 is not None
            and sl.predictions[key].confidence is not None
            and sl.predictions[key].confidence >= t
        ]
        if len(flags) >= MIN_SUPPORT and Decimal(sum(flags)) / Decimal(len(flags)) <= MAX_CONFIDENT_WRONG:
            return t
    return None


def thresholded_metrics(
    scored: Sequence[ScoredLine],
    key: str,
    threshold: Optional[Decimal],
    groups: Mapping[tuple, set[int]],
    *,
    since: date,
    until: date,
) -> dict[str, Any]:
    rows = [sl for sl in scored if in_window(sl, since, until) and sl.line.is_timelog_sourced]
    if threshold is None:
        return {"threshold": None, "n": len(rows), "shown": 0}
    shown = [
        sl for sl in rows
        if sl.predictions[key].top1 is not None
        and sl.predictions[key].confidence is not None
        and sl.predictions[key].confidence >= threshold
    ]
    wrong = sum(
        not line_correct(sl.predictions[key], sl.line.label_scc_id, groups[sl.line.group_key])
        for sl in shown
    )
    return {
        "threshold": float(threshold),
        "n": len(rows),
        "shown": len(shown),
        "shown_rate": _rate(len(shown), len(rows)),
        "confident_wrong": _rate(wrong, len(shown)),
        "correct_shown_over_all": _rate(len(shown) - wrong, len(rows)),
    }


def split_metrics(
    scored: Sequence[ScoredLine],
    key: str,
    group_truth: Mapping[tuple, set[int]],
    *,
    since: date,
    until: date,
) -> dict[str, Any]:
    """Per (ContractLabor, Project, LineDate) group of a TimeLog-era CL (split-offs included)."""
    groups: dict[tuple, list[ScoredLine]] = defaultdict(list)
    for sl in scored:
        if in_window(sl, since, until) and sl.line.is_timelog_sourced:
            groups[sl.line.group_key].append(sl)
    tp = fp = fn = 0
    set_correct_pred = set_correct_actual = 0
    hw_scores: list[float] = []
    hw_split_scores: list[float] = []
    for members in groups.values():
        actual: dict[int, Decimal] = defaultdict(Decimal)
        for sl in members:
            actual[sl.line.label_scc_id] += sl.line.hours or Decimal("0")
        assert set(actual) == group_truth[members[0].line.group_key], "group truth diverged"
        pred = members[0].predictions[key]
        pred_codes = {s for s, _ in pred.split} if pred.split else ({pred.top1} if pred.top1 else set())
        actual_split = len(actual) >= 2
        pred_split = bool(pred.split)
        tp += actual_split and pred_split
        fp += pred_split and not actual_split
        fn += actual_split and not pred_split
        if pred_split and pred_codes == set(actual):
            set_correct_pred += 1
        if actual_split and pred_codes == set(actual):
            set_correct_actual += 1
        total = sum(actual.values())
        if total > 0:
            shares = dict(pred.split) if pred.split else ({pred.top1: Decimal(1)} if pred.top1 else {})
            ok = sum(min(shares.get(s, Decimal(0)) * total, h) for s, h in actual.items())
            hw = float(ok / total)
            hw_scores.append(hw)
            if actual_split:
                hw_split_scores.append(hw)
    return {
        "groups": len(groups),
        "actual_split_groups": tp + fn,
        "predicted_split_groups": tp + fp,
        "split_precision": _rate(tp, tp + fp),
        "split_recall": _rate(tp, tp + fn),
        "set_correct_over_predicted_splits": _rate(set_correct_pred, tp + fp),
        "set_correct_over_actual_splits": _rate(set_correct_actual, tp + fn),
        "hours_weighted_correct_all_groups": round(sum(hw_scores) / len(hw_scores), 6) if hw_scores else None,
        "hours_weighted_correct_actual_splits": (
            round(sum(hw_split_scores) / len(hw_split_scores), 6) if hw_split_scores else None
        ),
    }


def phase_b_verdict(
    metrics: Mapping[str, Any], fitted: Mapping[str, Any]
) -> dict[str, Any]:
    s0 = metrics["s0_vendor_project"].get("timelog", {}).get("top1")
    out: dict[str, Any] = {"s0_vendor_project_top1_timelog": s0, "candidates": {}}
    for key in signal_keys():
        if not key.startswith("s1"):
            continue
        top1 = metrics[key].get("timelog", {}).get("top1")
        cw = fitted.get(key, {}).get("confident_wrong")
        shown = int(fitted.get(key, {}).get("shown") or 0)
        lift = None if (top1 is None or s0 is None) else round(top1 - s0, 6)
        if shown < MIN_SUPPORT:
            verdict = "insufficient_support"
        elif (
            lift is not None and Decimal(str(lift)) >= PHASE_B_MIN_LIFT
            and cw is not None and Decimal(str(cw)) <= MAX_CONFIDENT_WRONG
        ):
            verdict = "pass"
        else:
            verdict = "fail"
        out["candidates"][key] = {
            "top1_timelog": top1,
            "lift_over_s0": lift,
            "confident_wrong_at_fitted_threshold": cw,
            "shown": shown,
            "verdict": verdict,
            "passes": verdict == "pass",
        }
    out["any_passes"] = any(c["passes"] for c in out["candidates"].values())
    return out


# ---------------------------------------------------------------------------
# S3: label-over-number
# ---------------------------------------------------------------------------


def normalize_scc_hint(hint: str) -> Optional[str]:
    hint = hint.strip()
    if "." not in hint:
        return None
    left, _, right = hint.partition(".")
    if len(right) == 1:
        right = "0" + right
    return f"{left}.{right}"


def resolve_by_number(catalog: Sequence[SccRef], number: str) -> Optional[int]:
    """In-memory FindSubCostCodeForReply, in confidence order: exact_number (1.00),
    exact_number_normalized (0.95, right-segment pad), exact_alias (0.90, pipe-split Aliases).
    NOT mirrored: substring_alias (0.80) and substring_name (0.75) - on a numeric hint they are the
    sproc's weakest paths, and names are the label resolver's job here."""
    hint = number.strip()
    for s in catalog:
        if s.number == hint:
            return s.id
    padded = normalize_scc_hint(hint)
    for s in catalog:
        if padded and s.number == padded:
            return s.id
    for s in catalog:
        if s.aliases and any(part.strip() == hint for part in s.aliases.split("|")):
            return s.id
    return None


def _name_tokens(text: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"[a-z]+", text.lower()) if len(t) >= 2 and t not in {"the", "and", "of"})


def resolve_by_label(catalog: Sequence[SccRef], label: Optional[str]) -> Optional[int]:
    """Confident label match: unique best token-Jaccard with an SCC name, >= LABEL_MATCH_MIN."""
    if not label:
        return None
    lt = _name_tokens(label)
    if not lt:
        return None
    scored = []
    for s in catalog:
        nt = _name_tokens(s.name)
        if not nt:
            continue
        j = len(lt & nt) / len(lt | nt)
        if j > 0:
            scored.append((j, s.id))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1]))
    best = scored[0][0]
    if best < LABEL_MATCH_MIN or (len(scored) > 1 and scored[1][0] == best):
        return None
    return scored[0][1]


def labor_context_swap(catalog: Sequence[SccRef], scc_id: Optional[int]) -> Optional[int]:
    """In a labor context a '<x> Materials' code means its '<x> Labor' sibling when one exists."""
    if scc_id is None:
        return None
    by_id = {s.id: s for s in catalog}
    s = by_id.get(scc_id)
    if s is None or "material" not in s.name.lower():
        return scc_id
    target = re.sub(r"(?i)materials?", "labor", s.name).strip().lower()
    for c in catalog:
        if c.name.strip().lower() == target:
            return c.id
    return scc_id


def parse_reply(text: Optional[str]) -> Optional[tuple[str, Optional[str]]]:
    if not text:
        return None
    m = _REPLY_RE.search(text)
    if not m:
        return None
    label = m.group(2).strip() if m.group(2) else None
    return m.group(1), label or None


@dataclass(frozen=True)
class ReplyResolution:
    number_scc: Optional[int]
    label_scc: Optional[int]
    final_scc: Optional[int]
    disagreed: bool


def resolve_reply(
    catalog: Sequence[SccRef], text: Optional[str], *, labor_context: bool
) -> Optional[ReplyResolution]:
    parsed = parse_reply(text)
    if parsed is None:
        return None
    number, label = parsed
    by_num = resolve_by_number(catalog, number)
    by_label = resolve_by_label(catalog, label)
    disagreed = by_num is not None and by_label is not None and by_num != by_label
    final = by_label if by_label is not None else by_num
    if labor_context:
        final = labor_context_swap(catalog, final)
    return ReplyResolution(by_num, by_label, final, disagreed)


@dataclass(frozen=True)
class ReplyRow:
    review_id: int
    parent_id: int
    text: Optional[str] = field(repr=False, compare=False)
    created_at: Optional[datetime] = None
    # Labor only: (normalised text, project, work date) - one PM reply fans out to the whole crew.
    norm_text: Optional[str] = field(default=None, repr=False, compare=False)
    project_id: Optional[int] = None
    work_date: Optional[date] = None

    @property
    def dedupe_key(self) -> Optional[tuple]:
        if self.norm_text is None:
            return None
        return (self.norm_text, self.project_id, self.work_date)


def normalise_reply(text: Optional[str]) -> str:
    return " ".join((text or "").lower().split())


def dedupe_replies(rows: Sequence[ReplyRow]) -> list[ReplyRow]:
    """Keep the lowest review id per dedupe key (U-210: one reply codes every crew member's CL,
    each getting its own Review row with the same Comments). Rows without a key are kept."""
    seen: set[tuple] = set()
    out: list[ReplyRow] = []
    for r in sorted(rows, key=lambda x: x.review_id):
        k = r.dedupe_key
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        out.append(r)
    return out


_S3_SLICES = ("all_parsed", "truth_differs_from_number")


def _s3_slice_init() -> dict[str, int]:
    return dict(scored=0, num_ok=0, final_ok=0, disagree=0, dis_label_ok=0, dis_num_ok=0)


def s3_report(
    catalog: Sequence[SccRef],
    replies: Sequence[ReplyRow],
    labels: Mapping[int, set[int]],
    *,
    labor_context: bool,
) -> dict[str, Any]:
    """Score both reply resolvers against each parent's CURRENT single SCC, three ways.

    all_parsed is CIRCULAR: the current SCC was usually written by the incumbent number resolver
    acting on this very reply, so the number resolver is favoured by construction.
    truth_differs_from_number keeps replies whose current SCC is not the number resolver's pick -
    evidence a human corrected it. ContractLaborLineItem.ModifiedDatetime is deliberately NOT used as
    a correction signal: bill generation rewrites every line of every billed ContractLabor
    (bill_service update_by_id with bill_line_item_id) and UpdateContractLaborLineItemById stamps
    ModifiedDatetime unconditionally, so "modified after the review" mostly means "billed".
    """
    deduped = dedupe_replies(replies)
    acc = {k: _s3_slice_init() for k in _S3_SLICES}
    parsed = ambiguous = 0
    for r in deduped:
        res = resolve_reply(catalog, r.text, labor_context=labor_context)
        if res is None:
            continue
        parsed += 1
        lab = labels.get(r.parent_id, set())
        if len(lab) != 1:
            ambiguous += 1
            continue
        (truth,) = tuple(lab)
        slices = ["all_parsed"]
        if res.number_scc != truth:
            slices.append("truth_differs_from_number")
        for k in slices:
            a = acc[k]
            a["scored"] += 1
            a["num_ok"] += res.number_scc == truth
            a["final_ok"] += res.final_scc == truth
            if res.disagreed:
                a["disagree"] += 1
                a["dis_label_ok"] += res.label_scc == truth
                a["dis_num_ok"] += res.number_scc == truth
    out: dict[str, Any] = {
        "raw_replies": len(replies),
        "deduped_replies": len(deduped),
        "parsed": parsed,
        "ambiguous_or_unlabelled": ambiguous,
        "note_all_parsed": "circular:truth_is_mostly_the_incumbent_number_resolver_output",
    }
    for k in _S3_SLICES:
        a = acc[k]
        out[k] = {
            "scored": a["scored"],
            "number_resolver_accuracy": _rate(a["num_ok"], a["scored"]),
            "label_over_number_accuracy": _rate(a["final_ok"], a["scored"]),
            "disagreements": a["disagree"],
            "disagreement_label_correct": a["dis_label_ok"],
            "disagreement_number_correct": a["dis_num_ok"],
        }
    return out


# ---------------------------------------------------------------------------
# Privacy guard
# ---------------------------------------------------------------------------

_SAFE_JSON_STRING = re.compile(r"^[A-Za-z0-9_.:\-/+]*$")
_MIN_FORBIDDEN_LEN = 8


def _walk_json(value: Any, forbidden: Sequence[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _check_json_string(str(k), forbidden)
            _walk_json(v, forbidden)
    elif isinstance(value, list):
        for v in value:
            _walk_json(v, forbidden)
    elif isinstance(value, str):
        _check_json_string(value, forbidden)


def _check_json_string(s: str, forbidden: Sequence[str]) -> None:
    if not _SAFE_JSON_STRING.match(s):
        raise ValueError("privacy: JSON string is not an id/code/key token")
    for f in forbidden:
        if f in s:
            raise ValueError("privacy: corpus text found in JSON output")


def assert_outputs_privacy_safe(path: Path, forbidden_texts: Iterable[Optional[str]] = ()) -> None:
    """JSON: every key and string value must be a whitespace-free id/code token. Every file:
    no corpus free text (notes, descriptions, reply comments; >= 8 chars) may appear."""
    forbidden = sorted(
        {t.strip() for t in forbidden_texts if t and len(t.strip()) >= _MIN_FORBIDDEN_LEN}
    )
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        _walk_json(json.loads(text), forbidden)
    for f in forbidden:
        if f in text:
            raise ValueError(f"privacy: corpus text found in {path.name}")


# ---------------------------------------------------------------------------
# DB loading (prod read-only; never called by tests)
# ---------------------------------------------------------------------------


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    raise TypeError(f"expected datetime, got {type(value)}")


def _parse_date(value: Any) -> date:
    return value.date() if isinstance(value, datetime) else value


def _select_all(sqls: Sequence[str]) -> list[list[Any]]:
    from shared.database import get_connection

    out: list[list[Any]] = []
    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            for sql in sqls:
                cur.execute(sql)
                out.append(cur.fetchall())
        finally:
            conn.rollback()
    return out


def load_population_counts() -> dict[str, Any]:
    (rows,) = _select_all([_POPULATION_COUNTS_SQL])
    r = rows[0]
    return {
        "lines": int(r.Lines or 0),
        "timelog_sourced": int(r.TimeLogSourced or 0),
        "excel_era": int(r.ExcelEra or 0),
        "split_off_timelog_era": int(r.SplitOff or 0),
        "projects": int(r.Projects or 0),
        "contract_labors": int(r.ContractLabors or 0),
        "min_date": str(r.MinDate) if r.MinDate else None,
        "max_date": str(r.MaxDate) if r.MaxDate else None,
    }


@dataclass
class Corpus:
    lines: list[LaborLine]
    catalog: list[SccRef]
    labor_replies: list[ReplyRow]
    bill_replies: list[ReplyRow]
    bill_reply_labels: dict[int, set[int]]

    def free_texts(self) -> list[Optional[str]]:
        out: list[Optional[str]] = []
        for ln in self.lines:
            out += [ln.note_text, ln.stored_description]
            out += split_segments(ln.note_text)
        out += [r.text for r in self.labor_replies] + [r.text for r in self.bill_replies]
        out += [r.norm_text for r in self.labor_replies]
        return out


def build_lines(
    line_rows: Sequence[Any],
    description_rows: Sequence[Any],
    timelog_rows: Sequence[Any],
    coded_at_rows: Sequence[Any],
) -> list[LaborLine]:
    desc = {int(r.LineId): r.Description for r in description_rows}
    coded = {int(r.ContractLaborId): _parse_dt(r.CodedAt) for r in coded_at_rows}
    logs: dict[tuple[int, Optional[int]], list[tuple[datetime, Optional[str]]]] = defaultdict(list)
    for r in timelog_rows:
        pid = int(r.ProjectId) if r.ProjectId is not None else None
        logs[(int(r.TimeEntryId), pid)].append((_parse_dt(r.ClockIn), r.Note))
    out = []
    for r in line_rows:
        te = int(r.SourceTimeEntryId) if r.SourceTimeEntryId is not None else None
        cl_te = int(r.ClSourceTimeEntryId) if r.ClSourceTimeEntryId is not None else None
        lineage = te if te is not None else cl_te  # PM split-offs inherit the CL's TimeEntry
        pid = int(r.ProjectId) if r.ProjectId is not None else None
        out.append(
            LaborLine(
                line_id=int(r.LineId),
                contract_labor_id=int(r.ContractLaborId),
                line_date=_parse_date(r.LineDate),
                project_id=pid,
                vendor_id=int(r.VendorId) if r.VendorId is not None else None,
                label_scc_id=int(r.LabelSccId),
                hours=Decimal(str(r.Hours)) if r.Hours is not None else Decimal("0"),
                source_time_entry_id=te,
                coded_at=coded.get(int(r.ContractLaborId)),
                cl_source_time_entry_id=cl_te,
                # project-scoped only: no cross-project fallback
                note_text=rebuild_note_text(logs.get((lineage, pid), [])) if lineage is not None else None,
                stored_description=desc.get(int(r.LineId)),
            )
        )
    return out


def load_corpus_from_db() -> Corpus:
    lines_r, desc_r, logs_r, coded_r, scc_r, lrep_r, brep_r, blab_r = _select_all(
        [
            _LINES_SQL, _STORED_DESCRIPTION_SQL, _TIMELOG_NOTES_SQL, _CODED_AT_SQL,
            _SCC_SQL, _LABOR_REPLIES_SQL, _BILL_REPLIES_SQL, _BILL_REPLY_LABELS_SQL,
        ]
    )
    catalog = [SccRef(int(r.Id), str(r.Number), str(r.Name), r.Aliases) for r in scc_r]
    lines = build_lines(lines_r, desc_r, logs_r, coded_r)
    projects_by_cl: dict[int, set[Optional[int]]] = defaultdict(set)
    for ln in lines:
        projects_by_cl[ln.contract_labor_id].add(ln.project_id)

    def labor_project(cl_id: int) -> Optional[int]:
        ps = projects_by_cl.get(cl_id, set())
        return next(iter(ps)) if len(ps) == 1 else None
    bill_labels: dict[int, set[int]] = defaultdict(set)
    for r in blab_r:
        bill_labels[int(r.ParentId)].add(int(r.SubCostCodeId))
    return Corpus(
        lines=lines,
        catalog=catalog,
        labor_replies=[
            ReplyRow(
                int(r.ReviewId), int(r.ParentId), r.Comments, _parse_dt(r.CreatedDatetime),
                normalise_reply(r.Comments), labor_project(int(r.ParentId)),
                _parse_date(r.WorkDate) if r.WorkDate is not None else None,
            )
            for r in lrep_r
        ],
        bill_replies=[
            ReplyRow(int(r.ReviewId), int(r.ParentId), r.Comments, _parse_dt(r.CreatedDatetime))
            for r in brep_r
        ],
        bill_reply_labels=dict(bill_labels),
    )


# ---------------------------------------------------------------------------
# Run + report
# ---------------------------------------------------------------------------


def labor_reply_labels(lines: Sequence[LaborLine]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = defaultdict(set)
    for ln in lines:
        out[ln.contract_labor_id].add(ln.label_scc_id)
    return dict(out)


def population_summary(lines: Sequence[LaborLine]) -> dict[str, Any]:
    tl = [ln for ln in lines if ln.is_timelog_sourced]
    return {
        "lines": len(lines),
        "timelog_sourced": len(tl),
        "timelog_with_note": sum(1 for ln in tl if tokenize_labor_text(ln.note_text)),
        "excel_era": len(lines) - len(tl),
        "split_off_timelog_era": sum(1 for ln in tl if ln.source_time_entry_id is None),
        "with_coded_at": sum(1 for ln in lines if ln.coded_at is not None),
        "projects": len({ln.project_id for ln in lines}),
        "vendors": len({ln.vendor_id for ln in lines}),
    }


def run_backtest(
    corpus: Corpus,
    *,
    eval_since: date,
    eval_until: date,
    cal_since: date,
    cal_until: date,
    coding_lag_days: int,
) -> dict[str, Any]:
    holdout_since = cal_until + timedelta(days=1)
    validate_windows(cal_since, cal_until, holdout_since, eval_until)
    first = min(cal_since, eval_since)
    scored = score_lines(corpus.lines, coding_lag_days=coding_lag_days, score_since=first)
    probe = score_lines(corpus.lines, coding_lag_days=coding_lag_days, probe=True, score_since=first)
    groups = group_labels(scored)  # same lines in both passes, so one truth serves both
    metrics = compute_signal_metrics(scored, groups, since=eval_since, until=eval_until)
    probe_metrics = compute_signal_metrics(probe, groups, since=eval_since, until=eval_until)
    fitted = {}
    for key in signal_keys():
        thr = choose_threshold(scored, key, groups, cal_since=cal_since, cal_until=cal_until)
        fitted[key] = thresholded_metrics(scored, key, thr, groups, since=holdout_since, until=eval_until)
    leakage = {}
    for key in signal_keys():
        for seg in ("timelog", "excel", "all"):
            a = metrics[key].get(seg, {}).get("top1")
            b = probe_metrics[key].get(seg, {}).get("top1")
            leakage[f"{key}:{seg}"] = {
                "top1_timelog_notes": a,
                "top1_stored_description": b,
                "delta": None if a is None or b is None else round(b - a, 6),
            }
    splits = {
        f"s1s2_w{n}": split_metrics(scored, f"s1s2_w{n}", groups, since=eval_since, until=eval_until)
        for n in WINDOWS_WORKDAYS
    }
    _, s, u = NAMED_SLICE
    splits_slice = {
        f"s1s2_w{n}": split_metrics(scored, f"s1s2_w{n}", groups, since=s, until=u) for n in WINDOWS_WORKDAYS
    }
    s3 = {
        "labor_replies": s3_report(
            corpus.catalog, corpus.labor_replies, labor_reply_labels(corpus.lines),
            labor_context=True,
        ),
        "bill_replies": s3_report(
            corpus.catalog, corpus.bill_replies, corpus.bill_reply_labels, labor_context=False
        ),
    }
    return {
        "population": population_summary(corpus.lines),
        "metrics": metrics,
        "fitted_threshold_holdout": fitted,
        "leakage_probe": leakage,
        "splits": splits,
        "splits_named_slice": splits_slice,
        "s3": s3,
        "phase_b": phase_b_verdict(metrics, fitted),
        "windows": {
            "eval": [str(eval_since), str(eval_until)],
            "calibration": [str(cal_since), str(cal_until)],
            "fitted_threshold_holdout": [str(holdout_since), str(eval_until)],
            "named_slice": [str(s), str(u)],
        },
        "coding_lag_days": coding_lag_days,
    }


def _pct(v: Optional[float]) -> str:
    return f"{v * 100:.1f}" if v is not None else "n/a"


def render_markdown(result: Mapping[str, Any], stamp: str, git_sha: Optional[str]) -> str:
    out = [
        f"# U-673 labor coding backtest ({stamp})",
        "",
        f"- model `{MODEL_VERSION}` - git `{git_sha or 'unknown'}` - coding lag {result['coding_lag_days']} days",
        f"- windows: `{json.dumps(result['windows'])}`",
        f"- population: `{json.dumps(result['population'])}`",
        "",
        "## Top-1 / top-3 / coverage / hours-weighted top-1 (abstain = wrong)",
        "",
        "| signal | segment | n | coverage | top-1 | top-3 | top-1 hours | cw@0.60 | cw@0.70 | cw@0.85 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for key, segs in result["metrics"].items():
        for seg, m in segs.items():
            cw = m["confident_wrong"]
            out.append(
                f"| {key} | {seg} | {m['n']} | {_pct(m['coverage'])} | {_pct(m['top1'])} | "
                f"{_pct(m['top3'])} | {_pct(m['top1_hours'])} | "
                + " | ".join(_pct(cw[str(t)]["rate"]) for t in CONFIDENT_WRONG_THRESHOLDS)
                + " |"
            )
    out += ["", "## Fitted threshold (calibration-only), evaluated on the post-calibration holdout", "",
            "| signal | threshold | n | shown | confident-wrong |", "|---|---|---|---|---|"]
    for key, f in result["fitted_threshold_holdout"].items():
        out.append(
            f"| {key} | {f.get('threshold')} | {f.get('n')} | {f.get('shown', 'n/a')} | "
            f"{_pct(f.get('confident_wrong'))} |"
        )
    out += ["", "S3 note: `all_parsed` is circular - the current SCC was usually written by the "
            "incumbent number resolver from this same reply. Read `truth_differs_from_number` for "
            "evidence of human correction (line ModifiedDatetime is not used: billing bumps it).", ""]
    out += ["", "## Phase B bar", "", "```json", json.dumps(result["phase_b"], indent=2), "```",
            "", "## Leakage probe (stored Description as input)", "",
            "| signal:segment | TimeLog notes | stored Description | delta |", "|---|---|---|---|"]
    for k, v in result["leakage_probe"].items():
        out.append(f"| {k} | {_pct(v['top1_timelog_notes'])} | {_pct(v['top1_stored_description'])} | {_pct(v['delta'])} |")
    out += ["", "## S2 split detection (eval window / named slice)", "", "```json",
            json.dumps({"eval": result["splits"], "named_slice": result["splits_named_slice"]}, indent=2),
            "```", "", "## S3 label-over-number", "", "```json", json.dumps(result["s3"], indent=2), "```", ""]
    return "\n".join(out)


def _git_sha() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def write_outputs(
    result: Mapping[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    forbidden_texts: Iterable[Optional[str]],
    git_sha: Optional[str] = None,
) -> tuple[Path, Path]:
    """Fail-closed: write temp names, guard BOTH, rename only after both pass."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"u673_report_{stamp}.md"
    js = out_dir / f"u673_report_{stamp}.json"
    md_tmp = out_dir / f".u673_partial_{stamp}.md"
    js_tmp = out_dir / f".u673_partial_{stamp}.json"
    forbidden = list(forbidden_texts)
    try:
        js_tmp.write_text(
            json.dumps({"model_version": MODEL_VERSION, "git_sha": git_sha, **result}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        md_tmp.write_text(render_markdown(result, stamp, git_sha), encoding="utf-8")
        for p in (js_tmp, md_tmp):
            assert_outputs_privacy_safe(p, forbidden)
        js_tmp.replace(js)
        md_tmp.replace(md)
    except BaseException:
        for p in (js_tmp, md_tmp, js, md):
            p.unlink(missing_ok=True)
        raise
    return md, js


def main(
    argv: Optional[list[str]] = None,
    *,
    counts_loader: Callable[[], dict[str, Any]] = load_population_counts,
    corpus_loader: Callable[[], Corpus] = load_corpus_from_db,
) -> int:
    ap = argparse.ArgumentParser(description="U-673 labor coding backtest (read-only).")
    ap.add_argument("--run", action="store_true", help="Score (default: dry-run, counts only).")
    ap.add_argument("--eval-since", type=date.fromisoformat, default=DEFAULT_EVAL_SINCE)
    ap.add_argument("--eval-until", type=date.fromisoformat, default=DEFAULT_EVAL_UNTIL)
    ap.add_argument("--cal-since", type=date.fromisoformat, default=DEFAULT_CAL_SINCE)
    ap.add_argument("--cal-until", type=date.fromisoformat, default=DEFAULT_CAL_UNTIL)
    ap.add_argument("--coding-lag-days", type=int, default=DEFAULT_CODING_LAG_DAYS)
    ap.add_argument("--out-dir", type=Path, default=Path("docs/design/u673"))
    args = ap.parse_args(argv)
    if args.coding_lag_days < 0:
        ap.error("--coding-lag-days must be >= 0")
    if args.eval_since > args.eval_until:
        ap.error("evaluation window is reversed")
    validate_windows(args.cal_since, args.cal_until, args.cal_until + timedelta(days=1), args.eval_until)

    if not args.run:
        print("U-673 dry-run population: " + json.dumps(counts_loader(), sort_keys=True))
        return 0

    corpus = corpus_loader()
    result = run_backtest(
        corpus,
        eval_since=args.eval_since,
        eval_until=args.eval_until,
        cal_since=args.cal_since,
        cal_until=args.cal_until,
        coding_lag_days=args.coding_lag_days,
    )
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    md, _ = write_outputs(
        result, out_dir=args.out_dir, stamp=stamp, forbidden_texts=corpus.free_texts(), git_sha=_git_sha()
    )
    print(f"U-673: {len(corpus.lines)} lines; report={md.name}")
    return 0


if __name__ == "__main__":
    from scripts.sync_helper import assert_cli_system_admin

    assert_cli_system_admin()
    raise SystemExit(main())
