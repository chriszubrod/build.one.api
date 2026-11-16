"""Contract Labor coding shadow: the pure ladder (U-797a). No DB, no LLM, no I/O.

Ported (copied, never imported from scripts/) from:
  scripts/u673_labor_coding_backtest.py  rebuild_note_text, split_segments, LABOR_STOP_WORDS
  scripts/u750_labor_followup_backtest.py  arm A (IDF Jaccard, SegmentScore.pick), LLM prompt,
                                          parse/resolve (TAU/M grids, HOURS_TOL, MaxPrecedents)
  scripts/u751_single_code_rescore.py     arm_a_resolved_codes, the split-flag OR
The shared tokenizer and IDF Jaccard are imported from the bill similarity module (no copy).

Ladder (decide): history if strong (>= 3 priors AND top share >= 60 %) -> no model call;
else model single -> that code; model split -> code with most hours, MaySplit; abstain,
unavailable or invalid -> blank. MaySplit = model split OR arm A resolves >= 2 codes.
Confidence is the history share on the history rung and NULL on the model rung (see the
U-797a deviations note in docs/design/u797a-cl-coding-shadow.md).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from entities.coding_suggestion.business.similar_bills import (
    compute_idf_from_doc_token_sets,
    idf_weighted_jaccard,
    tokenize_bill_text,
)

MODEL_VERSION = "u797a-cl-v1"

# Frozen from the U-751 K=7 calibration run.
TAU = 0.60
M = 2

from entities.contract_labor.business.code_suggestions import MIN_PRIORS, TOP_N

HISTORY_MIN_PRIORS = MIN_PRIORS  # the U-749 picker's threshold: the shadow must match what it shadows
HISTORY_STRONG_SHARE_PCT = 60  # top share >= 60 %, compared as integers (no float)
TOP_VENDOR_CODES = TOP_N
PRECEDENT_MAX = 30
PRECEDENT_WINDOW_DAYS = 14
LLM_MAX_TOKENS = 600
HOURS_TOL = Decimal("0.01")

# The U-673 labelled set. A ContractLabor already in one of these is coded: its own row is skipped.
LABELLED_CL_STATUSES = frozenset({"approved", "completed", "ready", "billed"})

# The cascade's default generation params (intelligence/cascade/core.py CascadeTask.gen_params).
LLM_GEN_PARAMS: dict[str, Any] = {"temperature": 0, "reasoning_effort": "minimal", "thinking": "off"}

LABOR_STOP_WORDS = frozenset(
    "we our us i me my he she they them their his her you your this that these those "
    "today yesterday tomorrow day days morning afternoon all also some more rest "
    "work worked working did do done doing got get getting go went going help helped "
    "helping started start continue continued continuing finished finish finishing "
    "out up off over into onto then there here back still just again lot lots "
    "hours hour hrs hr time".split()
)

_SEGMENT_RE = re.compile(r";|\n|\r|,|\band\b", re.IGNORECASE)

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


# --- value types ----------------------------------------------------------


@dataclass(frozen=True)
class CatalogEntry:
    id: int
    number: str
    name: str


@dataclass(frozen=True)
class PrecedentDay:
    """One precedent crew-member day on the same project: its coded lines and rebuilt note."""

    contract_labor_id: int
    line_date: date
    lines: tuple[tuple[int, Decimal], ...]
    note_text: Optional[str]


@dataclass(frozen=True)
class DayInputs:
    line_date: date
    project_id: int
    total_hours: Decimal
    note_text: Optional[str]


@dataclass(frozen=True)
class ShadowInputs:
    total_hours: Decimal
    day_note_text: Optional[str]
    precedent_days: tuple[PrecedentDay, ...]
    catalog: tuple[CatalogEntry, ...]
    own_status: Optional[str] = None  # the row's own ContractLabor.Status (None = unknown)


@dataclass(frozen=True)
class HistoryStats:
    top_sub_cost_code_id: int
    top_count: int
    prior_count: int

    @property
    def share(self) -> Decimal:
        return (Decimal(self.top_count) / Decimal(self.prior_count)).quantize(Decimal("0.0001"))


@dataclass(frozen=True)
class ModelAnswer:
    proposal: Optional[str]  # "single" | "split" | "abstain" | None (invalid / no call)
    lines: tuple[tuple[int, Decimal], ...] = ()
    status: str = "ok"


@dataclass(frozen=True)
class ShadowDecision:
    decision_source: str  # history | model | none
    sub_cost_code_id: Optional[int]
    confidence: Optional[Decimal]
    may_split: bool
    model_proposal: Optional[str]
    model_split_codes: tuple[int, ...] = ()
    failure_reason: Optional[str] = None
    history_top_sub_cost_code_id: Optional[int] = None
    history_share: Optional[Decimal] = None
    history_prior_count: Optional[int] = None
    arm_a_code_count: int = 0


# --- note text and segments (U-673) ---------------------------------------


def rebuild_note_text(logs: Iterable[tuple[datetime, Optional[str]]]) -> Optional[str]:
    """Mirror AggregateTimeEntryOnSubmit: trimmed non-empty notes ordered by ClockIn, '; '-joined."""
    parts = [
        n.strip() for _, n in sorted(logs, key=lambda x: x[0]) if n is not None and n.strip()
    ]
    return "; ".join(parts) if parts else None


def split_segments(text: Optional[str]) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in _SEGMENT_RE.split(text) if s and s.strip()]


def tokenize_labor_text(text: Optional[str]) -> frozenset[str]:
    """Bill tokenizer (U-691) minus labor filler words."""
    if not text:
        return frozenset()
    return frozenset(t for t in tokenize_bill_text(text) if t not in LABOR_STOP_WORDS)


def workdays_between(earlier: date, later: date) -> int:
    """Mon-Fri days in (earlier, later]; 0 when later <= earlier."""
    if later <= earlier:
        return 0
    full_weeks, rem = divmod((later - earlier).days, 7)
    n = full_weeks * 5
    d = earlier + timedelta(days=7 * full_weeks)
    for _ in range(rem):
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def workday_distance(a: date, b: date) -> int:
    """Symmetric Mon-Fri distance between two dates."""
    return workdays_between(min(a, b), max(a, b))


# --- history rung --------------------------------------------------------


def history_stats(counts: Mapping[int, int]) -> Optional[HistoryStats]:
    """Top code and prior total from per-code prior counts (count desc, id asc for the top)."""
    positive = [(scc, n) for scc, n in counts.items() if n > 0]
    if not positive:
        return None
    prior_count = sum(n for _, n in positive)
    top, top_count = min(positive, key=lambda item: (-item[1], item[0]))
    return HistoryStats(top_sub_cost_code_id=top, top_count=top_count, prior_count=prior_count)


def is_history_strong(stats: Optional[HistoryStats]) -> bool:
    if stats is None:
        return False
    if stats.prior_count < HISTORY_MIN_PRIORS:
        return False
    return stats.top_count * 100 >= HISTORY_STRONG_SHARE_PCT * stats.prior_count


def top_codes(counts: Mapping[int, int], n: int = TOP_VENDOR_CODES) -> tuple[int, ...]:
    positive = sorted(((scc, c) for scc, c in counts.items() if c > 0), key=lambda x: (-x[1], x[0]))
    return tuple(scc for scc, _ in positive[:n])


def candidate_codes(precedent_codes: Iterable[int], vendor_top_codes: Iterable[int]) -> frozenset[int]:
    """Candidate catalog for the model: the precedent codes plus the vendor's top codes."""
    return frozenset(precedent_codes) | frozenset(vendor_top_codes)


