"""
U-775: multi-task-day DETECTOR for the single-code labor follow-up, re-scored end to end with U-751's
cached Sonnet single proposals. Read-only measurement.

Business case: U-751 (docs/design/u751/README.md) FAILED at 70 % exact. Its bottleneck is split DETECTION
(flag recall 34-40 %): with every split day flagged its proposals would be 83.5 % exact. U-775 builds three
detection signals, combines them under pre-registered rules and re-scores.

Population: exactly U-751's (`u751.build_population`: U-750 windows, TimeLog-sourced crew-days, K in
{7, 14}, headline K = 7, realistic stratum uncoded@T). Sonnet cache misses are excluded and counted.

Signals (booleans per holdout day per K):
  * S_text   ($0)  >= 2 distinct codes resolve across the day's segments at DETECTION thresholds
                   (tau_d, m_d) - U-750 SegmentScore.pick via U-751 arm_a_resolved_codes. (tau_d, m_d) are
                   fitted by 5-fold GROUPED cross-validation (fold = sha256 of contract_labor_id mod 5) over
                   ALL labelled TimeLog crew-days, objective F1 on "truth is split"; every holdout day uses
                   the fit from the folds NOT containing it (out-of-fold).
  * S_haiku  (paid, --llm-run only) one Haiku call per holdout crew-day (K-independent): the day's rebuilt
                   TimeLog notes + total hours -> {"multi_task": bool, "tasks": [...]}. Invalid -> False,
                   status counted. Without --llm-run S_haiku and every rule using it is `not_run`.
  * S_sonnet ($0)  U-751's cached Sonnet answer is a split (cache-only, `u751.rescore_k`).

Pre-registered rules (fixed before scoring; do not tune):
  headline: FLAG if S_text OR S_haiku OR S_sonnet; else PROPOSE Sonnet's single code; else ABSTAIN.
  secondary: each signal alone, S_haiku OR S_sonnet, S_text OR S_sonnet, U-751's headline (reference).

Verdict A (OFFICIAL): coverage_A = proposals / days_scored; PASS iff exact >= 0.80 AND coverage_A >= 0.50
AND proposals >= 20 (U-751 `verdict`). Verdict B (shown, never the verdict): coverage_B = (proposals +
flags) / days_scored, same thresholds.

Outputs (ids / codes / hours / rates / counts only): u775_report_<stamp>.{md,json}, privacy-guarded like
U-750/U-751; Haiku task labels never reach the report (they stay in the cache only). --out-dir must resolve
under /Users/chris/Applications/build.one/.data/.

Usage (repo root; prod read-only - EM only):
  .venv/bin/python -m scripts.u775_multitask_detector                                       # dry-run plan
  .venv/bin/python -m scripts.u775_multitask_detector --run --out-dir ../.data/u775          # S_text + S_sonnet
  .venv/bin/python -m scripts.u775_multitask_detector --run --out-dir ../.data/u775 \\
      --llm-run --budget-usd 2.00 --max-calls 400                                           # + S_haiku
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from entities.coding_suggestion.business.similar_bills import compute_idf_from_doc_token_sets
from scripts import u750_labor_followup_backtest as u750
from scripts import u751_single_code_rescore as u751
from scripts.u673_labor_coding_backtest import (
    _git_sha,
    _pct,
    _rate,
    assert_outputs_privacy_safe,
    tokenize_labor_text,
)

MODEL_VERSION = "u775-multitask-detector-v1"
KS = u751.KS  # (7, 14)
HEADLINE_K = u751.HEADLINE_K  # 7
N_FOLDS = 5
TAU_D_GRID = tuple(Decimal(x) / Decimal(100) for x in range(20, 91, 5))  # 0.20 .. 0.90
M_D_GRID = (1, 2, 3)
DEFAULT_SONNET_CACHE_DIRS = u751.DEFAULT_CACHE_DIRS
DEFAULT_BUDGET_USD = Decimal("2.00")
DEFAULT_MAX_CALLS = 400
FLAG, PROPOSE, ABSTAIN = u751.FLAG, u751.PROPOSE, u751.ABSTAIN
ALL_STRATUM = u751.ALL_STRATUM
NOT_RUN = "not_run"
INCOMPLETE = "incomplete"

# signal names -> the signals each detector ORs together
DETECTORS: dict[str, tuple[str, ...]] = {
    "S_text": ("text",),
    "S_haiku": ("haiku",),
    "S_sonnet": ("sonnet",),
    "S_text_or_S_haiku_or_S_sonnet": ("text", "haiku", "sonnet"),
    "S_haiku_or_S_sonnet": ("haiku", "sonnet"),
    "S_text_or_S_sonnet": ("text", "sonnet"),
}
HEADLINE_RULE = "headline"
RULE_DETECTORS: dict[str, tuple[str, ...]] = {
    HEADLINE_RULE: ("text", "haiku", "sonnet"),
    "R_text": ("text",),
    "R_haiku": ("haiku",),
    "R_sonnet": ("sonnet",),
    "R_haiku_or_sonnet": ("haiku", "sonnet"),
    "R_text_or_sonnet": ("text", "sonnet"),
}
U751_REFERENCE_RULE = "U751_headline"
RULES = (*RULE_DETECTORS, U751_REFERENCE_RULE)


def uses_haiku(rule: str) -> bool:
    return "haiku" in RULE_DETECTORS.get(rule, ())


# ---------------------------------------------------------------------------
# Grouped folds + S_text detection-threshold fit (pure)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=None)
def fold_of(contract_labor_id: int, n_folds: int = N_FOLDS) -> int:
    """Deterministic, grouped: every day of one ContractLabor lands in the same fold."""
    h = hashlib.sha256(f"u775-fold:{int(contract_labor_id)}".encode("utf-8")).hexdigest()
    return int(h, 16) % n_folds


GridKey = tuple[Decimal, int]


def detection_grid(an: u750.DayAnalysis) -> dict[GridKey, bool]:
    """S_text at every (tau_d, m_d): >= 2 distinct codes resolve (U-750 SegmentScore.pick)."""
    return {
        (tau, m): len(u751.arm_a_resolved_codes(an, float(tau), m)) >= 2
        for tau in TAU_D_GRID
        for m in M_D_GRID
    }


@dataclass(frozen=True)
class GridDay:
    day: u750.CrewDay
    grid: Mapping[GridKey, bool]


def _confusion(pairs: Iterable[tuple[bool, bool]]) -> dict[str, int]:
    """pairs of (truth_is_split, predicted)."""
    c = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for truth, pred in pairs:
        c[("tp" if truth else "fp") if pred else ("fn" if truth else "tn")] += 1
    return c


def _f1(c: Mapping[str, int]) -> Fraction:
    den = 2 * c["tp"] + c["fp"] + c["fn"]
    return Fraction(2 * c["tp"], den) if den else Fraction(0)


def _precision(c: Mapping[str, int]) -> Fraction:
    den = c["tp"] + c["fp"]
    return Fraction(c["tp"], den) if den else Fraction(0)


def fit_detection(train: Sequence[GridDay]) -> dict[str, Any]:
    """Max F1 for "truth is split"; tie -> higher precision -> higher tau -> higher m."""
    best: Optional[tuple] = None
    for tau in TAU_D_GRID:
        for m in M_D_GRID:
            c = _confusion((g.day.is_split, g.grid[(tau, m)]) for g in train)
            rank = (_f1(c), _precision(c), tau, m)
            if best is None or rank > best[0]:
                best = (rank, tau, m, c)
    _, tau, m, c = best
    return {
        "tau_d": float(tau),
        "m_d": m,
        "training_days": len(train),
        "training_split_days": sum(1 for g in train if g.day.is_split),
        "training_f1": float(_f1(c)),
        "training_precision": float(_precision(c)),
        "training_recall": _rate(c["tp"], c["tp"] + c["fn"]),
        "training_confusion": c,
    }


def cross_validate(
    training_for_fold: Callable[[int], Sequence[GridDay]],
    *,
    n_folds: int = N_FOLDS,
    fit_fn: Callable[[Sequence[GridDay]], dict[str, Any]] = fit_detection,
) -> dict[int, dict[str, Any]]:
    """fold -> the fit on `training_for_fold(fold)`; a training day inside the fold is a hard error."""
    fits = {}
    for f in range(n_folds):
        train = list(training_for_fold(f))
        if any(fold_of(g.day.contract_labor_id, n_folds) == f for g in train):
            raise AssertionError(f"U-775: fold {f} training set contains one of its own days")
        fits[f] = {"fold": f, **fit_fn(train)}
    return fits


def fold_complement(labelled: Sequence[GridDay], n_folds: int = N_FOLDS) -> Callable[[int], list[GridDay]]:
    """Pure-list training sets (grids already fixed): every labelled day NOT in the fold."""
    return lambda f: [g for g in labelled if fold_of(g.day.contract_labor_id, n_folds) != f]


def s_text_out_of_fold(
    gd: GridDay, fits: Mapping[int, Mapping[str, Any]], n_folds: int = N_FOLDS
) -> tuple[bool, int]:
    """(S_text, fold): the day is scored with the fit of ITS OWN fold, i.e. trained without it."""
    f = fold_of(gd.day.contract_labor_id, n_folds)
    fit = fits[f]
    return gd.grid[(Decimal(str(fit["tau_d"])), fit["m_d"])], f


# ---------------------------------------------------------------------------
# Labelled population for the cross-validation (U-750 as-of evidence per day and K)
# ---------------------------------------------------------------------------


def _setup(corpus: u750.Corpus) -> tuple[dict, dict, dict, dict]:
    """COPIED from U-751 build_population / U-750 run_backtest's setup block (kept there in locals):
    label-free IDF over every line's tokens, token weights, per-project line index."""
    tokens = {ln.line_id: tokenize_labor_text(ln.note_text) for ln in corpus.lines}
    idf = compute_idf_from_doc_token_sets([t for t in tokens.values() if t])
    weights = {lid: sum(idf.get(t, 0.0) for t in toks) for lid, toks in tokens.items()}
    by_project: dict[Optional[int], list] = defaultdict(list)
    for ln in corpus.lines:
        by_project[ln.project_id].append(ln)
    return tokens, weights, idf, by_project


