"""
U-751: $0 re-score of the cached U-750 Sonnet (arm B) answers as SINGLE-CODE-ONLY proposals.

Business case: U-750 (docs/design/u750/README.md) FAILED - Sonnet's follow-up proposals were 65 % exact on
the realistic uncoded@T population and its splits were weak. U-751 asks whether a narrower product works:
propose ONE code for all the day's hours, and when the day looks multi-task send "may need splitting -
please give codes and hours" instead of guessing a split.

Method (cache-only, no LLM, no network):
  * Rebuild the U-750 population with U-750's own functions: corpus, TimeLog-sourced crew-days,
    calibration / holdout windows, per-K arm-A (tau, m) fitted on calibration (`fit_thresholds`), holdout
    evidence (`evidence_for_day`, hindsight mode) and `analyze_day`.
  * Per holdout day and K, rebuild the arm-B prompt exactly as U-750 `run_arm_b` does
    (`build_llm_prompt` -> `prompt_hash(model, LLM_SYSTEM, user)`) and read `LlmCache.get(key)` from the
    K's cache dir. No entry = CACHE MISS: counted, never scored. A hit is parsed by U-750
    `parse_llm_response` (invalid -> abstain, status counted).
  * This module never imports a transport, never calls `call_llm` / `run_arm_b`.

Pre-registered headline rule (fixed before scoring; do not tune):
  FLAG may_need_splitting  if Sonnet proposed a split OR arm A (fitted tau, m) resolves >= 2 distinct codes
                           across the day's segments (split precedent or not);
  else PROPOSE             Sonnet's single code for all hours;
  else ABSTAIN             (Sonnet abstained / invalid).
Secondary (reported, not the verdict): R_sonnet_only (flag only on a Sonnet split) and R_dominant (never
flag; a Sonnet split becomes its largest-hours code, tie -> lower SubCostCodeId).

Phase B verdict per K, headline rule, uncoded@T: PASS iff exact >= 0.80 AND coverage >= 0.50 AND
proposals >= 20; proposals < 20 -> insufficient_support; else FAIL.

Outputs (ids / codes / hours / rates / counts only): u751_report_<stamp>.{md,json}, both checked by U-673's
assert_outputs_privacy_safe before being renamed into place; --out-dir must resolve under
/Users/chris/Applications/build.one/.data/.

Usage (repo root; prod read-only - EM only):
  .venv/bin/python -m scripts.u751_single_code_rescore                                   # dry-run plan
  .venv/bin/python -m scripts.u751_single_code_rescore --run --out-dir ../.data/u751
"""
from __future__ import annotations

import argparse
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
from scripts.u673_labor_coding_backtest import (
    _git_sha,
    _pct,
    _rate,
    assert_outputs_privacy_safe,
    tokenize_labor_text,
)

MODEL_VERSION = "u751-single-code-rescore-v1"
KS = (7, 14)
HEADLINE_K = u750.HEADLINE_K  # 7
DEFAULT_CACHE_DIRS = {
    7: u750.DATA_ROOT / "u750" / "armB" / "llm_cache",
    14: u750.DATA_ROOT / "u750" / "armB14" / "llm_cache",
}
FLAG = "may_need_splitting"
PROPOSE = "propose"
ABSTAIN = "abstain"
HEADLINE_RULE = "headline"
RULES = (HEADLINE_RULE, "R_sonnet_only", "R_dominant")
ALL_STRATUM = "all_holdout"


# ---------------------------------------------------------------------------
# Decision rules (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    action: str  # PROPOSE | FLAG | ABSTAIN
    proposal: Optional[u750.Proposal] = None  # set only for PROPOSE (always kind="single")


def arm_a_resolved_codes(an: u750.DayAnalysis, tau: float, m: int) -> frozenset[int]:
    """The distinct codes arm A resolves across the day's segments at (tau, m) - U-750 SegmentScore.pick,
    the same picks propose_arm_a uses, without the split-precedent requirement."""
    return frozenset(p.scc for p in (s.pick(tau, m) for s in an.segments) if p is not None)