def merge_day_lines(lines: Iterable[tuple[int, Optional[Decimal]]]) -> tuple[tuple[int, Decimal], ...]:
    """U-750 precedent_groups / _merge_hours: drop hours <= 0 (or NULL), sum hours per code."""
    acc: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for scc, hours in lines:
        if hours is None or hours <= 0:
            continue
        acc[scc] += hours
    return tuple(sorted(acc.items()))


def build_corpus_idf(line_note_rows: Iterable[tuple[int, datetime, Optional[str]]]) -> dict[str, float]:
    """IDF over the broad labelled corpus, as the U-750 backtest computes it.

    One document per labelled line. Rows are (line id, ClockIn, Note) of the line's day notes on
    its project; each line's notes are rebuilt exactly as AggregateTimeEntryOnSubmit does. Lines
    with no note tokens are not documents.
    """
    by_line: dict[int, list[tuple[datetime, Optional[str]]]] = defaultdict(list)
    for line_id, clock_in, note in line_note_rows:
        by_line[int(line_id)].append((clock_in, note))
    docs = [tokenize_labor_text(rebuild_note_text(notes)) for notes in by_line.values()]
    return compute_idf_from_doc_token_sets([d for d in docs if d])


# --- arm A (U-750 SegmentScore.pick, U-751 split OR) ----------------------