def holdout_grid_days(pop: u751.Population, k: int) -> dict[tuple, GridDay]:
    """The SCORED grids: each holdout day with its normal U-750 as-of evidence (U-751's analyses)."""
    hold_pairs, _ = pop.by_k[k]
    return {a.day.key: GridDay(a.day, detection_grid(a)) for a, _ in hold_pairs}


def fold_training_grid_days(
    setup: tuple[dict, dict, dict, dict],
    days: Sequence[u750.CrewDay],
    k: int,
    fold: int,
    *,
    cal: tuple[date, date],
    n_folds: int = N_FOLDS,
) -> list[GridDay]:
    """TRAINING grids for `fold`: every labelled TimeLog day outside the fold, with U-750's as-of evidence
    at K (calibration days capped at the calibration end, as U-750; other days U-750's default) MINUS every
    line whose ContractLabor is in `fold` - so no held-out label can shape a training feature."""
    tokens, weights, idf, by_project = setup
    out: list[GridDay] = []
    for d in days:
        if fold_of(d.contract_labor_id, n_folds) == fold:
            continue
        kw = {"evidence_until": cal[1]} if cal[0] <= d.line_date <= cal[1] else {}
        ev = [
            p for p in u750.evidence_for_day(d, by_project[d.project_id], k_days=k, **kw)
            if fold_of(p.contract_labor_id, n_folds) != fold
        ]
        out.append(GridDay(d, detection_grid(u750.analyze_day(d, ev, tokens, weights, idf))))
    return out


