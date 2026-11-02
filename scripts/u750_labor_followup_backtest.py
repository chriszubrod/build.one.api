"""
U-750: read-only backtest for a Contract Labor coding FOLLOW-UP proposal.

Business case: a crew-member day was submitted uncoded. At follow-up time T = LineDate + K calendar
days an email proposes the coding (codes + hour split) from same-project precedent, and a reviewer
approves it. This harness measures how often that proposal would be exactly right.

Unit of evaluation = a crew-member day: the labelled lines sharing (ContractLabor, Project, LineDate),
U-673's group truth. Truth = {SubCostCodeId: summed Hours}. "Split" = >= 2 distinct codes.

Evidence (precedent) for a query day d on project p at follow-up time T = d + K:
  * labelled lines on project p with |workdays_between| <= 10 of d;
  * EXCLUDING every line dated d on p (the whole crew is uncoded at follow-up time);
  * visible only if coded before T: coded_at < T when the prior's ContractLabor has a coded_at
    (first final, non-declined Review - U-673), else prior.LineDate + 2 days <= T.
  Later-dated priors are allowed when visible by T (hindsight mode). For CALIBRATION query days the
  evidence is additionally capped at the calibration end date so the fitted pair never sees a
  holdout row.

Arm A (no LLM): U-673 split_segments + an IDF-weighted Jaccard over the SYMMETRIC +-10 workday window,
weighted 1 / (1 + workday distance). A segment resolves to its top code when the code's share >= tau
and >= m evidence lines support it. One distinct resolved code -> single proposal (all hours). Two or
more -> a split ONLY when an evidence crew-member day has exactly that code set; its hour ratio
(nearest in workdays, tie earlier date then lower ContractLabor id) is scaled to the query's hours
and rounded to 0.25 h, remainder on the largest share. Otherwise fall back to the single best
segment pick (counted). Nothing resolved -> abstain. (tau, m) fitted on calibration only.

Arm B (LLM, only with --llm-run): Sonnet (the live U-701b ladder V2 Sonnet rung) gets the notes,
total hours and a precedent table, returns strict JSON. Isolated per-run Claude ledger (U-702) with
a hard cap from --budget-usd; a budget skip aborts loudly; responses cached on disk under
<out-dir>/llm_cache/ (contains PROMPTS - the out dir must live under
/Users/chris/Applications/build.one/.data/ and never be committed).

Outputs (ids / codes / hours / rates only): u750_report_<stamp>.md + .json, both checked by U-673's
assert_outputs_privacy_safe before they are renamed into place. Every SQL batch is SELECT-only and
rolled back (U-673 _select_all).

Usage (repo root; prod read-only - EM only):
  .venv/bin/python -m scripts.u750_labor_followup_backtest                       # dry-run: counts
  .venv/bin/python -m scripts.u750_labor_followup_backtest --run --out-dir ../.data/u750
  .venv/bin/python -m scripts.u750_labor_followup_backtest --run --out-dir ../.data/u750 \\
      --llm-run --budget-usd 5.00
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from entities.coding_suggestion.business.similar_bills import (
    compute_idf_from_doc_token_sets,
    idf_weighted_jaccard,
)
from scripts.u653b_backtest import validate_windows
from scripts.u673_labor_coding_backtest import (
    _CODED_AT_SQL,
    _LINES_SQL,
    _SCC_SQL,
    _TIMELOG_NOTES_SQL,
    ABSTAIN,
    LaborLine,
    SccRef,
    _git_sha,
    _parse_date,
    _pct,
    _rate,
    _select_all,
    assert_outputs_privacy_safe,
    build_lines,
    predict_s0_vendor_project,
    predict_s1,
    split_segments,
    tokenize_labor_text,
    visible_priors,
    workdays_between,
)

MODEL_VERSION = "u750-labor-followup-v1"
DEFAULT_CAL_SINCE = date(2026, 6, 1)
DEFAULT_CAL_UNTIL = date(2026, 8, 15)
DEFAULT_HOLD_SINCE = date(2026, 8, 16)
DEFAULT_HOLD_UNTIL = date(2026, 10, 15)
DEFAULT_KS = (3, 7, 14)
HEADLINE_K = 7
EVIDENCE_WINDOW_WORKDAYS = 10
NO_CODED_AT_LAG_DAYS = 2
BASELINE_LAG_DAYS = 2  # U-673 S0 / S1 as-of rule
BASELINE_S1_WINDOW = 7
TAU_GRID = tuple(Decimal(x) / Decimal(100) for x in range(40, 91, 5))
M_GRID = (1, 2, 3)
MIN_SUPPORT = 20
BAR_EXACT = 0.80
BAR_FALSE_SPLIT = 0.05
BAR_COVERAGE = 0.50
HOURS_TOL = Decimal("0.01")
QUARTER = Decimal("0.25")
NAMED_CASE = ("HP2", date(2026, 9, 29))
DATA_ROOT = Path("/Users/chris/Applications/build.one/.data")  # --out-dir must resolve under here

# Arm B
DEFAULT_BUDGET_USD = Decimal("5.00")
DEFAULT_MAX_CALLS = 400
LLM_MAX_TOKENS = 600
LLM_MAX_PRECEDENTS = 30
CHARS_PER_TOKEN_ESTIMATE = 3.0  # conservative (real ratio is ~3.5-4 for English)
ESTIMATE_PAD = Decimal("1.5")  # every per-call estimate is padded 1.5x

_PROJECT_SQL = """
SELECT [Id], [Abbreviation]
FROM dbo.[Project]
"""

# Dry-run only: one row per crew-member day, no text.
_DAY_COUNTS_SQL = """
SELECT
    li.[ContractLaborId],
    li.[ProjectId],
    COALESCE(li.[LineDate], cl.[WorkDate]) AS [LineDate],
    COUNT(DISTINCT CASE WHEN li.[Hours] > 0 THEN li.[SubCostCodeId] END) AS [Codes],  -- positive-hour codes, as the run
    MAX(CASE WHEN COALESCE(li.[SourceTimeEntryId], cl.[SourceTimeEntryId]) IS NOT NULL
             THEN 1 ELSE 0 END) AS [TimeLogSourced]
FROM dbo.[ContractLaborLineItem] li
INNER JOIN dbo.[ContractLabor] cl ON cl.[Id] = li.[ContractLaborId]
INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = li.[SubCostCodeId]
WHERE cl.[Status] IN (N'approved', N'completed', N'ready', N'billed')
  AND li.[SubCostCodeId] IS NOT NULL
  AND ISNULL(scc.[IsHoldingCode], 0) = 0