def arm_a_resolved_codes(
    day_note_text: Optional[str],
    precedent_days: Sequence[PrecedentDay],
    query_date: date,
    *,
    idf: Mapping[str, float],
    tau: float = TAU,
    m: int = M,
) -> frozenset[int]:
    """Distinct codes arm A resolves across the day's segments at (tau, m).

    The IDF is passed in (build_corpus_idf over the broad labelled corpus, as U-750 computes it).
    Evidence is one entry per (precedent day, code): that day's hours for the code merged, 0-hour
    codes dropped, carrying the day's note tokens. A segment's score for a code is the IDF-weighted
    Jaccard over evidence, damped by workday distance. A segment resolves when its top code holds
    >= tau of the score with >= m supporting day-code groups.
    """
    evidence: list[tuple[PrecedentDay, int, frozenset[str], float]] = []
    for day in precedent_days:
        toks = tokenize_labor_text(day.note_text)
        weight = sum(idf.get(t, 0.0) for t in toks)
        for scc, _hours in merge_day_lines(day.lines):
            evidence.append((day, scc, toks, weight))

    resolved: set[int] = set()
    for segment in split_segments(day_note_text):
        seg_tokens = tokenize_labor_text(segment)
        if not seg_tokens:
            continue  # token-less segments (filler only) are ignored, not "unresolved"
        scores: dict[int, float] = defaultdict(float)
        support: dict[int, int] = defaultdict(int)
        wq = sum(idf.get(t, 0.0) for t in seg_tokens)
        for day, scc, toks, weight in evidence:
            sim = idf_weighted_jaccard(seg_tokens, wq, toks, weight, idf)
            if sim <= 0:
                continue
            scores[scc] += sim / (1.0 + workday_distance(day.line_date, query_date))
            support[scc] += 1
        total = sum(scores.values())
        if total <= 0:
            continue
        top, score = min(scores.items(), key=lambda x: (-x[1], x[0]))
        if score / total + 1e-12 < tau or support.get(top, 0) < m:
            continue
        resolved.add(top)
    return frozenset(resolved)


# --- prompt (U-750) -------------------------------------------------------