def dominant_single(p: u750.Proposal, total_hours: Decimal) -> u750.Proposal:
    scc = min(p.lines, key=lambda x: (-x[1], x[0]))[0]  # largest hours, tie -> lower id
    return u750.single(scc, total_hours, "u751_dominant")


def decide(
    rule: str, sonnet: Optional[u750.Proposal], resolved_codes: frozenset[int], total_hours: Decimal
) -> Outcome:
    is_split = sonnet is not None and sonnet.kind == "split"
    if rule == HEADLINE_RULE:
        if is_split or len(resolved_codes) >= 2:
            return Outcome(FLAG)
    elif rule == "R_sonnet_only":
        if is_split:
            return Outcome(FLAG)
    elif rule == "R_dominant":
        if is_split:
            return Outcome(PROPOSE, dominant_single(sonnet, total_hours))
    else:
        raise ValueError(f"unknown rule {rule!r}")
    if sonnet is not None and sonnet.kind == "single":
        return Outcome(PROPOSE, sonnet)
    return Outcome(ABSTAIN)


# ---------------------------------------------------------------------------
# Population (U-750 functions, U-750 run_backtest's setup)
# ---------------------------------------------------------------------------


@dataclass
class Population:
    hold_days: list[u750.CrewDay]
    days: list[u750.CrewDay]  # all TimeLog-sourced days (counted in the population block)
    by_k: dict[int, tuple[list[tuple[u750.DayAnalysis, list]], dict[str, Any]]]  # k -> (holdout pairs, fit)
    calibration_days: int


def build_population(
    corpus: u750.Corpus, *, ks: Sequence[int], cal: tuple[date, date], hold: tuple[date, date]
) -> Population:
    """Mirrors the setup block of U-750 run_backtest (which keeps it in locals): label-free IDF over every
    line's tokens, TimeLog-sourced query days, calibration evidence capped at cal end, holdout in
    hindsight mode. Parity with run_backtest is pinned by a test."""
    lines = corpus.lines
    tokens = {ln.line_id: tokenize_labor_text(ln.note_text) for ln in lines}
    idf = compute_idf_from_doc_token_sets([t for t in tokens.values() if t])
    weights = {lid: sum(idf.get(t, 0.0) for t in toks) for lid, toks in tokens.items()}
    by_project: dict[Optional[int], list] = defaultdict(list)
    for ln in lines:
        by_project[ln.project_id].append(ln)
    days = [d for d in u750.build_crew_days(lines) if d.timelog_sourced]
    cal_days = [d for d in days if cal[0] <= d.line_date <= cal[1]]
    hold_days = [d for d in days if hold[0] <= d.line_date <= hold[1]]

    def _analyze(ds: Sequence[u750.CrewDay], k: int, **kw: Any) -> list[tuple[u750.DayAnalysis, list]]:
        out = []
        for d in ds:
            ev = u750.evidence_for_day(d, by_project[d.project_id], k_days=k, **kw)
            out.append((u750.analyze_day(d, ev, tokens, weights, idf), ev))
        return out

    by_k = {}
    for k in ks:
        fit = u750.fit_thresholds([a for a, _ in _analyze(cal_days, k, evidence_until=cal[1])])
        by_k[k] = (_analyze(hold_days, k), fit)
    return Population(hold_days, days, by_k, len(cal_days))


def cache_key(day: u750.CrewDay, evidence: Sequence, catalog: Mapping[int, Any], model: str) -> tuple[str, dict]:
    """(key, allowed) exactly as U-750 run_arm_b builds them."""
    user, allowed = u750.build_llm_prompt(day, evidence, catalog)
    return u750.prompt_hash(model, u750.LLM_SYSTEM, user), allowed


# ---------------------------------------------------------------------------
# Re-scoring (cache-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DayRecord:
    day: u750.CrewDay
    hit: bool
    status: Optional[str]  # parse_llm_response status; None on a miss
    sonnet: Optional[u750.Proposal]
    resolved_codes: frozenset[int]
    outcomes: Mapping[str, Outcome]  # empty on a miss