GROUP BY li.[ContractLaborId], li.[ProjectId], COALESCE(li.[LineDate], cl.[WorkDate])
"""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrewDay:
    contract_labor_id: int
    project_id: Optional[int]
    line_date: date
    vendor_id: Optional[int]
    truth: tuple[tuple[int, Decimal], ...]  # (scc, hours) merged per code, sorted by scc; 0-h codes dropped
    timelog_sourced: bool
    lines: tuple[LaborLine, ...] = field(repr=False, compare=False)
    note_text: Optional[str] = field(default=None, repr=False, compare=False)
    coded_at: Optional[datetime] = None  # the day's own CL coded-at (first final, non-declined Review)

    @property
    def key(self) -> tuple[int, Optional[int], date]:
        return (self.contract_labor_id, self.project_id, self.line_date)

    def uncoded_at(self, k_days: int) -> bool:
        """True when a follow-up at T = LineDate + K would still find this day uncoded."""
        return self.coded_at is None or self.coded_at >= datetime.combine(self.line_date + timedelta(days=k_days), time.min)

    @property
    def total_hours(self) -> Decimal:
        return sum((h for _, h in self.truth), Decimal("0"))

    @property
    def codes(self) -> frozenset[int]:
        return frozenset(s for s, _ in self.truth)

    @property
    def is_split(self) -> bool:
        return len(self.truth) >= 2


def _merge_hours(pairs: Iterable[tuple[int, Decimal]]) -> tuple[tuple[int, Decimal], ...]:
    acc: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for s, h in pairs:
        acc[s] += h
    return tuple(sorted(acc.items()))


def build_crew_days(lines: Sequence[LaborLine]) -> list[CrewDay]:
    groups: dict[tuple, list[LaborLine]] = defaultdict(list)
    for ln in lines:
        groups[ln.group_key].append(ln)
    out = []
    for (cl, pid, d), ls in groups.items():
        ls = sorted(ls, key=lambda x: x.line_id)
        notes: list[str] = []
        for x in ls:  # one CL per TimeEntry: normally one note; de-dupe defensively
            if x.note_text and x.note_text not in notes:
                notes.append(x.note_text)
        merged = _merge_hours((x.label_scc_id, x.hours) for x in ls)
        positive = tuple((s, h) for s, h in merged if h > 0)
        out.append(
            CrewDay(
                contract_labor_id=cl,
                project_id=pid,
                line_date=d,
                vendor_id=ls[0].vendor_id,
                # a 0-h (NULL-hours) code never makes a day "split" nor counts in exact-correct;
                # a day with no positive hours keeps its raw truth (degenerate, reported as is)
                truth=positive or merged,
                timelog_sourced=any(x.is_timelog_sourced for x in ls),
                lines=tuple(ls),
                note_text="; ".join(notes) if notes else None,
                coded_at=ls[0].coded_at,
            )
        )
    return sorted(out, key=lambda c: (c.line_date, c.project_id or 0, c.contract_labor_id))


@dataclass(frozen=True)
class Proposal:
    kind: str  # "single" | "split"
    lines: tuple[tuple[int, Decimal], ...]  # (scc, hours)
    source: str
    flags: frozenset[str] = frozenset()
    precedent: Optional["PrecedentGroup"] = field(default=None, compare=False)  # chosen split precedent

    @property
    def codes(self) -> frozenset[int]:
        return frozenset(s for s, _ in self.lines)


def single(scc: int, hours: Decimal, source: str, flags: Iterable[str] = ()) -> Proposal:
    return Proposal("single", ((scc, hours),), source, frozenset(flags))


# ---------------------------------------------------------------------------
# Evidence (follow-up-time visibility)
# ---------------------------------------------------------------------------


def workday_distance(a: date, b: date) -> int:
    """Symmetric: Mon-Fri days between the two dates (U-673 workdays_between in either order)."""
    return workdays_between(min(a, b), max(a, b))


def evidence_visible(
    prior: LaborLine,
    *,
    project_id: Optional[int],
    query_date: date,
    k_days: int,
    window_workdays: int = EVIDENCE_WINDOW_WORKDAYS,
    evidence_until: Optional[date] = None,
    exclude_contract_labor_id: Optional[int] = None,
    past_only: bool = False,
) -> bool:
    if prior.project_id != project_id or project_id is None:
        return False
    if prior.line_date == query_date:  # the whole crew-day is uncoded at follow-up time
        return False
    if exclude_contract_labor_id is not None and prior.contract_labor_id == exclude_contract_labor_id:
        return False  # the query's own CL never counts, on any date (U-673)
    if past_only and prior.line_date > query_date:
        return False
    if evidence_until is not None and prior.line_date > evidence_until:
        return False
    if workday_distance(prior.line_date, query_date) > window_workdays:
        return False
    t_date = query_date + timedelta(days=k_days)
    if prior.coded_at is not None:
        return prior.coded_at < datetime.combine(t_date, time.min)
    return prior.line_date + timedelta(days=NO_CODED_AT_LAG_DAYS) <= t_date


def evidence_for_day(
    day: CrewDay,
    project_lines: Sequence[LaborLine],
    *,
    k_days: int,
    evidence_until: Optional[date] = None,
    past_only: bool = False,
) -> list[LaborLine]:
    return [
        p for p in project_lines
        if evidence_visible(
            p, project_id=day.project_id, query_date=day.line_date, k_days=k_days,
            evidence_until=evidence_until, exclude_contract_labor_id=day.contract_labor_id,
            past_only=past_only,
        )
    ]


def evidence_provenance(days_evidence: Iterable[tuple[CrewDay, Sequence[LaborLine]]]) -> dict[str, Any]:
    """How evidence lines entered: coded_at vs the no-coded_at date fallback; earlier vs later-dated."""
    c = {"lines": 0, "via_coded_at": 0, "via_date_fallback": 0, "earlier_dated": 0, "later_dated": 0}
    for day, ev in days_evidence:
        for p in ev:
            c["lines"] += 1
            c["via_coded_at" if p.coded_at is not None else "via_date_fallback"] += 1
            c["earlier_dated" if p.line_date < day.line_date else "later_dated"] += 1
    return _with_shares(c, "lines")


def precedent_provenance(rows: Iterable[tuple[CrewDay, Optional["Proposal"]]]) -> dict[str, Any]:
    c = {"split_precedents": 0, "via_coded_at": 0, "via_date_fallback": 0, "earlier_dated": 0, "later_dated": 0}
    for day, p in rows:
        if p is None or p.precedent is None:
            continue
        g = p.precedent
        c["split_precedents"] += 1
        c["via_coded_at" if g.via_coded_at else "via_date_fallback"] += 1
        c["earlier_dated" if g.line_date < day.line_date else "later_dated"] += 1
    return _with_shares(c, "split_precedents")


def _with_shares(counts: dict[str, int], denom: str) -> dict[str, Any]:
    n = counts[denom]
    return {**counts, **{f"{k}_share": _rate(v, n) for k, v in counts.items() if k != denom}}


@dataclass(frozen=True)
class PrecedentGroup:
    contract_labor_id: int
    line_date: date
    hours: tuple[tuple[int, Decimal], ...]
    via_coded_at: bool = False  # entered evidence via its coded_at (else the date fallback)

    @property
    def codes(self) -> frozenset[int]:
        return frozenset(s for s, _ in self.hours)

    @property
    def total(self) -> Decimal:
        return sum((h for _, h in self.hours), Decimal("0"))


def precedent_groups(evidence: Sequence[LaborLine]) -> list[PrecedentGroup]:
    """0-hour (NULL-hours) lines are dropped, matching the 0-hour truth rule; an all-zero group vanishes."""
    g: dict[tuple, list[LaborLine]] = defaultdict(list)
    for ln in evidence:
        if ln.hours > 0:
            g[ln.group_key].append(ln)
    return [
        PrecedentGroup(
            cl, d, _merge_hours((x.label_scc_id, x.hours) for x in ls),
            via_coded_at=ls[0].coded_at is not None,
        )
        for (cl, _pid, d), ls in sorted(g.items(), key=lambda kv: (kv[0][2], kv[0][0]))
    ]


def nearest_precedent_with_codes(
    groups: Sequence[PrecedentGroup], codes: frozenset[int], query_date: date
) -> Optional[PrecedentGroup]:
    matches = [g for g in groups if g.codes == codes and g.total > 0]
    if not matches:
        return None
    return min(
        matches,
        key=lambda g: (workday_distance(g.line_date, query_date), g.line_date, g.contract_labor_id),
    )


def scale_hours(group: PrecedentGroup, total_hours: Decimal) -> Optional[tuple[tuple[int, Decimal], ...]]:
    """Precedent ratio x query total, rounded to 0.25 h; remainder on the largest share.
    None when any scaled line would be <= 0 h (the caller falls back to a single code)."""
    gt = group.total
    raw = [(s, total_hours * h / gt) for s, h in group.hours]
    rounded = {s: (v / QUARTER).quantize(Decimal(1), rounding=ROUND_HALF_UP) * QUARTER for s, v in raw}
    largest = min(group.hours, key=lambda x: (-x[1], x[0]))[0]
    rounded[largest] += total_hours - sum(rounded.values())
    if any(h <= 0 for h in rounded.values()):
        return None
    return tuple(sorted(rounded.items()))


# ---------------------------------------------------------------------------
# Arm A
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SegmentPick:
    scc: int
    share: float
    score: float


@dataclass(frozen=True)
class SegmentScore:
    scores: Mapping[int, float]
    support: Mapping[int, int]

    def pick(self, tau: float, m: int) -> Optional[SegmentPick]:
        total = sum(self.scores.values())
        if total <= 0:
            return None
        top, sc = min(self.scores.items(), key=lambda x: (-x[1], x[0]))
        share = sc / total
        if share + 1e-12 < tau or self.support.get(top, 0) < m:
            return None
        return SegmentPick(top, share, sc)


def score_segment(
    seg_tokens: frozenset[str],
    query_date: date,
    evidence: Sequence[tuple[LaborLine, frozenset[str], float]],
    idf: dict[str, float],
) -> SegmentScore:
    scores: dict[int, float] = defaultdict(float)
    support: dict[int, int] = defaultdict(int)
    if seg_tokens:
        wq = sum(idf.get(t, 0.0) for t in seg_tokens)
        for prior, toks, w in evidence:
            if prior.hours <= 0:
                continue  # a 0-hour line is not evidence for its code (0-hour truth rule)
            sim = idf_weighted_jaccard(seg_tokens, wq, toks, w, idf)
            if sim <= 0:
                continue
            scores[prior.label_scc_id] += sim / (1.0 + workday_distance(prior.line_date, query_date))
            support[prior.label_scc_id] += 1
    return SegmentScore(dict(scores), dict(support))


@dataclass(frozen=True)
class DayAnalysis:
    """Everything arm A needs that does NOT depend on (tau, m) - computed once per (day, K)."""
    day: CrewDay
    segments: tuple[SegmentScore, ...]
    precedents: tuple[PrecedentGroup, ...]


def analyze_day(
    day: CrewDay,
    evidence: Sequence[LaborLine],
    tokens: Mapping[int, frozenset[str]],
    weights: Mapping[int, float],
    idf: dict[str, float],
) -> DayAnalysis:
    ev = [(p, tokens.get(p.line_id, frozenset()), weights.get(p.line_id, 0.0)) for p in evidence]
    segs = []
    for seg in split_segments(day.note_text):
        toks = tokenize_labor_text(seg)
        if toks:  # token-less segments (filler only) are ignored, not "unresolved"
            segs.append(score_segment(toks, day.line_date, ev, idf))
    return DayAnalysis(day, tuple(segs), tuple(precedent_groups(evidence)))


def propose_arm_a(an: DayAnalysis, tau: float, m: int) -> Optional[Proposal]:
    picks = [s.pick(tau, m) for s in an.segments]
    resolved = [p for p in picks if p is not None]
    if not resolved:
        return None
    flags = {"unresolved_segments"} if len(resolved) < len(picks) else set()
    total = an.day.total_hours
    codes = frozenset(p.scc for p in resolved)
    if len(codes) == 1:
        return single(next(iter(codes)), total, "arm_a", flags)
    prec = nearest_precedent_with_codes(an.precedents, codes, an.day.line_date)
    scaled = scale_hours(prec, total) if prec is not None else None
    if scaled is not None:
        return Proposal("split", scaled, "arm_a", frozenset(flags), precedent=prec)
    best = min(resolved, key=lambda p: (-p.share, -p.score, p.scc))
    return single(best.scc, total, "arm_a", flags | {"split_fallback"})


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _truth_map(day: CrewDay) -> dict[int, Decimal]:
    return dict(day.truth)


def set_correct(day: CrewDay, p: Proposal) -> bool:
    return p.codes == day.codes


def exact_correct(day: CrewDay, p: Proposal) -> bool:
    if not set_correct(day, p):
        return False
    t = _truth_map(day)
    return all(abs(h - t[s]) <= HOURS_TOL for s, h in p.lines)


def hours_matched(day: CrewDay, p: Proposal) -> Decimal:
    t = _truth_map(day)
    return sum((min(h, t[s]) for s, h in p.lines if s in t), Decimal("0"))


def wilson(num: int, den: int, z: float = 1.96) -> Optional[list[float]]:
    if not den:
        return None
    p = num / den
    d = 1 + z * z / den
    c = (p + z * z / (2 * den)) / d
    h = z * math.sqrt(p * (1 - p) / den + z * z / (4 * den * den)) / d
    return [round(max(0.0, c - h), 6), round(min(1.0, c + h), 6)]


def uncoded_key(k: int) -> str:
    return f"uncoded_at_T_k{k}"


def compute_metrics(
    rows: Sequence[tuple[CrewDay, Optional[Proposal]]], ks: Sequence[int] = ()
) -> dict[str, Any]:
    """Strata: all / split_truth / single_truth, plus uncoded_at_T_k<K> per K - the days whose own
    CL was still uncoded at T = LineDate + K (the population a follow-up email would actually see)."""
    out: dict[str, Any] = {}
    strata = {
        "all": rows,
        "split_truth": [r for r in rows if r[0].is_split],
        "single_truth": [r for r in rows if not r[0].is_split],
    }
    for k in ks:
        strata[uncoded_key(k)] = [r for r in rows if r[0].uncoded_at(k)]
    for name, rs in strata.items():
        n = len(rs)
        props = [(d, p) for d, p in rs if p is not None]
        np_ = len(props)
        exact = sum(1 for d, p in props if exact_correct(d, p))
        setc = sum(1 for d, p in props if set_correct(d, p))
        single_days = [(d, p) for d, p in rs if not d.is_split]
        split_days = [(d, p) for d, p in rs if d.is_split]
        false_split = sum(1 for d, p in single_days if p is not None and p.kind == "split")
        missed = sum(1 for d, p in split_days if p is None or p.kind != "split")
        split_props = [(d, p) for d, p in props if p.kind == "split"]
        split_on_split = sum(1 for d, _ in split_props if d.is_split)
        split_exact = sum(1 for d, p in split_props if exact_correct(d, p))
        hrs_total = sum((d.total_hours for d, _ in props), Decimal("0"))
        hrs_ok = sum((hours_matched(d, p) for d, p in props), Decimal("0"))
        out[name] = {
            "days": n,
            "proposals": np_,
            "abstains": n - np_,
            "coverage": _rate(np_, n),
            "coverage_ci95": wilson(np_, n),
            "abstain_rate": _rate(n - np_, n),
            "exact_correct": _rate(exact, np_),
            "exact_correct_ci95": wilson(exact, np_),
            "exact_correct_of_days": _rate(exact, n),
            "set_correct": _rate(setc, np_),
            "single_truth_days": len(single_days),
            "split_truth_days": len(split_days),
            "false_splits": false_split,
            "false_split_rate": _rate(false_split, len(single_days)),
            "false_split_rate_ci95": wilson(false_split, len(single_days)),
            "missed_splits": missed,
            "missed_split_rate": _rate(missed, len(split_days)),
            "split_proposals": len(split_props),
            "split_precision": _rate(split_on_split, len(split_props)),
            "split_exact_correct": _rate(split_exact, len(split_props)),
            "hours_weighted_accuracy": float(hrs_ok / hrs_total) if hrs_total else None,
            "split_fallbacks": sum(1 for _, p in props if "split_fallback" in p.flags),
            "unresolved_segment_flags": sum(1 for _, p in props if "unresolved_segments" in p.flags),
        }
    return out


def verdict(m_all: Mapping[str, Any]) -> str:
    if m_all["proposals"] < MIN_SUPPORT:
        return "insufficient_support"
    fsr = m_all["false_split_rate"]  # None = no single-truth days: no false split is possible
    ok = (
        (m_all["exact_correct"] or 0) >= BAR_EXACT
        and (fsr is None or fsr <= BAR_FALSE_SPLIT)
        and (m_all["coverage"] or 0) >= BAR_COVERAGE
    )
    return "PASS" if ok else "FAIL"


def verdicts(metrics: Mapping[str, Any], ks: Sequence[int] = (), *, incomplete: bool = False) -> dict[str, str]:
    """Phase B verdict for all days and for each realistic uncoded_at_T_k<K> population."""
    keys = ["all"] + [uncoded_key(k) for k in ks]
    return {k: ("incomplete" if incomplete else verdict(metrics[k])) for k in keys}


def fit_thresholds(cal: Sequence[DayAnalysis]) -> dict[str, Any]:
    """Max coverage s.t. exact >= 0.80, false-split <= 0.05, >= 20 proposals - CALIBRATION ONLY."""
    grid = []
    for tau in TAU_GRID:
        for m in M_GRID:
            met = compute_metrics([(a.day, propose_arm_a(a, float(tau), m)) for a in cal])["all"]
            grid.append((tau, m, met))
    qualified = [
        g for g in grid
        if g[2]["proposals"] >= MIN_SUPPORT
        and (g[2]["exact_correct"] or 0) >= BAR_EXACT
        and (g[2]["false_split_rate"] or 0) <= BAR_FALSE_SPLIT
    ]
    if qualified:
        tau, m, met = max(qualified, key=lambda g: (g[2]["coverage"] or 0, g[2]["exact_correct"] or 0, g[0], g[1]))
        status = "qualified"
    else:
        shown = [g for g in grid if g[2]["proposals"] > 0] or grid
        tau, m, met = max(shown, key=lambda g: (g[2]["exact_correct"] or 0, g[2]["coverage"] or 0, g[0], g[1]))
        status = "no_pair_qualified_best_exact_used"
    return {
        "status": status,
        "tau": float(tau),
        "m": m,
        "calibration_days": len(cal),
        "calibration_proposals": met["proposals"],
        "calibration_exact_correct": met["exact_correct"],
        "calibration_coverage": met["coverage"],
        "calibration_false_split_rate": met["false_split_rate"],
    }


# ---------------------------------------------------------------------------
# Baselines (single-code, at-submit, U-673 rules)
# ---------------------------------------------------------------------------


def baseline_proposals(
    days: Sequence[CrewDay],
    lines: Sequence[LaborLine],
    tokens: Mapping[int, frozenset[str]],
    weights: Mapping[int, float],
    idf: dict[str, float],
) -> dict[str, dict[tuple, Optional[Proposal]]]:
    by_vendor: dict[Optional[int], list[LaborLine]] = defaultdict(list)
    by_project: dict[Optional[int], list[LaborLine]] = defaultdict(list)
    for ln in lines:
        by_vendor[ln.vendor_id].append(ln)
        by_project[ln.project_id].append(ln)
    s0: dict[tuple, Optional[Proposal]] = {}
    s1: dict[tuple, Optional[Proposal]] = {}
    for d in days:
        q = d.lines[0]  # every line of a crew-day shares CL, date and coded_at
        vpri = visible_priors(by_vendor[d.vendor_id], q, coding_lag_days=BASELINE_LAG_DAYS) if d.vendor_id is not None else []
        ppri = visible_priors(by_project[d.project_id], q, coding_lag_days=BASELINE_LAG_DAYS) if d.project_id is not None else []
        vp = predict_s0_vendor_project(vpri, d.project_id)
        s0[d.key] = None if vp.top1 is None else single(vp.top1, d.total_hours, "s0_vendor_project")
        pr = predict_s1(
            d.note_text, d.line_date, [(p, tokens[p.line_id], weights[p.line_id]) for p in ppri], idf,
            window_workdays=BASELINE_S1_WINDOW, fallback=vp if vp.top1 is not None else ABSTAIN,
        )
        s1[d.key] = None if pr.top1 is None else single(pr.top1, d.total_hours, "s1_w7")
    return {"baseline_s0_vendor_project": s0, "baseline_s1_w7_at_submit": s1}


# ---------------------------------------------------------------------------
# Arm B (LLM)
# ---------------------------------------------------------------------------

LLM_SYSTEM = (
    "You code construction contract-labor crew days to sub cost codes. A crew member's day was "
    "submitted without coding. Propose its coding from the precedent days on the same project.\n"
    "Rules:\n"
    "- Use ONLY sub cost codes listed in the code catalog; return each code's id AND number exactly "
    "as listed.\n"
    "- Propose \"split\" ONLY when a precedent day shows the same kind of split (the same set of "
    "codes on one day) and the notes describe each of those activities. Otherwise propose "
    "\"single\" with all hours on one code.\n"
    "- Hours must sum exactly to the day's total hours. Prefer the precedent day's hour ratio.\n"
    "- If the precedents do not support a confident answer, propose \"abstain\".\n"
    "Return ONLY strict JSON: {\"proposal\": \"single\"|\"split\"|\"abstain\", \"lines\": "
    "[{\"sub_cost_code_id\": int, \"sub_cost_code_number\": str, \"hours\": number}], "
    "\"evidence_dates\": [\"YYYY-MM-DD\", ...]}"
)
# The cascade's default generation params (intelligence/cascade/core.py CascadeTask.gen_params).
LLM_GEN_PARAMS: dict[str, Any] = {"temperature": 0, "reasoning_effort": "minimal", "thinking": "off"}


def sonnet_model_id() -> str:
    """The Sonnet rung of the live U-701b ladder V2 (no new model id is hardcoded here)."""
    from intelligence.cascade.ladder_config import DEFAULT_BILL_CODING_LADDER_V2

    rungs = [r.model for r in DEFAULT_BILL_CODING_LADDER_V2 if r.provider == "anthropic" and "sonnet" in r.model]
    if len(rungs) != 1:
        raise RuntimeError("U-750: expected exactly one Sonnet rung in DEFAULT_BILL_CODING_LADDER_V2")
    return rungs[0]


def build_llm_prompt(
    day: CrewDay,
    evidence: Sequence[LaborLine],
    catalog: Mapping[int, SccRef],
) -> tuple[str, dict[int, str]]:
    """User prompt + the allowed {scc id: code number} map (codes appearing in the evidence).
    The SCC id is shown beside the number and must be returned, so colliding numbers stay resolvable."""
    notes_by_group: dict[tuple, str] = {}
    for ln in evidence:
        if ln.note_text and ln.group_key not in notes_by_group:
            notes_by_group[ln.group_key] = ln.note_text
    groups = sorted(
        precedent_groups(evidence),
        key=lambda g: (workday_distance(g.line_date, day.line_date), g.line_date, g.contract_labor_id),
    )[:LLM_MAX_PRECEDENTS]
    groups = sorted(groups, key=lambda g: (g.line_date, g.contract_labor_id))
    allowed: dict[int, str] = {}
    for ln in evidence:
        ref = catalog.get(ln.label_scc_id)
        if ref is not None:
            allowed[ref.id] = ref.number.strip()
    out = [
        f"Uncoded day: {day.line_date.isoformat()}",
        f"Total hours: {day.total_hours}",
        f"Notes: {day.note_text or '(none)'}",
        "",
        "Code catalog (codes seen in the precedents):",
    ]
    for sid in sorted(allowed, key=lambda s: (allowed[s], s)):
        out.append(f"- id {sid} | number {allowed[sid]} | {catalog[sid].name}")
    out += ["", "Precedent crew-member days (same project):"]
    for g in groups:
        codes = ", ".join(
            f"id {s} ({catalog[s].number if s in catalog else '?'}): {h}h" for s, h in g.hours
        )
        note = notes_by_group.get((g.contract_labor_id, day.project_id, g.line_date)) or "(no notes)"
        out.append(f"- {g.line_date.isoformat()} | {codes} | notes: {note}")
    return "\n".join(out), allowed


def _num_value(x: Any) -> Optional[Decimal]:
    try:
        v = Decimal(str(x).strip())
    except Exception:  # noqa: BLE001
        return None
    return v if v.is_finite() else None


def resolve_llm_code(item: Mapping[str, Any], allowed: Mapping[int, str]) -> tuple[Optional[int], str]:
    """id first (authoritative; a contradicting number is invalid_shape); else the number: exact
    string, then numeric ("35", 35, 35.0 -> "35.00") only when exactly one allowed code matches."""
    sid, num = item.get("sub_cost_code_id"), item.get("sub_cost_code_number")
    if sid is not None:
        if isinstance(sid, bool) or not isinstance(sid, (int, str)) or not str(sid).strip().isdigit():
            return None, "invalid_shape"
        sid = int(str(sid).strip())
        if sid not in allowed:
            return None, "unknown_code"
        if num is not None and _num_value(num) is not None and _num_value(num) != _num_value(allowed[sid]):
            return None, "invalid_shape"
        return sid, "ok"
    if num is None or isinstance(num, bool) or not isinstance(num, (str, int, float)):
        return None, "invalid_shape"
    exact = [s for s, n in allowed.items() if n == str(num).strip()]
    if not exact:
        v = _num_value(num)
        exact = [s for s, n in allowed.items() if v is not None and _num_value(n) == v]
    if len(exact) == 1:
        return exact[0], "ok"
    return None, ("ambiguous_code" if exact else "unknown_code")


def parse_llm_response(
    text: Optional[str], allowed: Mapping[int, str], total_hours: Decimal
) -> tuple[Optional[Proposal], str]:
    """(proposal or None, status). Anything malformed is an abstain with a counted status."""
    from intelligence.cascade.core import _parse_json_object

    if not text:
        return None, "invalid_json"
    try:
        obj = _parse_json_object(text)
    except Exception:  # noqa: BLE001 - defensive: any parse failure is an abstain
        obj = None
    if not isinstance(obj, dict):
        return None, "invalid_json"
    kind = obj.get("proposal")
    if kind == "abstain":
        return None, "abstain"
    if kind not in ("single", "split"):
        return None, "invalid_shape"
    raw = obj.get("lines")
    if not isinstance(raw, list) or not raw:
        return None, "invalid_shape"
    pairs: list[tuple[int, Decimal]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None, "invalid_shape"
        scc, status = resolve_llm_code(item, allowed)
        if scc is None:
            return None, status
        hrs = item.get("hours")
        if isinstance(hrs, bool) or not isinstance(hrs, (int, float, str)):
            return None, "invalid_shape"
        h = _num_value(hrs)
        if h is None or h <= 0:
            return None, "invalid_shape"
        pairs.append((scc, h))
    if len({s for s, _ in pairs}) != len(pairs):
        return None, "invalid_shape"  # duplicate codes
    lines = tuple(sorted(pairs))
    if kind == "single":
        if len(lines) != 1:
            return None, "invalid_shape"
        return single(lines[0][0], total_hours, "arm_b"), "ok"
    if len(lines) < 2:
        return None, "invalid_shape"
    if abs(sum(h for _, h in lines) - total_hours) > HOURS_TOL:
        return None, "hours_mismatch"
    return Proposal("split", lines, "arm_b"), "ok"


def prompt_hash(model: str, system: str, user: str) -> str:
    """Cache key: harness version, model, max_tokens, gen params, system + user prompt."""
    params = json.dumps(LLM_GEN_PARAMS, sort_keys=True)
    blob = f"{MODEL_VERSION}\n{model}\n{LLM_MAX_TOKENS}\n{params}\n{system}\n{user}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def estimate_call_cost_usd(model: str, system: str, user: str) -> Decimal:
    from intelligence.observability.pricing import compute_cost_usd
    from intelligence.transport.base import Usage

    tokens_in = math.ceil((len(system) + len(user)) / CHARS_PER_TOKEN_ESTIMATE)
    cost = compute_cost_usd(
        provider="anthropic", model=model,
        usage=Usage(input_tokens=tokens_in, output_tokens=LLM_MAX_TOKENS),
    )
    if cost is None:
        raise RuntimeError(f"U-750: no pricing for {model}; refusing to estimate")
    return (Decimal(f"{cost:.6f}") * ESTIMATE_PAD).quantize(Decimal("0.000001"))


class LlmBudgetAbort(RuntimeError):
    def __init__(self, token: str) -> None:
        super().__init__(f"Claude budget gate skipped a call (token={token!r})")
        self.token = token


class LlmBudgetRefused(RuntimeError):
    pass


class LlmTransportAbort(RuntimeError):
    """Too many non-budget transport errors, or too many truncated (max_tokens) answers."""


class LlmCache:
    """On-disk response cache: <out-dir>/llm_cache/<sha256>.json (holds model text, never committed)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> Optional[str]:
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))["text"]
        except (ValueError, KeyError, OSError):
            return None

    def put(self, key: str, text: str, usage: Mapping[str, int]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps({"text": text, "usage": dict(usage)}), encoding="utf-8")
        tmp.replace(self._path(key))