def build_prompt(
    day: DayInputs,
    precedent_days: Sequence[PrecedentDay],
    catalog: Mapping[int, CatalogEntry],
    vendor_top: Iterable[int],
) -> tuple[str, dict[int, str]]:
    """User prompt and the allowed {scc id: code number} map.

    The candidate catalog is the precedent codes plus the vendor's top codes, each tagged with
    where it came from. Codes not in the catalog are dropped.
    """
    vendor = set(vendor_top)
    precedent = {scc for d in precedent_days for scc, _ in merge_day_lines(d.lines)}
    candidates = candidate_codes(precedent, vendor) & frozenset(catalog)
    allowed = {scc: catalog[scc].number.strip() for scc in candidates}

    out = [
        f"Uncoded day: {day.line_date.isoformat()}",
        f"Total hours: {day.total_hours}",
        f"Notes: {day.note_text or '(none)'}",
        "",
        "Code catalog (candidates: codes seen in the precedents and the vendor's top codes):",
    ]
    for scc in sorted(allowed, key=lambda s: (allowed[s], s)):
        if scc in precedent and scc in vendor:
            tag = "precedent+vendor"
        elif scc in precedent:
            tag = "precedent"
        else:
            tag = "vendor_top"
        out.append(f"- id {scc} | number {allowed[scc]} | {catalog[scc].name} | source: {tag}")

    # Only days with positive hours compete for the PRECEDENT_MAX slots (U-750 precedent_groups): an
    # all-zero day prints nothing, so it must not take a slot from a real precedent.
    with_hours = [d for d in precedent_days if merge_day_lines(d.lines)]
    chosen = sorted(
        with_hours,
        key=lambda d: (workday_distance(d.line_date, day.line_date), d.line_date, d.contract_labor_id),
    )[:PRECEDENT_MAX]
    chosen = sorted(chosen, key=lambda d: (d.line_date, d.contract_labor_id))
    out += ["", "Precedent crew-member days (same project):"]
    for d in chosen:
        codes = ", ".join(
            f"id {scc} ({catalog[scc].number.strip() if scc in catalog else '?'}): {hours}h"
            for scc, hours in merge_day_lines(d.lines)
        )
        out.append(f"- {d.line_date.isoformat()} | {codes} | notes: {d.note_text or '(no notes)'}")
    return "\n".join(out), allowed


# --- response parse / resolve (U-750) -------------------------------------


def _num(value: Any) -> Optional[Decimal]:
    try:
        v = Decimal(str(value).strip())
    except Exception:  # noqa: BLE001 - any non-number is simply not a number
        return None
    return v if v.is_finite() else None


def _resolve_code(item: Mapping[str, Any], allowed: Mapping[int, str]) -> tuple[Optional[int], str]:
    """id first (authoritative; a contradicting number is invalid_shape); else the number."""
    sid, num = item.get("sub_cost_code_id"), item.get("sub_cost_code_number")
    if sid is not None:
        if isinstance(sid, bool) or not isinstance(sid, (int, str)) or not str(sid).strip().isdigit():
            return None, "invalid_shape"
        sid = int(str(sid).strip())
        if sid not in allowed:
            return None, "unknown_code"
        if num is not None and _num(num) is not None and _num(num) != _num(allowed[sid]):
            return None, "invalid_shape"
        return sid, "ok"
    if num is None or isinstance(num, bool) or not isinstance(num, (str, int, float)):
        return None, "invalid_shape"
    exact = [s for s, n in allowed.items() if n == str(num).strip()]
    if not exact:
        v = _num(num)
        exact = [s for s, n in allowed.items() if v is not None and _num(n) == v]
    if len(exact) == 1:
        return exact[0], "ok"
    return None, ("ambiguous_code" if exact else "unknown_code")