# ---------------------------------------------------------------------------
# S_haiku (paid; U-750 LLM plumbing)
# ---------------------------------------------------------------------------

HAIKU_SYSTEM = (
    f"[{MODEL_VERSION}] You read the daily time-log notes of one construction crew member. Decide whether "
    "the work described belongs to MORE THAN ONE construction cost category (for example drywall finishing "
    "and door protection are two categories; hanging and taping drywall is one).\n"
    "Return ONLY strict JSON: {\"multi_task\": true|false, \"tasks\": [\"<short task label>\", ...]}"
)


def haiku_model_id() -> str:
    """The Haiku rung of the live U-701b ladder V2 (never hardcoded)."""
    from intelligence.cascade.ladder_config import DEFAULT_BILL_CODING_LADDER_V2

    rungs = [r.model for r in DEFAULT_BILL_CODING_LADDER_V2 if r.provider == "anthropic" and "haiku" in r.model]
    if len(rungs) != 1:
        raise RuntimeError("U-775: expected exactly one Haiku rung in DEFAULT_BILL_CODING_LADDER_V2")
    return rungs[0]


def build_haiku_prompt(day: u750.CrewDay) -> str:
    """K-independent: U-750's rebuilt TimeLog notes (never the stored Description) + total hours."""
    return f"Total hours: {day.total_hours}\nNotes: {day.note_text or '(none)'}"


def haiku_cache_key(model: str, day: u750.CrewDay) -> str:
    return u750.prompt_hash(model, HAIKU_SYSTEM, build_haiku_prompt(day))


def parse_haiku_response(text: Optional[str]) -> tuple[bool, str, tuple[str, ...]]:
    """(multi_task, status, tasks). Invalid JSON / shape -> (False, status, ()) - no flag, counted."""
    from intelligence.cascade.core import _parse_json_object

    if not text:
        return False, "invalid_json", ()
    try:
        obj = _parse_json_object(text)
    except Exception:  # noqa: BLE001 - any parse failure is counted, never a flag
        obj = None
    if not isinstance(obj, dict):
        return False, "invalid_json", ()
    mt, tasks = obj.get("multi_task"), obj.get("tasks")
    if not isinstance(mt, bool) or not isinstance(tasks, list) or not all(isinstance(t, str) for t in tasks):
        return False, "invalid_shape", ()
    return mt, "ok", tuple(tasks)


def _cache_text(cache: u750.LlmCache, key: str) -> Optional[str]:
    """U-751's guard: a valid-JSON non-object envelope makes LlmCache.get raise TypeError, and a non-string
    "text" field is unusable - both are misses (re-asked), never a crash."""
    try:
        text = cache.get(key)
    except TypeError:
        return None
    return text if isinstance(text, str) else None


@dataclass(frozen=True)
class HaikuResult:
    multi_task: bool
    status: str  # ok | invalid_json | invalid_shape | truncated