def rescore_k(
    pairs: Sequence[tuple[u750.DayAnalysis, Sequence]],
    fit: Mapping[str, Any],
    catalog: Mapping[int, Any],
    *,
    model: str,
    cache: u750.LlmCache,
) -> list[DayRecord]:
    tau, m = fit["tau"], fit["m"]
    out = []
    for an, ev in pairs:
        day = an.day
        codes = arm_a_resolved_codes(an, tau, m)
        key, allowed = cache_key(day, ev, catalog, model)
        try:
            text = cache.get(key)
        except TypeError:
            # Valid JSON that is not an object (e.g. `[]`, `null`): U-750's reader catches only
            # ValueError/KeyError/OSError. Unreadable entries are misses there; same here.
            text = None
        if not isinstance(text, str):  # None, or a non-string "text" field: a miss
            out.append(DayRecord(day, False, None, None, codes, {}))
            continue
        prop, status = u750.parse_llm_response(text, allowed, day.total_hours)
        outcomes = {r: decide(r, prop, codes, day.total_hours) for r in RULES}
        out.append(DayRecord(day, True, status, prop, codes, outcomes))
    return out


def rule_metrics(records: Sequence[DayRecord], rule: str) -> dict[str, Any]:
    scored = [r for r in records if r.hit]
    outs = [(r.day, r.outcomes[rule]) for r in scored]
    n = len(outs)
    props = [(d, o.proposal) for d, o in outs if o.action == PROPOSE]
    flags = [d for d, o in outs if o.action == FLAG]
    abstains = sum(1 for _, o in outs if o.action == ABSTAIN)
    np_ = len(props)
    exact = sum(1 for d, p in props if u750.exact_correct(d, p))
    single_on_split = sum(1 for d, _ in props if d.is_split)
    split_days = sum(1 for d, _ in outs if d.is_split)
    flagged_split = sum(1 for d in flags if d.is_split)
    return {
        "days": len(records),
        "days_scored": n,
        "cache_misses": len(records) - n,
        "proposals": np_,
        "flags": len(flags),
        "abstains": abstains,
        "coverage": _rate(np_, n),
        "coverage_ci95": u750.wilson(np_, n),
        "exact_correct_count": exact,
        "exact_correct": _rate(exact, np_),
        "exact_correct_ci95": u750.wilson(exact, np_),
        "single_proposals_on_split_truth": single_on_split,
        "single_on_split_truth_rate": _rate(single_on_split, np_),
        "split_truth_days_scored": split_days,
        "flags_on_split_truth": flagged_split,
        "flag_precision": _rate(flagged_split, len(flags)),
        "split_recall": _rate(flagged_split, split_days),
    }


def verdict(mt: Mapping[str, Any]) -> str:
    props = mt["proposals"]
    if props < u750.MIN_SUPPORT:
        return "insufficient_support"
    ok = (
        Fraction(mt["exact_correct_count"], props) >= Fraction(str(u750.BAR_EXACT))
        and Fraction(props, mt["days_scored"]) >= Fraction(str(u750.BAR_COVERAGE))
    )
    return "PASS" if ok else "FAIL"


def _statuses(records: Sequence[DayRecord]) -> dict[str, int]:
    return dict(sorted(Counter(r.status if r.hit else "cache_miss" for r in records).items()))


def _outcome_json(o: Optional[Outcome], catalog: Mapping[int, Any]) -> Any:
    if o is None:
        return {"action": "not_scored_cache_miss"}
    if o.action == PROPOSE:
        return {"action": PROPOSE, "lines": u750._lines_json(o.proposal.lines, catalog)}
    return {"action": o.action}