def _loads_object(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return None
    try:
        obj = json.loads(match.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_model_response(
    text: Optional[str], allowed: Mapping[int, str], total_hours: Decimal
) -> ModelAnswer:
    """Strict parse of the U-750 JSON. Anything malformed is an invalid answer with a status."""
    obj = _loads_object(text)
    if obj is None:
        return ModelAnswer(proposal=None, status="invalid_json")
    kind = obj.get("proposal")
    if kind == "abstain":
        return ModelAnswer(proposal="abstain", status="abstain")
    if kind not in ("single", "split"):
        return ModelAnswer(proposal=None, status="invalid_shape")
    raw = obj.get("lines")
    if not isinstance(raw, list) or not raw:
        return ModelAnswer(proposal=None, status="invalid_shape")
    pairs: list[tuple[int, Decimal]] = []
    for item in raw:
        if not isinstance(item, dict):
            return ModelAnswer(proposal=None, status="invalid_shape")
        scc, status = _resolve_code(item, allowed)
        if scc is None:
            return ModelAnswer(proposal=None, status=status)
        hrs = item.get("hours")
        if isinstance(hrs, bool) or not isinstance(hrs, (int, float, str)):
            return ModelAnswer(proposal=None, status="invalid_shape")
        h = _num(hrs)
        if h is None or h <= 0:
            return ModelAnswer(proposal=None, status="invalid_shape")
        pairs.append((scc, h))
    if len({s for s, _ in pairs}) != len(pairs):
        return ModelAnswer(proposal=None, status="invalid_shape")  # duplicate codes
    lines = tuple(sorted(pairs))
    if kind == "single":
        if len(lines) != 1:
            return ModelAnswer(proposal=None, status="invalid_shape")
        return ModelAnswer(proposal="single", lines=lines, status="ok")
    if len(lines) < 2:
        return ModelAnswer(proposal=None, status="invalid_shape")
    if abs(sum(h for _, h in lines) - total_hours) > HOURS_TOL:
        return ModelAnswer(proposal=None, status="hours_mismatch")
    return ModelAnswer(proposal="split", lines=lines, status="ok")


# --- the ladder -----------------------------------------------------------


def decide(
    *,
    history: Optional[HistoryStats],
    model: Optional[ModelAnswer],
    arm_a_codes: frozenset[int],
    total_hours: Decimal,
) -> ShadowDecision:
    """The CL v1 ladder. `model` is None when no call was made or the call was unavailable."""
    arm_split = len(arm_a_codes) >= 2
    history_fields: dict[str, Any] = {
        "history_top_sub_cost_code_id": history.top_sub_cost_code_id if history else None,
        "history_share": history.share if history else None,
        "history_prior_count": history.prior_count if history else None,
        "arm_a_code_count": len(arm_a_codes),
    }

    if is_history_strong(history):
        return ShadowDecision(
            decision_source="history",
            sub_cost_code_id=history.top_sub_cost_code_id,
            confidence=history.share,
            may_split=arm_split,
            model_proposal=None,
            **history_fields,
        )

    if model is None:
        return ShadowDecision(
            decision_source="none",
            sub_cost_code_id=None,
            confidence=None,
            may_split=arm_split,
            model_proposal=None,
            failure_reason="model_unavailable",
            **history_fields,
        )

    if model.proposal == "abstain":
        return ShadowDecision(
            decision_source="none",
            sub_cost_code_id=None,
            confidence=None,
            may_split=arm_split,
            model_proposal="abstain",
            **history_fields,
        )

    if model.proposal == "single" and model.lines:
        return ShadowDecision(
            decision_source="model",
            sub_cost_code_id=model.lines[0][0],
            confidence=None,
            may_split=arm_split,
            model_proposal="single",
            **history_fields,
        )

    if model.proposal == "split" and model.lines:
        # Most hours wins; tie -> lower id (deterministic).
        scc, _ = min(model.lines, key=lambda x: (-x[1], x[0]))
        return ShadowDecision(
            decision_source="model",
            sub_cost_code_id=scc,
            confidence=None,
            may_split=True,
            model_proposal="split",
            model_split_codes=tuple(sorted(s for s, _ in model.lines)),
            **history_fields,
        )

    return ShadowDecision(
        decision_source="none",
        sub_cost_code_id=None,
        confidence=None,
        may_split=arm_split,
        model_proposal=None,
        failure_reason=model.status,
        **history_fields,
    )


# --- model identity (no hardcoded model id) -------------------------------


# The model U-750/U-751 measured. Pinned here rather than read from the bill ladder, so an edit to
# the bill ladder can never break (or silently re-point) the CL shadow.
SHADOW_MODEL = "claude-sonnet-5-5"


def sonnet_model_id() -> str:
    """The model the CL shadow calls (SHADOW_MODEL)."""
    return SHADOW_MODEL