def run_haiku(
    days: Sequence[u750.CrewDay],
    *,
    transport: Any,
    model: str,
    cache: u750.LlmCache,
    budget_usd: Decimal,
    max_calls: int,
    spend_fn: Callable[[], Decimal] = u750._isolated_spend,
) -> tuple[dict[tuple, HaikuResult], u750.LlmState, Decimal, set[str]]:
    """One Haiku answer per crew-day. Control flow COPIED from U-750 run_arm_b (which hard-wires its own
    prompt and parser): estimate-before-call refusal, per-call estimate abort, --max-calls, transport-error
    and truncation limits; calls go through U-750 call_llm (U-702 pre-call gate + metering) and answers are
    cached by U-750 LlmCache keyed on U-750 prompt_hash. Returns (results by day key, state, estimate,
    task labels seen - for the privacy guard only). Days not run / hit by a transport error are absent."""
    prompts = [(d, build_haiku_prompt(d)) for d in days]
    keyed = [(d, user, u750.prompt_hash(model, HAIKU_SYSTEM, user)) for d, user in prompts]
    uncached_by_key: dict[str, str] = {}
    cached_days = 0
    for _, user, key in keyed:
        if _cache_text(cache, key) is not None:
            cached_days += 1
        elif key not in uncached_by_key:
            uncached_by_key[key] = user
    uncached = list(uncached_by_key.values())[:max_calls]
    estimate = sum((u750.estimate_call_cost_usd(model, HAIKU_SYSTEM, u) for u in uncached), Decimal("0"))
    if spend_fn() + estimate > budget_usd:
        raise u750.LlmBudgetRefused(
            f"U-775: estimated ${estimate:.4f} for {len(uncached)} uncached Haiku calls plus ${spend_fn():.4f} "
            f"already spent exceeds --budget-usd {budget_usd}; refusing to call"
        )
    state = u750.LlmState()
    out: dict[tuple, HaikuResult] = {}
    labels: set[str] = set()
    # U-750 semantics: at most every cached DAY plus max_calls real calls (identical uncached prompts are
    # each re-asked after a failure, so unique keys must not inflate this)
    planned = min(len(keyed), cached_days + max_calls)
    limit = u750.transport_error_limit(planned)

    async def _go() -> None:
        for day, user, key in keyed:
            text = _cache_text(cache, key)
            if text is not None:
                state.cache_hits += 1
            else:
                if state.calls >= max_calls:
                    state.not_run += 1
                    continue
                call_est = u750.estimate_call_cost_usd(model, HAIKU_SYSTEM, user)
                if spend_fn() + call_est > budget_usd:
                    raise u750.LlmBudgetAbort("per_call_estimate_exceeds_budget")
                state.calls += 1
                res = await u750.call_llm(transport, model, HAIKU_SYSTEM, user)
                if res.error is not None or res.text is None:
                    state.transport_errors += 1
                    state.statuses["transport_error"] += 1
                    if state.transport_errors > limit:
                        raise u750.LlmTransportAbort(
                            f"U-775: {state.transport_errors} non-budget transport errors "
                            f"(limit {limit} of {planned} planned)"
                        )
                    continue  # excluded: the day has no Haiku answer
                if res.stop_reason == "max_tokens":
                    state.truncated += 1
                    state.statuses["truncated"] += 1
                    out[day.key] = HaikuResult(False, "truncated")  # never cached, never a flag
                    if state.truncated > limit:
                        raise u750.LlmTransportAbort(
                            f"U-775: {state.truncated} truncated (max_tokens) answers "
                            f"(limit {limit} of {planned} planned)"
                        )
                    continue
                cache.put(key, res.text, res.usage)
                text = res.text
            mt, status, tasks = parse_haiku_response(text)
            labels.update(tasks)
            state.statuses[status] += 1
            out[day.key] = HaikuResult(mt, status)
        attempted = state.calls + state.cache_hits  # U-750 run_arm_b's final checks, on ACTUAL attempts
        if state.truncated > u750.transport_error_limit(attempted):
            raise u750.LlmTransportAbort(
                f"U-775: {state.truncated} truncated (max_tokens) answers exceed "
                f"max(3, 5% of {attempted} attempted)"
            )
        if state.transport_errors > u750.transport_error_limit(attempted):
            raise u750.LlmTransportAbort(
                f"U-775: {state.transport_errors} non-budget transport errors exceed "
                f"max(3, 5% of {attempted} attempted)"
            )

    asyncio.run(_go())
    return out, state, estimate, labels


def _state_json(state: u750.LlmState) -> dict[str, Any]:
    return {
        "calls": state.calls,
        "cache_hits": state.cache_hits,
        "not_run": state.not_run,
        "transport_errors": state.transport_errors,
        "truncated": state.truncated,
        "statuses": dict(sorted(state.statuses.items())),
    }


# ---------------------------------------------------------------------------
# Rules + metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Signals:
    text: bool
    haiku: Optional[bool]  # None = not run / no answer for this day
    sonnet: Optional[u750.Proposal]  # U-751 cached Sonnet proposal (None = abstain / invalid)

    @property
    def sonnet_split(self) -> bool:
        return self.sonnet is not None and self.sonnet.kind == "split"

    def value(self, name: str) -> Optional[bool]:
        return {"text": self.text, "haiku": self.haiku, "sonnet": self.sonnet_split}[name]


def detect(signals: Signals, names: Sequence[str]) -> Optional[bool]:
    """OR of the named signals; None when a needed signal is unavailable (never silently False)."""
    vals = [signals.value(n) for n in names]
    if any(v is None for v in vals):
        return None
    return any(vals)


def decide(rule: str, signals: Signals) -> Optional[u751.Outcome]:
    """FLAG if the rule's detector fires; else PROPOSE Sonnet's single code; else ABSTAIN.
    None when the rule needs an unavailable signal."""
    if rule not in RULE_DETECTORS:
        raise ValueError(f"unknown rule {rule!r}")
    flag = detect(signals, RULE_DETECTORS[rule])
    if flag is None:
        return None
    if flag:
        return u751.Outcome(FLAG)
    if signals.sonnet is not None and signals.sonnet.kind == "single":
        return u751.Outcome(PROPOSE, signals.sonnet)
    return u751.Outcome(ABSTAIN)


@dataclass(frozen=True)
class DayScore:
    day: u750.CrewDay
    hit: bool  # Sonnet cache hit
    sonnet_status: Optional[str]
    signals: Optional[Signals]  # None on a cache miss
    fold: int
    outcomes: Mapping[str, Optional[u751.Outcome]]  # empty on a miss; None = rule not computable