def run_rescore(
    corpus: u750.Corpus,
    *,
    caches: Mapping[int, u750.LlmCache],
    model: str,
    cal: tuple[date, date],
    hold: tuple[date, date],
) -> dict[str, Any]:
    ks = tuple(sorted(caches))
    catalog = {c.id: c for c in corpus.catalog}
    pop = build_population(corpus, ks=ks, cal=cal, hold=hold)
    hp2_ids = {pid for pid, ab in corpus.project_abbr.items() if ab.strip().upper() == u750.NAMED_CASE[0]}
    named: dict[tuple, dict[str, Any]] = {
        d.key: {
            "contract_labor_id": d.contract_labor_id,
            "project_id": d.project_id,
            "line_date": d.line_date.isoformat(),
            "truth_is_split": d.is_split,
            "truth": u750._lines_json(d.truth, catalog),
            "by_k": {},
        }
        for d in pop.hold_days
        if d.project_id in hp2_ids and d.line_date == u750.NAMED_CASE[1]
    }

    by_k: dict[str, Any] = {}
    for k in ks:
        pairs, fit = pop.by_k[k]
        recs = rescore_k(pairs, fit, catalog, model=model, cache=caches[k])
        uk = u750.uncoded_key(k)
        hits = sum(r.hit for r in recs)
        strata = {uk: [r for r in recs if r.day.uncoded_at(k)], ALL_STRATUM: recs}
        rules: dict[str, Any] = {}
        for rule in RULES:
            rules[rule] = {}
            for sname, rs in strata.items():
                mt = rule_metrics(rs, rule)
                rules[rule][sname] = {"metrics": mt, "verdict": verdict(mt)}
        by_k[f"k{k}"] = {
            "fit": fit,
            "holdout_days": len(recs),
            "cache_hits": hits,
            "cache_misses": len(recs) - hits,
            "response_statuses": _statuses(recs),
            "response_statuses_uncoded_at_T": _statuses(strata[uk]),
            "rules": rules,
            "phase_b_verdict": rules[HEADLINE_RULE][uk]["verdict"],
        }
        for r in recs:
            if r.day.key in named:
                named[r.day.key]["by_k"][f"k{k}"] = {
                    "cache": "hit" if r.hit else "miss",
                    "uncoded_at_T": r.day.uncoded_at(k),
                    "sonnet_status": r.status,
                    "sonnet_proposal": u750._proposal_json(r.sonnet, catalog) if r.hit else None,
                    "arm_a_resolved_codes": [
                        {"scc_id": s, "code": catalog[s].number if s in catalog else None}
                        for s in sorted(r.resolved_codes)
                    ],
                    "outcomes": {rule: _outcome_json(r.outcomes.get(rule), catalog) for rule in RULES},
                }

    return {
        "model": model,
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
        "bar": {
            "exact_correct": u750.BAR_EXACT,
            "coverage": u750.BAR_COVERAGE,
            "min_proposals": u750.MIN_SUPPORT,
        },
        "by_k": by_k,
        "named_case": {
            "project_abbreviation": u750.NAMED_CASE[0],
            "line_date": u750.NAMED_CASE[1].isoformat(),
            "crew_days": list(named.values()),
        },
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render_markdown(result: Mapping[str, Any], stamp: str, git_sha: Optional[str]) -> str:
    out = [
        f"# U-751 single-code re-score of cached U-750 Sonnet answers ({stamp})",
        "",
        f"- model `{MODEL_VERSION}` - git `{git_sha or 'unknown'}` - Sonnet `{result['model']}` (cache only, $0)",
        f"- windows: `{json.dumps(result['windows'])}`",
        f"- population: `{json.dumps(result['population'])}`",
        "",
        "## Phase B verdict (headline rule, uncoded@T)",
        "",
    ]
    for kk, v in result["by_k"].items():
        out.append(f"- {kk}: **{v['phase_b_verdict']}** (cache hits {v['cache_hits']}, misses {v['cache_misses']})")
    out += [
        "",
        "## Rules x strata",
        "",
        "| K | rule | stratum | scored | misses | proposals | flags | abstains | coverage | exact "
        "| single on split-truth | flag precision | split recall | verdict |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for kk, v in result["by_k"].items():
        for rule, strata in v["rules"].items():
            for sname, s in strata.items():
                mt = s["metrics"]
                out.append(
                    f"| {kk[1:]} | {rule} | {sname} | {mt['days_scored']} | {mt['cache_misses']} "
                    f"| {mt['proposals']} | {mt['flags']} | {mt['abstains']} | {_pct(mt['coverage'])} "
                    f"| {_pct(mt['exact_correct'])} | {mt['single_proposals_on_split_truth']} "
                    f"| {_pct(mt['flag_precision'])} | {_pct(mt['split_recall'])} | {s['verdict']} |"
                )
    out += ["", "## Full results", "", "```json", json.dumps(result, indent=2, sort_keys=True), "```", ""]
    return "\n".join(out)


def write_outputs(
    result: Mapping[str, Any],
    *,
    out_dir: Path,
    stamp: str,
    forbidden_texts: Iterable[Optional[str]],
    git_sha: Optional[str] = None,
) -> tuple[Path, Path]:
    """U-750 write_outputs pattern (its file names are hard-coded to u750_*): temp names, guard BOTH,
    rename only after both pass, remove everything on failure."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"u751_report_{stamp}.md"
    js = out_dir / f"u751_report_{stamp}.json"
    md_tmp = out_dir / f".u751_partial_{stamp}.md"
    js_tmp = out_dir / f".u751_partial_{stamp}.json"
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


def out_dir_allowed(out_dir: Path) -> bool:
    data_root = u750.DATA_ROOT.resolve()
    resolved = out_dir.resolve()
    return resolved == data_root or data_root in resolved.parents


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cache_file_count(p: Path) -> Optional[int]:
    return len(list(p.glob("*.json"))) if p.is_dir() else None


def main(
    argv: Optional[list[str]] = None,
    *,
    corpus_loader: Callable[[], u750.Corpus] = u750.load_corpus_from_db,
    model_fn: Callable[[], str] = u750.sonnet_model_id,
) -> int:
    ap = argparse.ArgumentParser(
        description="U-751 cache-only single-code re-score of U-750 arm B (read-only; no LLM calls)."
    )
    ap.add_argument("--run", action="store_true", help="Rebuild the population (read-only DB) and re-score.")
    ap.add_argument("--out-dir", type=Path, default=None)
    ap.add_argument("--k", type=u750._parse_ks, default=KS, help="Subset of 7,14 (default both; 7 = headline).")
    ap.add_argument("--cache-dir-k7", type=Path, default=DEFAULT_CACHE_DIRS[7])
    ap.add_argument("--cache-dir-k14", type=Path, default=DEFAULT_CACHE_DIRS[14])
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
    cal = (args.cal_since, args.cal_until)
    hold = (args.holdout_since, args.holdout_until)
    cache_dirs = {7: args.cache_dir_k7, 14: args.cache_dir_k14}
    cache_dirs = {k: cache_dirs[k] for k in args.k}
    model = model_fn()

    if not args.run:
        plan = {
            "mode": "dry_run_no_db_no_llm",
            "ks": list(args.k),
            "headline_k": HEADLINE_K,
            "rules": list(RULES),
            "model_for_cache_keys": model,
            "windows": {"calibration": [d.isoformat() for d in cal], "holdout": [d.isoformat() for d in hold]},
            "cache_dirs": {f"k{k}": {"path": str(p), "files": _cache_file_count(p)} for k, p in cache_dirs.items()},
        }
        print("U-751 dry-run plan: " + json.dumps(plan, sort_keys=True))
        return 0
    if args.out_dir is None:
        ap.error("--run requires --out-dir (use /Users/chris/Applications/build.one/.data/u751)")
    if not out_dir_allowed(args.out_dir):
        ap.error(f"--out-dir must resolve under {u750.DATA_ROOT} (outputs are never committed)")
    for k, p in cache_dirs.items():
        if not p.is_dir():
            ap.error(f"cache dir for K={k} not found: {p}")

    corpus = corpus_loader()
    caches = {k: u750.LlmCache(p) for k, p in cache_dirs.items()}
    result = run_rescore(corpus, caches=caches, model=model, cal=cal, hold=hold)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    md, _ = write_outputs(
        result, out_dir=args.out_dir.resolve(), stamp=stamp, forbidden_texts=corpus.free_texts(), git_sha=_git_sha()
    )
    print(f"U-751: {len(corpus.lines)} lines; report={md.name}; "
          + ", ".join(f"{kk}={v['phase_b_verdict']}" for kk, v in result["by_k"].items()))
    return 0


if __name__ == "__main__":
    from scripts.sync_helper import assert_cli_system_admin

    assert_cli_system_admin()
    raise SystemExit(main())