@dataclass
class LlmResult:
    text: Optional[str]
    usage: dict[str, int]
    error: Optional[str]
    stop_reason: Optional[str]


async def call_llm(transport: Any, model: str, system: str, user: str) -> LlmResult:
    """One completion through the transport (which runs the U-702 pre-call gate + metering).
    A claude_unavailable:* error raises LlmBudgetAbort."""
    from intelligence.messages.types import Message, Text
    from intelligence.transport.base import Done, TextDelta, TransportError, TurnEnd

    parts: list[str] = []
    usage: dict[str, int] = {}
    error: Optional[str] = None
    stop: Optional[str] = None
    async for ev in transport.stream(
        [Message(role="user", content=[Text(text=user)])],
        model=model, system=system, max_tokens=LLM_MAX_TOKENS, extra_body=dict(LLM_GEN_PARAMS),
    ):
        if isinstance(ev, TextDelta):
            parts.append(ev.text)
        elif isinstance(ev, TurnEnd):
            stop = ev.stop_reason
        elif isinstance(ev, Done):
            usage = {"input_tokens": ev.usage.input_tokens, "output_tokens": ev.usage.output_tokens}
        elif isinstance(ev, TransportError):
            if ev.code and ev.code.startswith("claude_unavailable:"):
                raise LlmBudgetAbort(ev.code.split(":", 1)[1])
            error = ev.code or "transport_error"
    return LlmResult(None if error else "".join(parts), usage, error, stop)


