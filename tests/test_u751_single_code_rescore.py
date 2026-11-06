"""U-751 cache-only single-code re-score: synthetic tests only (no DB, no network, no LLM)."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import u750_labor_followup_backtest as u750
from scripts import u751_single_code_rescore as r
from tests.test_u750_labor_followup_backtest import (
    CATALOG,
    CHAN,
    DRY,
    FRAME,
    PROT,
    FakeTransport,
    hp2_lines,
    ln,
)

MODEL = "claude-sonnet-5-5"
CAT = {c.id: c for c in CATALOG}
CAL = (u750.DEFAULT_CAL_SINCE, u750.DEFAULT_CAL_UNTIL)
HOLD = (u750.DEFAULT_HOLD_SINCE, u750.DEFAULT_HOLD_UNTIL)
H8 = Decimal("8")


def _corpus() -> u750.Corpus:
    return u750.Corpus(lines=hp2_lines(), catalog=CATALOG, project_abbr={7: "HP2"})


def single_p(scc: int) -> u750.Proposal:
    return u750.single(scc, H8, "arm_b")


def split_p(*pairs: tuple[int, str]) -> u750.Proposal:
    return u750.Proposal("split", tuple(sorted((s, Decimal(h)) for s, h in pairs)), "arm_b")


def crew_day(*pairs: tuple[int, str], cl: int = 1, coded_at: datetime | None = None) -> u750.CrewDay:
    (d,) = u750.build_crew_days(
        [ln(date(2026, 9, 29), s, h, cl=cl, note="n", coded_at=coded_at) for s, h in pairs]
    )
    return d


# ---------------------------------------------------------------------------
# Decision rules
# ---------------------------------------------------------------------------


def test_headline_flags_on_sonnet_split():
    o = r.decide("headline", split_p((DRY, "4"), (PROT, "4")), frozenset({DRY}), H8)
    assert o.action == r.FLAG and o.proposal is None


def test_headline_flags_on_arm_a_two_codes_even_when_sonnet_single():
    o = r.decide("headline", single_p(DRY), frozenset({DRY, PROT}), H8)
    assert o.action == r.FLAG
    # and when Sonnet abstained too (flag precedes abstain)
    assert r.decide("headline", None, frozenset({DRY, PROT}), H8).action == r.FLAG


def test_headline_proposes_single_and_abstains():
    o = r.decide("headline", single_p(DRY), frozenset({DRY}), H8)
    assert o.action == r.PROPOSE and o.proposal.lines == ((DRY, H8),)
    assert r.decide("headline", single_p(DRY), frozenset(), H8).action == r.PROPOSE
    assert r.decide("headline", None, frozenset({DRY}), H8).action == r.ABSTAIN


def test_sonnet_only_ignores_arm_a():
    assert r.decide("R_sonnet_only", single_p(DRY), frozenset({DRY, PROT}), H8).action == r.PROPOSE
    assert r.decide("R_sonnet_only", split_p((DRY, "4"), (PROT, "4")), frozenset(), H8).action == r.FLAG
    assert r.decide("R_sonnet_only", None, frozenset({DRY, PROT}), H8).action == r.ABSTAIN


def test_dominant_never_flags_and_tie_breaks_lower_id():
    o = r.decide("R_dominant", split_p((DRY, "2"), (PROT, "6")), frozenset({DRY, PROT}), H8)
    assert o.action == r.PROPOSE and o.proposal.kind == "single" and o.proposal.lines == ((PROT, H8),)
    tie = r.decide("R_dominant", split_p((PROT, "4"), (DRY, "4")), frozenset(), H8)
    assert tie.proposal.lines == ((min(DRY, PROT), H8),)
    assert r.decide("R_dominant", single_p(CHAN), frozenset({DRY, PROT}), H8).proposal.lines == ((CHAN, H8),)
    assert r.decide("R_dominant", None, frozenset({DRY, PROT}), H8).action == r.ABSTAIN


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        r.decide("nope", None, frozenset(), H8)


def test_arm_a_resolved_codes_needs_no_split_precedent():
    day = crew_day((DRY, "8"))
    segs = (
        u750.SegmentScore({DRY: 1.0}, {DRY: 2}),
        u750.SegmentScore({PROT: 1.0}, {PROT: 2}),
        u750.SegmentScore({CHAN: 0.5, FRAME: 0.5}, {CHAN: 1, FRAME: 1}),  # share 0.5 < tau 0.6: unresolved
    )
    an = u750.DayAnalysis(day, segs, ())  # no precedents at all
    assert r.arm_a_resolved_codes(an, 0.6, 1) == frozenset({DRY, PROT})
    assert u750.propose_arm_a(an, 0.6, 1).kind == "single"  # U-750 would fall back; U-751 still flags
    assert r.arm_a_resolved_codes(an, 0.6, 3) == frozenset()  # support below m


# ---------------------------------------------------------------------------
# Metrics + verdict
# ---------------------------------------------------------------------------


def rec(day: u750.CrewDay, sonnet, codes=frozenset(), hit=True) -> r.DayRecord:
    if not hit:
        return r.DayRecord(day, False, None, None, codes, {})
    return r.DayRecord(day, True, "ok" if sonnet else "abstain", sonnet, codes,
                       {x: r.decide(x, sonnet, codes, day.total_hours) for x in r.RULES})


def test_metric_denominators_and_split_truth_scoring():
    single_truth = crew_day((DRY, "8"), cl=1)
    split_truth = crew_day((DRY, "4"), (PROT, "4"), cl=2)
    recs = [
        rec(single_truth, single_p(DRY)),                               # propose, exact
        rec(split_truth, single_p(DRY)),                                # propose single on split truth: wrong
        rec(split_truth, split_p((DRY, "4"), (PROT, "4"))),             # flag, split truth
        rec(single_truth, single_p(DRY), frozenset({DRY, PROT})),       # flag (arm A), single truth
        rec(single_truth, None),                                        # abstain
        rec(split_truth, single_p(PROT), hit=False),                    # cache miss: never scored
    ]
    mt = r.rule_metrics(recs, "headline")
    assert mt["days"] == 6 and mt["days_scored"] == 5 and mt["cache_misses"] == 1
    assert (mt["proposals"], mt["flags"], mt["abstains"]) == (2, 2, 1)
    assert mt["coverage"] == 0.4  # flags + abstains are not proposed
    assert mt["exact_correct_count"] == 1 and mt["exact_correct"] == 0.5
    assert mt["single_proposals_on_split_truth"] == 1 and mt["single_on_split_truth_rate"] == 0.5
    assert mt["split_truth_days_scored"] == 2  # the missed split-truth day is not counted
    assert mt["flag_precision"] == 0.5 and mt["split_recall"] == 0.5
    assert not u750.exact_correct(split_truth, single_p(DRY))


def test_dominant_on_split_truth_is_scored_wrong():
    split_truth = crew_day((DRY, "4"), (PROT, "4"), cl=2)
    mt = r.rule_metrics([rec(split_truth, split_p((DRY, "4"), (PROT, "4")))], "R_dominant")
    assert mt["proposals"] == 1 and mt["exact_correct_count"] == 0 and mt["single_proposals_on_split_truth"] == 1


def _vm(props: int, exact: int, scored: int) -> dict:
    return {"proposals": props, "exact_correct_count": exact, "days_scored": scored}


def test_verdict_boundaries():
    assert r.verdict(_vm(20, 16, 40)) == "PASS"           # exact exactly 0.80, coverage exactly 0.50
    assert r.verdict(_vm(20, 15, 40)) == "FAIL"           # exact 0.75
    assert r.verdict(_vm(20, 16, 41)) == "FAIL"           # coverage just under 0.50
    assert r.verdict(_vm(19, 19, 19)) == "insufficient_support"
    assert r.verdict(_vm(20, 20, 20)) == "PASS"


# ---------------------------------------------------------------------------
# Cache-key parity + population parity with U-750
# ---------------------------------------------------------------------------


def _seed_cache_via_u750(cache_dir: Path, corpus: u750.Corpus) -> tuple[dict, list]:
    """Run the real U-750 run_backtest -> run_arm_b path with the fake transport (as U-750's own tests do),
    capturing the jobs it builds; returns (result, jobs)."""
    captured: list = []

    def llm(jobs, k):
        captured.extend(jobs)
        return u750.run_arm_b(jobs, CAT, transport=FakeTransport(), model=MODEL, cache=u750.LlmCache(cache_dir),
                              budget_usd=Decimal("5"), max_calls=100)

    res = u750.run_backtest(corpus, ks=(7,), cal=CAL, hold=HOLD, llm=llm, llm_ks=(7,))
    return res, captured


def test_population_and_cache_key_parity_with_u750(tmp_path):
    cache_dir = tmp_path / "armB" / "llm_cache"
    corpus = _corpus()  # one corpus: line ids come from a global counter
    res, jobs = _seed_cache_via_u750(cache_dir, corpus)
    pop = r.build_population(corpus, ks=(7,), cal=CAL, hold=HOLD)
    pairs, fit = pop.by_k[7]
    # same days, same evidence, same fitted pair as U-750 run_backtest
    assert [(a.day.key, [x.line_id for x in ev]) for a, ev in pairs] == [
        (d.key, [x.line_id for x in ev]) for d, ev in jobs
    ]
    assert fit == res["arms"]["arm_a"]["k7"]["fit"]
    # every key U-751 computes is a file U-750 wrote
    keys = {r.cache_key(a.day, ev, CAT, MODEL)[0] for a, ev in pairs}
    assert keys == {p.stem for p in cache_dir.glob("*.json")}
    assert keys == {u750.prompt_hash(MODEL, u750.LLM_SYSTEM, u750.build_llm_prompt(d, ev, CAT)[0]) for d, ev in jobs}
    recs = r.rescore_k(pairs, fit, CAT, model=MODEL, cache=u750.LlmCache(cache_dir))
    assert recs and all(x.hit for x in recs)


# ---------------------------------------------------------------------------
# Cache-only guarantee
# ---------------------------------------------------------------------------


@pytest.fixture
def no_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("U-751 must never construct a transport or call the LLM")

    import intelligence.transport.anthropic as anth

    monkeypatch.setattr(anth, "AnthropicTransport", boom)
    monkeypatch.setattr(u750, "call_llm", boom)
    monkeypatch.setattr(u750, "run_arm_b", boom)
    monkeypatch.setattr(u750, "estimate_call_cost_usd", boom)


def test_source_never_references_a_transport():
    src = Path(r.__file__).read_text()
    assert "AnthropicTransport" not in src and "call_llm(" not in src and "transport" not in src.split('"""', 2)[2]


def test_cache_miss_excluded_counted_and_never_calls(tmp_path, no_llm):
    pop = r.build_population(_corpus(), ks=(7,), cal=CAL, hold=HOLD)
    pairs, fit = pop.by_k[7]
    cache = u750.LlmCache(tmp_path / "c")
    target = next(a for a, _ in pairs if a.day.contract_labor_id == 1000)
    ev = next(e for a, e in pairs if a is target)
    key, _ = r.cache_key(target.day, ev, CAT, MODEL)
    cache.put(key, json.dumps({"proposal": "single", "lines": [{"sub_cost_code_id": DRY, "hours": 8}]}), {})
    recs = r.rescore_k(pairs, fit, CAT, model=MODEL, cache=cache)  # misses must not trigger any call
    hits = [x for x in recs if x.hit]
    assert len(hits) == 1 and hits[0].day.contract_labor_id == 1000
    misses = [x for x in recs if not x.hit]
    assert misses and all(not x.outcomes and x.status is None for x in misses)
    mt = r.rule_metrics(recs, "headline")
    assert mt["days_scored"] == 1 and mt["cache_misses"] == len(recs) - 1


def test_invalid_cached_answer_is_abstain(tmp_path, no_llm):
    pop = r.build_population(_corpus(), ks=(7,), cal=CAL, hold=HOLD)
    pairs, fit = pop.by_k[7]
    cache = u750.LlmCache(tmp_path / "c")
    a, ev = next((a, e) for a, e in pairs if a.day.contract_labor_id == 1000)
    cache.put(r.cache_key(a.day, ev, CAT, MODEL)[0], "not json {", {})
    (hit,) = [x for x in r.rescore_k(pairs, fit, CAT, model=MODEL, cache=cache) if x.hit]
    assert hit.status == "invalid_json" and hit.sonnet is None
    assert hit.outcomes["R_sonnet_only"].action == r.ABSTAIN


# ---------------------------------------------------------------------------
# CLI, outputs, privacy
# ---------------------------------------------------------------------------


def test_dry_run_touches_no_db(capsys):
    def boom():
        raise AssertionError("dry-run must not load the corpus")

    assert r.main([], corpus_loader=boom, model_fn=lambda: MODEL) == 0
    assert "dry-run plan" in capsys.readouterr().out


def test_out_dir_outside_data_root_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(u750, "DATA_ROOT", tmp_path / "data")
    (tmp_path / "data").mkdir()
    for d in (tmp_path / "elsewhere", Path(r.__file__).resolve().parents[1] / "docs" / "design" / "u751"):
        with pytest.raises(SystemExit):
            r.main(["--run", "--out-dir", str(d), "--cache-dir-k7", str(tmp_path), "--cache-dir-k14", str(tmp_path)],
                   corpus_loader=_corpus, model_fn=lambda: MODEL)
    with pytest.raises(SystemExit):
        r.main(["--run"], corpus_loader=_corpus, model_fn=lambda: MODEL)
    with pytest.raises(SystemExit):  # missing cache dir
        r.main(["--run", "--out-dir", str(tmp_path / "data"), "--cache-dir-k7", str(tmp_path / "nope"),
                "--k", "7"], corpus_loader=_corpus, model_fn=lambda: MODEL)
    with pytest.raises(SystemExit):  # K without a cached run
        r.main(["--k", "3"], corpus_loader=_corpus, model_fn=lambda: MODEL)


def test_run_end_to_end_privacy_safe_with_named_case(tmp_path, monkeypatch):
    cache_dir = tmp_path / "armB" / "llm_cache"
    corpus = _corpus()
    _seed_cache_via_u750(cache_dir, corpus)  # FakeTransport answers single 35.00 for every day

    import intelligence.transport.anthropic as anth

    def boom(*a, **k):
        raise AssertionError("no LLM")

    monkeypatch.setattr(anth, "AnthropicTransport", boom)
    monkeypatch.setattr(u750, "call_llm", boom)
    monkeypatch.setattr(u750, "DATA_ROOT", tmp_path)
    out = tmp_path / "u751"
    assert r.main(["--run", "--k", "7", "--out-dir", str(out), "--cache-dir-k7", str(cache_dir)],
                  corpus_loader=lambda: corpus, model_fn=lambda: MODEL) == 0
    (js,) = out.glob("u751_report_*.json")
    (md,) = out.glob("u751_report_*.md")
    body = js.read_text() + md.read_text()
    for x in hp2_lines():
        assert x.note_text not in body
    for c in CATALOG:
        assert c.name not in body
    data = json.loads(js.read_text())
    k7 = data["by_k"]["k7"]
    assert k7["cache_misses"] == 0 and k7["phase_b_verdict"] == "insufficient_support"
    named = {c["contract_labor_id"]: c for c in data["named_case"]["crew_days"]}
    assert set(named) == {1000, 1001}
    assert [t["code"] for t in named[1000]["truth"]] == ["35.00", "44.00", "51.00"]
    assert named[1000]["by_k"]["k7"]["cache"] == "hit"
    assert set(named[1000]["by_k"]["k7"]["outcomes"]) == set(r.RULES)
    assert not list(out.glob(".u751_partial_*"))


def test_privacy_guard_rejects_note_string(tmp_path):
    note = hp2_lines()[0].note_text
    result = {"by_k": {}, "model": MODEL, "windows": {}, "population": {}, "leak": note}
    with pytest.raises(ValueError):
        r.write_outputs(result, out_dir=tmp_path / "bad", stamp="S", forbidden_texts=[note])
    assert not list((tmp_path / "bad").iterdir())
    # a token-shaped leak is caught by the forbidden-text check alone
    result["leak"] = "protected-doors-with-plastic"
    with pytest.raises(ValueError):
        r.write_outputs(result, out_dir=tmp_path / "bad2", stamp="S", forbidden_texts=["protected-doors-with-plastic"])
    assert not list((tmp_path / "bad2").iterdir())


@pytest.mark.parametrize("envelope", ["[]", "null", "42", '"text"'])
def test_wrong_shape_cache_envelope_is_a_miss_not_a_crash(tmp_path, no_llm, envelope):
    # Pass 1 (Codex): valid JSON that is not an object made LlmCache.get raise TypeError and abort the run.
    pop = r.build_population(_corpus(), ks=(7,), cal=CAL, hold=HOLD)
    pairs, fit = pop.by_k[7]
    cache = u750.LlmCache(tmp_path / "c")
    a, ev = next((a, e) for a, e in pairs if a.day.contract_labor_id == 1000)
    key = r.cache_key(a.day, ev, CAT, MODEL)[0]
    cache.root.mkdir(parents=True, exist_ok=True)
    (cache.root / f"{key}.json").write_text(envelope, encoding="utf-8")
    recs = r.rescore_k(pairs, fit, CAT, model=MODEL, cache=cache)
    assert not any(x.hit for x in recs)
    assert r.rule_metrics(recs, "headline")["cache_misses"] == len(recs)