def score_k(
    sonnet_records: Sequence[u751.DayRecord],
    s_text: Mapping[tuple, tuple[bool, int]],
    haiku: Optional[Mapping[tuple, HaikuResult]],
) -> list[DayScore]:
    out = []
    for r in sonnet_records:
        text, fold = s_text[r.day.key]
        if not r.hit:
            out.append(DayScore(r.day, False, None, None, fold, {}))
            continue
        h = haiku.get(r.day.key) if haiku is not None else None
        sig = Signals(text, h.multi_task if h is not None else None, r.sonnet)
        outcomes: dict[str, Optional[u751.Outcome]] = {rule: decide(rule, sig) for rule in RULE_DETECTORS}
        outcomes[U751_REFERENCE_RULE] = r.outcomes[u751.HEADLINE_RULE]
        out.append(DayScore(r.day, True, r.status, sig, fold, outcomes))
    return out


def verdict_b(mt: Mapping[str, Any]) -> str:
    """Same thresholds as U-751 verdict, with coverage_B = (proposals + flags) / days_scored."""
    props = mt["proposals"]
    if props < u750.MIN_SUPPORT:
        return "insufficient_support"
    ok = (
        Fraction(mt["exact_correct_count"], props) >= Fraction(str(u750.BAR_EXACT))
        and Fraction(props + mt["flags"], mt["days_scored"]) >= Fraction(str(u750.BAR_COVERAGE))
    )
    return "PASS" if ok else "FAIL"


def rule_metrics(scores: Sequence[DayScore], rule: str, *, haiku_run: bool) -> dict[str, Any]:
    """U-751 rule_metrics (counts, coverage_A, exact + Wilson, single-on-split, flag precision, split
    recall) plus coverage_B and both verdicts. Days whose Haiku answer is missing are excluded and
    counted; any such day makes the verdicts `incomplete`."""
    if uses_haiku(rule) and not haiku_run:
        return {"status": NOT_RUN}
    missing, recs = [], []
    for s in scores:
        if s.hit and s.outcomes.get(rule) is None:
            missing.append(s)
        else:
            recs.append(u751.DayRecord(s.day, s.hit, s.sonnet_status, None, frozenset(), {rule: s.outcomes[rule]} if s.hit else {}))
    mt = u751.rule_metrics(recs, rule)
    mt["coverage_A"] = mt.pop("coverage")
    mt["coverage_A_ci95"] = mt.pop("coverage_ci95")
    covered = mt["proposals"] + mt["flags"]
    mt["coverage_B"] = _rate(covered, mt["days_scored"])
    mt["coverage_B_ci95"] = u750.wilson(covered, mt["days_scored"])
    mt["haiku_missing_days"] = len(missing)
    if missing:
        va = vb = INCOMPLETE
    else:
        va, vb = u751.verdict(mt), verdict_b(mt)  # A: U-751's exact-Fraction verdict, unchanged
    return {"status": "scored", "metrics": mt, "verdict_A": va, "verdict_B": vb}