@dataclass
class LlmState:
    calls: int = 0
    cache_hits: int = 0
    not_run: int = 0
    transport_errors: int = 0
    truncated: int = 0
    statuses: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    @property
    def excluded(self) -> int:
        return self.transport_errors


def transport_error_limit(attempted: int) -> int:
    return max(3, math.floor(0.05 * attempted))


def _isolated_spend() -> Decimal:
    from intelligence.cascade import claude_budget

    return claude_budget.get_isolated_spend_total() or Decimal("0")


def run_arm_b(
    jobs: Sequence[tuple[CrewDay, Sequence[LaborLine]]],
    catalog: Mapping[int, SccRef],
    *,
    transport: Any,
    model: str,
    cache: LlmCache,
    budget_usd: Decimal,
    max_calls: int,
    spend_fn: Callable[[], Decimal] = _isolated_spend,
) -> tuple[dict[tuple, Optional[Proposal]], LlmState, Decimal]:
    """Estimate first (refuse when spend so far + the estimate exceeds the budget), then call or
    replay from cache. Before EACH uncached call: spend so far + that call's estimate must fit.
    Days beyond --max-calls are not run; days hit by a non-budget transport error are excluded
    (never scored); too many such errors abort. Truncated (max_tokens) answers are never cached."""
    prompts = []
    for day, ev in jobs:
        user, allowed = build_llm_prompt(day, ev, catalog)
        prompts.append((day, user, allowed, prompt_hash(model, LLM_SYSTEM, user)))
    all_uncached = [p for p in prompts if cache.get(p[3]) is None]
    uncached = all_uncached[:max_calls]
    estimate = sum((estimate_call_cost_usd(model, LLM_SYSTEM, u) for _, u, _, _ in uncached), Decimal("0"))
    if spend_fn() + estimate > budget_usd:
        raise LlmBudgetRefused(
            f"U-750: estimated ${estimate:.2f} for {len(uncached)} uncached calls plus ${spend_fn():.2f} "
            f"already spent exceeds --budget-usd {budget_usd}; refusing to call"
        )
    state = LlmState()
    out: dict[tuple, Optional[Proposal]] = {}
    planned = min(len(prompts), len(prompts) - len(all_uncached) + max_calls)

    async def _go() -> None:
        for day, user, allowed, key in prompts:
            text = cache.get(key)
            if text is not None:
                state.cache_hits += 1
            else:
                if state.calls >= max_calls:
                    state.not_run += 1
                    continue
                call_est = estimate_call_cost_usd(model, LLM_SYSTEM, user)
                if spend_fn() + call_est > budget_usd:
                    raise LlmBudgetAbort("per_call_estimate_exceeds_budget")
                state.calls += 1
                res = await call_llm(transport, model, LLM_SYSTEM, user)
                if res.error is not None or res.text is None:
                    state.transport_errors += 1
                    state.statuses["transport_error"] += 1
                    if state.transport_errors > transport_error_limit(planned):
                        raise LlmTransportAbort(
                            f"U-750: {state.transport_errors} non-budget transport errors "
                            f"(limit {transport_error_limit(planned)} of {planned} planned)"
                        )
                    continue  # excluded from the scored denominator
                if res.stop_reason == "max_tokens":
                    state.truncated += 1
                    state.statuses["truncated"] += 1
                    out[day.key] = None  # a model answer that did not finish: abstain, not cached
                    if state.truncated > transport_error_limit(planned):
                        raise LlmTransportAbort(
                            f"U-750: {state.truncated} truncated (max_tokens) answers "
                            f"(limit {transport_error_limit(planned)} of {planned} planned)"
                        )
                    continue
                cache.put(key, res.text, res.usage)
                text = res.text
            prop, status = parse_llm_response(text, allowed, day.total_hours)
            state.statuses[status] += 1
            out[day.key] = prop
        attempted = state.calls + state.cache_hits
        if state.truncated > transport_error_limit(attempted):
            raise LlmTransportAbort(
                f"U-750: {state.truncated} truncated (max_tokens) answers exceed "
                f"max(3, 5% of {attempted} attempted)"
            )
        if state.transport_errors > transport_error_limit(attempted):
            raise LlmTransportAbort(
                f"U-750: {state.transport_errors} non-budget transport errors exceed "
                f"max(3, 5% of {attempted} attempted)"
            )

    asyncio.run(_go())
    return out, state, estimate


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


@dataclass
class Corpus:
    lines: list[LaborLine]
    catalog: list[SccRef]
    project_abbr: dict[int, str]

    def free_texts(self) -> list[Optional[str]]:
        out: list[Optional[str]] = []
        for ln in self.lines:
            out.append(ln.note_text)
            out += split_segments(ln.note_text)
        out += [c.name for c in self.catalog]
        return out


def load_corpus_from_db() -> Corpus:
    lines_r, logs_r, coded_r, scc_r, proj_r = _select_all(
        [_LINES_SQL, _TIMELOG_NOTES_SQL, _CODED_AT_SQL, _SCC_SQL, _PROJECT_SQL]
    )
    return Corpus(
        lines=build_lines(lines_r, [], logs_r, coded_r),  # stored Description never loaded
        catalog=[SccRef(int(r.Id), str(r.Number), str(r.Name), r.Aliases) for r in scc_r],
        project_abbr={int(r.Id): str(r.Abbreviation) for r in proj_r if r.Abbreviation is not None},
    )


def load_population_counts(
    cal: tuple[date, date] = (DEFAULT_CAL_SINCE, DEFAULT_CAL_UNTIL),
    hold: tuple[date, date] = (DEFAULT_HOLD_SINCE, DEFAULT_HOLD_UNTIL),
) -> dict[str, Any]:
    (rows,) = _select_all([_DAY_COUNTS_SQL])
    return day_population_counts(
        [(_parse_date(r.LineDate), int(r.Codes or 0), bool(r.TimeLogSourced)) for r in rows], cal, hold
    )