def detector_metrics(scores: Sequence[DayScore], name: str, *, haiku_run: bool) -> dict[str, Any]:
    names = DETECTORS[name]
    if "haiku" in names and not haiku_run:
        return {"status": NOT_RUN}
    pairs, missing = [], 0
    for s in scores:
        if not s.hit:
            continue
        v = detect(s.signals, names)
        if v is None:
            missing += 1
            continue
        pairs.append((s.day.is_split, v))
    c = _confusion(pairs)
    n_flag = c["tp"] + c["fp"]
    return {
        "status": "scored",
        "days": len(pairs),
        "haiku_missing_days": missing,
        "flags": n_flag,
        "split_truth_days": c["tp"] + c["fn"],
        "recall": _rate(c["tp"], c["tp"] + c["fn"]),
        "precision": _rate(c["tp"], n_flag),
        "f1": float(_f1(c)) if (2 * c["tp"] + c["fp"] + c["fn"]) else None,
        "flags_on_single_truth": c["fp"],
        "confusion": c,
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

HaikuFn = Callable[[Sequence[u750.CrewDay]], tuple[dict, u750.LlmState, Decimal, set]]


def _outcome_json(o: Optional[u751.Outcome], catalog: Mapping[int, Any], *, hit: bool) -> Any:
    if not hit:
        return {"action": "not_scored_cache_miss"}
    if o is None:
        return {"action": NOT_RUN}
    return u751._outcome_json(o, catalog)


def run_detector(
    corpus: u750.Corpus,
    *,
    sonnet_caches: Mapping[int, u750.LlmCache],
    sonnet_model: str,
    cal: tuple[date, date],
    hold: tuple[date, date],
    haiku_fn: Optional[HaikuFn] = None,
) -> tuple[dict[str, Any], set[str]]:
    """(result, haiku task labels seen - for the privacy guard; never part of the result)."""
    ks = tuple(sorted(sonnet_caches))
    catalog = {c.id: c for c in corpus.catalog}
    pop = u751.build_population(corpus, ks=ks, cal=cal, hold=hold)
    setup = _setup(corpus)

    haiku: Optional[dict[tuple, HaikuResult]] = None
    haiku_block: dict[str, Any] = {"status": NOT_RUN}
    labels: set[str] = set()
    if haiku_fn is not None:
        haiku, state, estimate, labels = haiku_fn(pop.hold_days)  # ONE call per crew-day, shared by every K
        haiku_block = {
            "status": "run",
            "holdout_days": len(pop.hold_days),
            "days_with_answer": len(haiku),
            "estimate_usd_uncached": float(estimate),
            **_state_json(state),
        }

    hp2_ids = {pid for pid, ab in corpus.project_abbr.items() if ab.strip().upper() == u750.NAMED_CASE[0]}
    named: dict[tuple, dict[str, Any]] = {
        d.key: {
            "contract_labor_id": d.contract_labor_id,
            "project_id": d.project_id,
            "line_date": d.line_date.isoformat(),
            "truth_is_split": d.is_split,
            "truth": u750._lines_json(d.truth, catalog),
            "s_haiku": (
                NOT_RUN if haiku is None
                else ({"multi_task": haiku[d.key].multi_task, "status": haiku[d.key].status}
                      if d.key in haiku else "no_answer")
            ),
            "by_k": {},
        }
        for d in pop.hold_days
        if d.project_id in hp2_ids and d.line_date == u750.NAMED_CASE[1]
    }

    by_k: dict[str, Any] = {}
    for k in ks:
        pairs, arm_a_fit = pop.by_k[k]
        hold_grid = holdout_grid_days(pop, k)
        fits = cross_validate(lambda f: fold_training_grid_days(setup, pop.days, k, f, cal=cal))
        s_text = {key: s_text_out_of_fold(g, fits) for key, g in hold_grid.items()}
        sonnet_recs = u751.rescore_k(pairs, arm_a_fit, catalog, model=sonnet_model, cache=sonnet_caches[k])
        scores = score_k(sonnet_recs, s_text, haiku)
        uk = u750.uncoded_key(k)
        strata = {uk: [s for s in scores if s.day.uncoded_at(k)], ALL_STRATUM: scores}
        hits = sum(s.hit for s in scores)
        rules = {
            rule: {sn: rule_metrics(ss, rule, haiku_run=haiku is not None) for sn, ss in strata.items()}
            for rule in RULES
        }
        detectors = {
            name: {sn: detector_metrics(ss, name, haiku_run=haiku is not None) for sn, ss in strata.items()}
            for name in DETECTORS
        }
        head = rules[HEADLINE_RULE][uk]
        by_k[f"k{k}"] = {
            "arm_a_fit_u750": arm_a_fit,
            "s_text_cv": {
                "folds": N_FOLDS,
                "evidence_rule": "training_grids_exclude_every_line_of_the_held_out_fold",
                "labelled_days": len(pop.days),
                "labelled_split_days": sum(1 for d in pop.days if d.is_split),
                "labelled_split_days_outside_holdout": sum(
                    1 for d in pop.days if d.is_split and d.key not in hold_grid
                ),
                "holdout_days_per_fold": dict(sorted(Counter(f for _, f in s_text.values()).items())),
                "fits": [fits[f] for f in sorted(fits)],
            },
            "holdout_days": len(scores),
            "cache_hits": hits,
            "cache_misses": len(scores) - hits,
            "sonnet_statuses": dict(sorted(Counter(s.sonnet_status if s.hit else "cache_miss" for s in scores).items())),
            "rules": rules,
            "detectors": detectors,
            "verdict_A": head.get("verdict_A", NOT_RUN),
            "verdict_B": head.get("verdict_B", NOT_RUN),
        }
        for s in scores:
            if s.day.key in named:
                named[s.day.key]["by_k"][f"k{k}"] = {
                    "cache": "hit" if s.hit else "miss",
                    "uncoded_at_T": s.day.uncoded_at(k),
                    "fold": s.fold,
                    "s_text": s_text[s.day.key][0],
                    "s_text_fit": {"tau_d": fits[s.fold]["tau_d"], "m_d": fits[s.fold]["m_d"]},
                    "sonnet_status": s.sonnet_status,
                    "sonnet_proposal": u750._proposal_json(s.signals.sonnet, catalog) if s.hit else None,
                    "s_sonnet": s.signals.sonnet_split if s.hit else None,
                    "outcomes": {rule: _outcome_json(s.outcomes.get(rule), catalog, hit=s.hit) for rule in RULES},
                }

    result = {
        "sonnet_model": sonnet_model,
        "headline_k": HEADLINE_K,
        "headline_rule": HEADLINE_RULE,
        "windows": {
            "calibration": [cal[0].isoformat(), cal[1].isoformat()],
            "holdout": [hold[0].isoformat(), hold[1].isoformat()],
        },
        "population": {
            "crew_days_timelog": len(pop.days),
            "calibration_days": pop.calibration_days,
            "holdout_days": len(pop.hold_days),
            "holdout_split_days": sum(1 for d in pop.hold_days if d.is_split),
            **{f"holdout_{u750.uncoded_key(k)}_days": sum(1 for d in pop.hold_days if d.uncoded_at(k)) for k in ks},
        },
        "bar": {"exact_correct": u750.BAR_EXACT, "coverage": u750.BAR_COVERAGE, "min_proposals": u750.MIN_SUPPORT},
        "grids": {"tau_d": [float(t) for t in TAU_D_GRID], "m_d": list(M_D_GRID)},
        "s_haiku": haiku_block,
        "by_k": by_k,
        "named_case": {
            "project_abbreviation": u750.NAMED_CASE[0],
            "line_date": u750.NAMED_CASE[1].isoformat(),
            "crew_days": list(named.values()),
        },
    }
    return result, labels


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_markdown(result: Mapping[str, Any], stamp: str, git_sha: Optional[str]) -> str:
    out = [
        f"# U-775 multi-task detector + single-code re-score ({stamp})",
        "",
        f"- model `{MODEL_VERSION}` - git `{git_sha or 'unknown'}` - Sonnet `{result['sonnet_model']}` (cache only)",
        f"- S_haiku: `{json.dumps(result['s_haiku'], sort_keys=True)}`",
        f"- population: `{json.dumps(result['population'])}`",
        "",
        "## Verdict (headline rule, uncoded@T) - A is OFFICIAL, B is shown only",
        "",
    ]
    for kk, v in result["by_k"].items():
        out.append(f"- {kk}: A **{v['verdict_A']}**, B {v['verdict_B']} (cache hits {v['cache_hits']}, "
                   f"misses {v['cache_misses']})")
    out += ["", "## S_text cross-validated fits (per fold)", "", "| K | fold | tau_d | m_d | train days | train F1 |",
            "|---|---|---|---|---|---|"]
    for kk, v in result["by_k"].items():
        for f in v["s_text_cv"]["fits"]:
            out.append(f"| {kk[1:]} | {f['fold']} | {f['tau_d']:.2f} | {f['m_d']} | {f['training_days']} "
                       f"| {_pct(f['training_f1'])} |")
    out += [
        "",
        "## Rules x strata",
        "",
        "| K | rule | stratum | scored | proposals | flags | abstains | coverage A | coverage B | exact "
        "| single on split | flag precision | split recall | verdict A | verdict B |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for kk, v in result["by_k"].items():
        for rule, strata in v["rules"].items():
            for sname, s in strata.items():
                if s["status"] == NOT_RUN:
                    out.append(f"| {kk[1:]} | {rule} | {sname} | " + " | ".join([NOT_RUN] * 12) + " |")
                    continue
                mt = s["metrics"]
                out.append(
                    f"| {kk[1:]} | {rule} | {sname} | {mt['days_scored']} | {mt['proposals']} | {mt['flags']} "
                    f"| {mt['abstains']} | {_pct(mt['coverage_A'])} | {_pct(mt['coverage_B'])} "
                    f"| {_pct(mt['exact_correct'])} | {mt['single_proposals_on_split_truth']} "
                    f"| {_pct(mt['flag_precision'])} | {_pct(mt['split_recall'])} | {s['verdict_A']} | {s['verdict_B']} |"
                )
    out += ["", "## Detectors x strata", "", "| K | detector | stratum | days | flags | recall | precision | F1 "
            "| flags on single-truth |", "|---|---|---|---|---|---|---|---|---|"]
    for kk, v in result["by_k"].items():
        for name, strata in v["detectors"].items():
            for sname, s in strata.items():
                if s["status"] == NOT_RUN:
                    out.append(f"| {kk[1:]} | {name} | {sname} | " + " | ".join([NOT_RUN] * 6) + " |")
                    continue
                out.append(f"| {kk[1:]} | {name} | {sname} | {s['days']} | {s['flags']} | {_pct(s['recall'])} "
                           f"| {_pct(s['precision'])} | {_pct(s['f1'])} | {s['flags_on_single_truth']} |")
    out += ["", "## Full results", "", "```json", json.dumps(result, indent=2, sort_keys=True), "```", ""]
    return "\n".join(out)


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _walk_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk_strings(v)
    elif isinstance(value, str):
        yield value


def assert_no_task_labels(result: Any, labels: Iterable[str]) -> None:
    """Haiku task labels can be single short words, below U-673's 8-char substring floor: reject any
    JSON key or string value equal (case-insensitive) to a label of >= 3 chars."""
    bad = {t.strip().lower() for t in labels if t and len(t.strip()) >= 3}
    if bad and any(s.strip().lower() in bad for s in _walk_strings(result)):
        raise ValueError("privacy: a Haiku task label found in the report")


def write_outputs(
    result: Mapping[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    forbidden_texts: Iterable[Optional[str]],
    task_labels: Iterable[str] = (),
    git_sha: Optional[str] = None,
) -> tuple[Path, Path]:
    """COPIED from U-751 write_outputs (file names hard-coded there): temp names, U-673 guard on BOTH,
    rename only after both pass, remove everything on failure. Plus the Haiku task-label check."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"u775_report_{stamp}.md"
    js = out_dir / f"u775_report_{stamp}.json"
    md_tmp = out_dir / f".u775_partial_{stamp}.md"
    js_tmp = out_dir / f".u775_partial_{stamp}.json"
    labels = list(task_labels)
    forbidden = [*forbidden_texts, *labels]
    try:
        payload = {"model_version": MODEL_VERSION, "git_sha": git_sha, **result}
        assert_no_task_labels(payload, labels)
        js_tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    argv: Optional[list[str]] = None,
    *,
    corpus_loader: Callable[[], u750.Corpus] = u750.load_corpus_from_db,
    sonnet_model_fn: Callable[[], str] = u750.sonnet_model_id,
    haiku_model_fn: Callable[[], str] = haiku_model_id,
    transport_factory: Optional[Callable[[], Any]] = None,
) -> int:
    ap = argparse.ArgumentParser(description="U-775 multi-task detector + single-code re-score (read-only).")
    ap.add_argument("--run", action="store_true", help="Rebuild the population (read-only DB) and score.")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--k", type=u750._parse_ks, default=KS, help="Subset of 7,14 (default both; 7 = headline).")
    ap.add_argument("--sonnet-cache-k7", type=Path, default=DEFAULT_SONNET_CACHE_DIRS[7])
    ap.add_argument("--sonnet-cache-k14", type=Path, default=DEFAULT_SONNET_CACHE_DIRS[14])
    ap.add_argument("--llm-run", action="store_true", help="Add S_haiku (paid Haiku calls, isolated ledger).")
    ap.add_argument("--budget-usd", type=Decimal, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    ap.add_argument("--cal-since", type=date.fromisoformat, default=u750.DEFAULT_CAL_SINCE)
    ap.add_argument("--cal-until", type=date.fromisoformat, default=u750.DEFAULT_CAL_UNTIL)
    ap.add_argument("--holdout-since", type=date.fromisoformat, default=u750.DEFAULT_HOLD_SINCE)
    ap.add_argument("--holdout-until", type=date.fromisoformat, default=u750.DEFAULT_HOLD_UNTIL)
    args = ap.parse_args(argv)
    if not set(args.k) <= set(KS):
        ap.error("--k must be a subset of 7,14 (the cached U-750 arm-B runs)")
    u750.validate_windows(args.cal_since, args.cal_until, args.holdout_since, args.holdout_until)
    if args.cal_until >= args.holdout_since:
        ap.error("calibration must end before the holdout starts")
    if args.llm_run and not args.run:
        ap.error("--llm-run requires --run")
    if args.llm_run:
        if not args.budget_usd.is_finite() or args.budget_usd <= 0:
            ap.error("--budget-usd must be > 0")
        if args.max_calls < 1:
            ap.error("--max-calls must be >= 1")
    cal = (args.cal_since, args.cal_until)
    hold = (args.holdout_since, args.holdout_until)
    cache_dirs = {k: {7: args.sonnet_cache_k7, 14: args.sonnet_cache_k14}[k] for k in args.k}
    sonnet_model = sonnet_model_fn()
    haiku_model = haiku_model_fn()

    if not args.run:
        plan = {
            "mode": "dry_run_no_db_no_llm",
            "ks": list(args.k),
            "headline_k": HEADLINE_K,
            "rules": list(RULES),
            "detectors": list(DETECTORS),
            "folds": N_FOLDS,
            "grids": {"tau_d": [float(t) for t in TAU_D_GRID], "m_d": list(M_D_GRID)},
            "sonnet_model_for_cache_keys": sonnet_model,
            "haiku_model": haiku_model,
            "windows": {"calibration": [d.isoformat() for d in cal], "holdout": [d.isoformat() for d in hold]},
            "sonnet_cache_dirs": {
                f"k{k}": {"path": str(p), "files": u751._cache_file_count(p)} for k, p in cache_dirs.items()
            },
            "haiku_cache_dir": str(args.out_dir / "haiku" / "llm_cache") if args.out_dir else "<out-dir>/haiku/llm_cache",
            "haiku_budget_usd": float(args.budget_usd),
            "haiku_max_calls": args.max_calls,
        }
        print("U-775 dry-run plan: " + json.dumps(plan, sort_keys=True))
        return 0
    if args.out_dir is None:
        ap.error("--run requires --out-dir (use /Users/chris/Applications/build.one/.data/u775)")
    if not u751.out_dir_allowed(args.out_dir):
        ap.error(f"--out-dir must resolve under {u750.DATA_ROOT} (outputs and the Haiku cache are never committed)")
    for k, p in cache_dirs.items():
        if not p.is_dir():
            ap.error(f"Sonnet cache dir for K={k} not found: {p}")
    out_dir = args.out_dir.resolve()

    corpus = corpus_loader()
    sonnet_caches = {k: u750.LlmCache(p) for k, p in cache_dirs.items()}
    haiku_fn: Optional[HaikuFn] = None
    if args.llm_run:
        from intelligence.cascade import claude_budget

        if transport_factory is None:
            from intelligence.transport.anthropic import AnthropicTransport

            transport_factory = AnthropicTransport
        transport = transport_factory()
        haiku_cache = u750.LlmCache(out_dir / "haiku" / "llm_cache")

        def haiku_fn(days):  # noqa: ANN001
            return run_haiku(days, transport=transport, model=haiku_model, cache=haiku_cache,
                             budget_usd=args.budget_usd, max_calls=args.max_calls)

        claude_budget.use_isolated_spend_ledger(args.budget_usd)
    try:
        try:
            result, labels = run_detector(corpus, sonnet_caches=sonnet_caches, sonnet_model=sonnet_model,
                                          cal=cal, hold=hold, haiku_fn=haiku_fn)
        except u750.LlmBudgetRefused as exc:
            print(str(exc))
            return 2
        except (u750.LlmBudgetAbort, u750.LlmTransportAbort) as exc:
            print(f"ABORT: {exc}. Cached Haiku answers are kept under haiku/llm_cache/; re-run to resume.")
            return 1
        if args.llm_run:
            result["s_haiku"].update({
                "model": haiku_model,
                "spend_ledger": "isolated",
                "isolated_spend_total_usd": float(claude_budget.get_isolated_spend_total() or 0),
                "budget_usd": float(args.budget_usd),
            })
    finally:
        if args.llm_run:
            claude_budget.clear_isolated_spend_ledger()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    md, _ = write_outputs(result, out_dir=out_dir, stamp=stamp, forbidden_texts=corpus.free_texts(),
                          task_labels=labels, git_sha=_git_sha())
    print(f"U-775: {len(corpus.lines)} lines; report={md.name}; "
          + ", ".join(f"{kk}=A:{v['verdict_A']}/B:{v['verdict_B']}" for kk, v in result["by_k"].items()))
    return 0


if __name__ == "__main__":
    from scripts.sync_helper import assert_cli_system_admin

    assert_cli_system_admin()
    raise SystemExit(main())