def day_population_counts(
    rows: Sequence[tuple[date, int, bool]], cal: tuple[date, date], hold: tuple[date, date]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, (s, u) in (("all", (date.min, date.max)), ("calibration", cal), ("holdout", hold)):
        rs = [r for r in rows if s <= r[0] <= u]
        tl = [r for r in rs if r[2]]
        out[name] = {
            "days": len(rs),
            "split_days": sum(1 for r in rs if r[1] >= 2),
            "timelog_days": len(tl),
            "timelog_split_days": sum(1 for r in tl if r[1] >= 2),
        }
    return out


def _lines_json(lines: Iterable[tuple[int, Decimal]], catalog: Mapping[int, SccRef]) -> list[dict[str, Any]]:
    return [{"scc_id": s, "code": catalog[s].number if s in catalog else None, "hours": float(h)} for s, h in lines]


def _proposal_json(p: Optional[Proposal], catalog: Mapping[int, SccRef]) -> Any:
    if p is None:
        return {"kind": "abstain"}
    return {"kind": p.kind, "lines": _lines_json(p.lines, catalog), "flags": sorted(p.flags)}


def run_backtest(
    corpus: Corpus,
    *,
    ks: Sequence[int],
    cal: tuple[date, date],
    hold: tuple[date, date],
    llm: Optional[Callable[[Sequence[tuple[CrewDay, Sequence[LaborLine]]], int], tuple[dict, LlmState, Decimal]]] = None,
    llm_ks: Sequence[int] = (HEADLINE_K,),
) -> dict[str, Any]:
    lines = corpus.lines
    catalog = {c.id: c for c in corpus.catalog}
    tokens = {ln.line_id: tokenize_labor_text(ln.note_text) for ln in lines}
    idf = compute_idf_from_doc_token_sets([t for t in tokens.values() if t])  # label-free (U-673 limit)
    weights = {lid: sum(idf.get(t, 0.0) for t in toks) for lid, toks in tokens.items()}
    by_project: dict[Optional[int], list[LaborLine]] = defaultdict(list)
    for ln in lines:
        by_project[ln.project_id].append(ln)

    all_days = build_crew_days(lines)
    days = [d for d in all_days if d.timelog_sourced]
    cal_days = [d for d in days if cal[0] <= d.line_date <= cal[1]]
    hold_days = [d for d in days if hold[0] <= d.line_date <= hold[1]]

    if not set(llm_ks) <= set(ks):
        raise ValueError("llm_ks must be a subset of ks")
    base = baseline_proposals(cal_days + hold_days, lines, tokens, weights, idf)
    arms: dict[str, Any] = {}
    for name, props in base.items():
        arms[name] = {}
        for win, ds in (("calibration", cal_days), ("holdout", hold_days)):
            mt = compute_metrics([(d, props[d.key]) for d in ds], ks)
            arms[name][win] = {"metrics": mt, "verdict": verdicts(mt, ks)}

    hp2_ids = {pid for pid, ab in corpus.project_abbr.items() if ab.strip().upper() == NAMED_CASE[0]}
    named_days = [d for d in days if d.project_id in hp2_ids and d.line_date == NAMED_CASE[1]]
    named: dict[tuple, dict[str, Any]] = {
        d.key: {
            "contract_labor_id": d.contract_labor_id,
            "project_id": d.project_id,
            "line_date": d.line_date.isoformat(),
            "truth": _lines_json(d.truth, catalog),
            "proposals": {n: _proposal_json(base[n].get(d.key), catalog) for n in base},
        }
        for d in named_days
    }

    def _analyze(ds: Sequence[CrewDay], k: int, **kw: Any) -> list[tuple[DayAnalysis, list[LaborLine]]]:
        out_ = []
        for d in ds:
            ev = evidence_for_day(d, by_project[d.project_id], k_days=k, **kw)
            out_.append((analyze_day(d, ev, tokens, weights, idf), ev))
        return out_

    arm_a: dict[str, Any] = {}
    hold_by_k: dict[int, tuple[list[tuple[DayAnalysis, list[LaborLine]]], float, int]] = {}
    for k in ks:
        cal_an = [a for a, _ in _analyze(cal_days, k, evidence_until=cal[1])]
        fit = fit_thresholds(cal_an)
        tau, m = fit["tau"], fit["m"]
        hold_pairs = _analyze(hold_days, k)
        hold_by_k[k] = (hold_pairs, tau, m)
        cal_rows = [(a.day, propose_arm_a(a, tau, m)) for a in cal_an]
        hold_rows = [(a.day, propose_arm_a(a, tau, m)) for a, _ in hold_pairs]
        past_rows = [(a.day, propose_arm_a(a, tau, m)) for a, _ in _analyze(hold_days, k, past_only=True)]
        cal_m = compute_metrics(cal_rows, [k])
        hold_m = compute_metrics(hold_rows, [k])
        past_m = compute_metrics(past_rows, [k])
        arm_a[f"k{k}"] = {
            "fit": fit,
            "calibration": {"metrics": cal_m, "verdict": verdicts(cal_m, [k])},
            "holdout": {
                "metrics": hold_m,
                "verdict": verdicts(hold_m, [k]),
                "evidence_provenance": evidence_provenance((a.day, ev) for a, ev in hold_pairs),
                "split_precedent_provenance": precedent_provenance(hold_rows),
            },
            "sensitivity_past_only_priors": {
                "metrics": past_m,
                "verdict": verdicts(past_m, [k]),
                "split_precedent_provenance": precedent_provenance(past_rows),
            },
        }
        for a, _ in _analyze(named_days, k):
            named[a.day.key]["proposals"][f"arm_a_k{k}"] = _proposal_json(propose_arm_a(a, tau, m), catalog)
    arms["arm_a"] = arm_a

    if llm is None:
        arms["arm_b"] = {"status": "skipped_no_llm_run"}
    else:
        arm_b: dict[str, Any] = {"status": "ran"}
        for k in llm_ks:
            hold_pairs, tau, m = hold_by_k[k]
            jobs = [(a.day, ev) for a, ev in hold_pairs]  # the same holdout evidence arm A scored
            props, state, estimate = llm(jobs, k)
            attempted = [d for d in hold_days if d.key in props]
            att_keys = {d.key for d in attempted}
            incomplete = len(attempted) < len(hold_days)
            mt = compute_metrics([(d, props[d.key]) for d in attempted], [k])
            paired = compute_metrics([(a.day, propose_arm_a(a, tau, m)) for a, _ in hold_pairs if a.day.key in att_keys], [k])
            arm_b[f"k{k}"] = {
                "holdout": {
                    "metrics": mt,
                    "verdict": verdicts(mt, [k], incomplete=incomplete),
                    "evidence_provenance": evidence_provenance(jobs),
                },
                "paired_arm_a_same_days": {"metrics": paired, "verdict": verdicts(paired, [k])},
                "holdout_days": len(hold_days),
                "days_attempted": len(attempted),
                "days_not_run_max_calls": state.not_run,
                "days_excluded_transport_error": state.transport_errors,
                "truncated": state.truncated,
                "calls": state.calls,
                "cache_hits": state.cache_hits,
                "response_statuses": dict(state.statuses),
                "estimated_cost_usd": float(estimate),
            }
            if incomplete:
                arm_b["status"] = "incomplete"
            for d in named_days:
                if d.key in props:
                    named[d.key]["proposals"][f"arm_b_k{k}"] = _proposal_json(props[d.key], catalog)
        arms["arm_b"] = arm_b

    return {
        "population": {
            "labelled_lines": len(lines),
            "crew_days": len(all_days),
            "crew_days_timelog": len(days),
            "excluded_excel_era_days": len(all_days) - len(days),
            "calibration_days": len(cal_days),
            "calibration_split_days": sum(1 for d in cal_days if d.is_split),
            "holdout_days": len(hold_days),
            "holdout_split_days": sum(1 for d in hold_days if d.is_split),
            **{f"holdout_{uncoded_key(k)}_days": sum(1 for d in hold_days if d.uncoded_at(k)) for k in ks},
        },
        "windows": {
            "calibration": [cal[0].isoformat(), cal[1].isoformat()],
            "holdout": [hold[0].isoformat(), hold[1].isoformat()],
        },
        "params": {
            "ks": list(ks),
            "headline_k": HEADLINE_K,
            "evidence_window_workdays": EVIDENCE_WINDOW_WORKDAYS,
            "tau_grid": [float(t) for t in TAU_GRID],
            "m_grid": list(M_GRID),
            "min_support": MIN_SUPPORT,
            "bar": {"exact_correct": BAR_EXACT, "false_split_rate": BAR_FALSE_SPLIT, "coverage": BAR_COVERAGE},
        },
        "arms": arms,
        "named_case": {
            "project_abbreviation": NAMED_CASE[0],
            "line_date": NAMED_CASE[1].isoformat(),
            "crew_days": list(named.values()),
        },
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_markdown(result: Mapping[str, Any], stamp: str, git_sha: Optional[str]) -> str:
    out = [
        f"# U-750 labor follow-up coding backtest ({stamp})",
        "",
        f"- model `{MODEL_VERSION}` - git `{git_sha or 'unknown'}`",
        f"- windows: `{json.dumps(result['windows'])}`",
        f"- population: `{json.dumps(result['population'])}`",
        "",
        "## Holdout headline",
        "",
        "`uncoded@T` = days whose own ContractLabor was still uncoded at T = LineDate + K: the "
        "**realistic population** a follow-up email would see. Its verdict sits next to the all-days one.",
        "",
        "| arm | K | coverage | exact | set | false-split | missed-split | proposals | verdict (all days) "
        "| uncoded@T days | uncoded@T coverage | uncoded@T exact | uncoded@T proposals "
        "| verdict (uncoded@T, realistic) | fit status | cal proposals |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    arms = result["arms"]
    ks = result["params"]["ks"]
    base_k = HEADLINE_K if HEADLINE_K in ks else ks[0]
    rows: list[tuple[str, int, Mapping[str, Any], Optional[Mapping[str, Any]]]] = []
    for kk, v in arms["arm_a"].items():
        rows.append(("arm_a", int(kk[1:]), v["holdout"], v["fit"]))
        rows.append(("arm_a_past_only_priors", int(kk[1:]), v["sensitivity_past_only_priors"], v["fit"]))
    if isinstance(arms.get("arm_b"), dict):
        for kk, v in arms["arm_b"].items():
            if isinstance(v, dict) and "holdout" in v:
                rows.append(("arm_b", int(kk[1:]), v["holdout"], None))
                rows.append(("arm_a_paired_with_arm_b", int(kk[1:]), v["paired_arm_a_same_days"], None))
    for name in ("baseline_s0_vendor_project", "baseline_s1_w7_at_submit"):
        rows.append((name, base_k, arms[name]["holdout"], None))
    for name, k, h, fit in rows:
        a = h["metrics"]["all"]
        u = h["metrics"][uncoded_key(k)]
        fs = fit["status"] if fit else "-"
        fp = fit["calibration_proposals"] if fit else "-"
        out.append(
            f"| {name} | {k} | {_pct(a['coverage'])} | {_pct(a['exact_correct'])} | {_pct(a['set_correct'])} "
            f"| {_pct(a['false_split_rate'])} | {_pct(a['missed_split_rate'])} | {a['proposals']} "
            f"| {h['verdict']['all']} | {u['days']} | {_pct(u['coverage'])} | {_pct(u['exact_correct'])} "
            f"| {u['proposals']} | {h['verdict'][uncoded_key(k)]} | {fs} | {fp} |"
        )
    out += [
        "",
        f"Arm B status: `{arms['arm_b'].get('status')}`",
        "",
        "## Full results",
        "",
        "```json",
        json.dumps(result, indent=2, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(out)


def write_outputs(
    result: Mapping[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    forbidden_texts: Iterable[Optional[str]],
    git_sha: Optional[str] = None,
) -> tuple[Path, Path]:
    """Fail-closed (U-673 discipline): temp names, guard BOTH, rename only after both pass.
    The llm_cache/ subdirectory is NOT a report output and is not guarded (it holds prompts)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"u750_report_{stamp}.md"
    js = out_dir / f"u750_report_{stamp}.json"
    md_tmp = out_dir / f".u750_partial_{stamp}.md"
    js_tmp = out_dir / f".u750_partial_{stamp}.json"
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


def _parse_ks(raw: str) -> tuple[int, ...]:
    try:
        ks = tuple(sorted({int(x) for x in raw.split(",") if x.strip()}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"bad K list: {raw!r}") from exc
    if not ks or any(k < 0 for k in ks):
        raise argparse.ArgumentTypeError(f"bad K list: {raw!r}")
    return ks


def main(
    argv: Optional[list[str]] = None,
    *,
    counts_loader: Callable[..., dict[str, Any]] = load_population_counts,
    corpus_loader: Callable[[], Corpus] = load_corpus_from_db,
    transport_factory: Optional[Callable[[], Any]] = None,
) -> int:
    ap = argparse.ArgumentParser(description="U-750 labor follow-up coding backtest (read-only).")
    ap.add_argument("--run", action="store_true", help="Score arm A + baselines (default: dry-run counts).")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--k", type=_parse_ks, default=DEFAULT_KS, help="Follow-up delays in days, e.g. 3,7,14.")
    ap.add_argument("--cal-since", type=date.fromisoformat, default=DEFAULT_CAL_SINCE)
    ap.add_argument("--cal-until", type=date.fromisoformat, default=DEFAULT_CAL_UNTIL)
    ap.add_argument("--holdout-since", type=date.fromisoformat, default=DEFAULT_HOLD_SINCE)
    ap.add_argument("--holdout-until", type=date.fromisoformat, default=DEFAULT_HOLD_UNTIL)
    ap.add_argument("--llm-run", action="store_true", help="Add arm B (paid Sonnet calls).")
    ap.add_argument("--budget-usd", type=Decimal, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    ap.add_argument("--llm-k", type=_parse_ks, default=(HEADLINE_K,), help="K values arm B runs (default 7).")
    args = ap.parse_args(argv)
    validate_windows(args.cal_since, args.cal_until, args.holdout_since, args.holdout_until)
    if args.cal_until >= args.holdout_since:
        ap.error("calibration must end before the holdout starts")
    cal = (args.cal_since, args.cal_until)
    hold = (args.holdout_since, args.holdout_until)

    if not args.run:
        if args.llm_run:
            ap.error("--llm-run requires --run")
        print("U-750 dry-run population: " + json.dumps(counts_loader(cal, hold), sort_keys=True))
        return 0
    if args.out_dir is None:
        ap.error("--run requires --out-dir (use /Users/chris/Applications/build.one/.data/u750)")
    out_dir = args.out_dir.resolve()
    data_root = DATA_ROOT.resolve()
    if out_dir != data_root and data_root not in out_dir.parents:
        ap.error(f"--out-dir must resolve under {DATA_ROOT} (outputs and the LLM cache are never committed)")

    llm = None
    if args.llm_run:
        if not args.budget_usd.is_finite() or args.budget_usd <= 0:
            ap.error("--budget-usd must be > 0")
        if args.max_calls < 1:
            ap.error("--max-calls must be >= 1")
        if not set(args.llm_k) <= set(args.k):
            ap.error("--llm-k must be a subset of --k")
        from intelligence.cascade import claude_budget

        model = sonnet_model_id()
        cache = LlmCache(out_dir / "llm_cache")
        if transport_factory is None:
            from intelligence.transport.anthropic import AnthropicTransport

            transport_factory = AnthropicTransport
        transport = transport_factory()

        def llm(jobs, k):  # noqa: ANN001 - budget = the whole run's cap; run_arm_b subtracts spend so far
            return run_arm_b(
                jobs, catalog_map, transport=transport, model=model, cache=cache,
                budget_usd=args.budget_usd, max_calls=args.max_calls,
            )

    corpus = corpus_loader()
    catalog_map = {c.id: c for c in corpus.catalog}
    if args.llm_run:
        claude_budget.use_isolated_spend_ledger(args.budget_usd)
    try:
        try:
            result = run_backtest(corpus, ks=args.k, cal=cal, hold=hold, llm=llm, llm_ks=args.llm_k)
        except LlmBudgetRefused as exc:
            print(str(exc))
            return 2
        except (LlmBudgetAbort, LlmTransportAbort) as exc:
            print(f"ABORT: {exc}. Cached responses are kept under llm_cache/; re-run to resume.")
            return 1
        if args.llm_run:
            total = claude_budget.get_isolated_spend_total()
            result["arms"]["arm_b"]["model"] = model
            result["arms"]["arm_b"]["spend_ledger"] = "isolated"
            result["arms"]["arm_b"]["isolated_spend_total_usd"] = float(total or 0)
            result["arms"]["arm_b"]["budget_usd"] = float(args.budget_usd)
    finally:
        if args.llm_run:
            claude_budget.clear_isolated_spend_ledger()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    md, _ = write_outputs(result, out_dir=out_dir, stamp=stamp, forbidden_texts=corpus.free_texts(), git_sha=_git_sha())
    print(f"U-750: {len(corpus.lines)} lines; report={md.name}")
    return 0


if __name__ == "__main__":
    from scripts.sync_helper import assert_cli_system_admin

    assert_cli_system_admin()
    raise SystemExit(main())
