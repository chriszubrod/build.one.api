"""U-775 multi-task detector: synthetic tests only (no DB, no network, no LLM)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from intelligence.cascade import claude_budget
from scripts import u750_labor_followup_backtest as u750
from scripts import u751_single_code_rescore as u751
from scripts import u775_multitask_detector as m
from tests.test_u750_labor_followup_backtest import CATALOG, CHAN, DRY, PROT, FakeTransport, hp2_lines, ln
from tests.test_u751_single_code_rescore import _seed_cache_via_u750

SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
CAT = {c.id: c for c in CATALOG}
CAL = (u750.DEFAULT_CAL_SINCE, u750.DEFAULT_CAL_UNTIL)
HOLD = (u750.DEFAULT_HOLD_SINCE, u750.DEFAULT_HOLD_UNTIL)
H8 = Decimal("8")
HAIKU_TEXT = '{"multi_task": true, "tasks": ["cornerbeadwork", "doorwrap"]}'


def _corpus() -> u750.Corpus:
    return u750.Corpus(lines=hp2_lines(), catalog=CATALOG, project_abbr={7: "HP2"})


def crew_day(*pairs: tuple[int, str], cl: int = 1, d: date = date(2026, 9, 29)) -> u750.CrewDay:
    (x,) = u750.build_crew_days([ln(d, s, h, cl=cl, note="n") for s, h in pairs])
    return x


def split_day(cl: int, d: date) -> u750.CrewDay:
    return crew_day((DRY, "4"), (PROT, "4"), cl=cl, d=d)


def single_day(cl: int, d: date) -> u750.CrewDay:
    return crew_day((DRY, "8"), cl=cl, d=d)


def grid_upto(thr: str, value: bool = True) -> dict:
    """S_text True for every tau_d <= thr (all m_d), else not value."""
    return {(t, mm): (value if t <= Decimal(thr) else not value) for t in m.TAU_D_GRID for mm in m.M_D_GRID}


def cls_in_folds(n: int) -> list[int]:
    """n contract-labor ids that land in n distinct folds."""
    out, seen = [], set()
    for cl in range(1, 10_000):
        f = m.fold_of(cl)
        if f not in seen:
            seen.add(f)
            out.append(cl)
        if len(out) == n:
            return out
    raise AssertionError


# ---------------------------------------------------------------------------
# Folds + S_text fit
# ---------------------------------------------------------------------------


def test_fold_assignment_deterministic_and_grouped():
    for cl in (1, 1000, 987654321):
        expected = int(hashlib.sha256(f"u775-fold:{cl}".encode()).hexdigest(), 16) % 5
        assert m.fold_of(cl) == expected == m.fold_of(cl)
        assert 0 <= m.fold_of(cl) < m.N_FOLDS
    assert len({m.fold_of(c) for c in range(1, 200)}) == m.N_FOLDS
    # grouped: every day of one CL is in the same fold, whatever its date
    days = [m.GridDay(single_day(42, date(2026, 9, 1) + timedelta(days=i)), grid_upto("0.5")) for i in range(5)]
    assert len({m.fold_of(g.day.contract_labor_id) for g in days}) == 1


def test_cross_validation_never_trains_on_the_scored_days_fold():
    labelled = [m.GridDay(single_day(cl, date(2026, 9, 1) + timedelta(days=i)), grid_upto("0.5"))
                for cl in range(1, 60) for i in range(2)]
    trained_on: dict[int, set[int]] = {}

    def spy(train):
        f = len(trained_on)
        trained_on[f] = {g.day.contract_labor_id for g in train}
        return m.fit_detection(train)

    fits = m.cross_validate(m.fold_complement(labelled), fit_fn=spy)
    for g in labelled:
        _, fold = m.s_text_out_of_fold(g, fits)
        assert g.day.contract_labor_id not in trained_on[fold]
        assert fits[fold]["training_days"] == sum(1 for x in labelled if m.fold_of(x.day.contract_labor_id) != fold)


def test_out_of_fold_differs_from_fit_on_all_days():
    """Goes red if a holdout day's S_text used a fit that saw its own fold."""
    x, y = cls_in_folds(2)
    d0 = date(2026, 9, 1)
    own = [m.GridDay(split_day(x, d0 + timedelta(days=i)), grid_upto("0.20")) for i in range(5)]
    other = [
        m.GridDay(single_day(y, d0), grid_upto("0.20")),           # false positive at tau 0.20
        m.GridDay(split_day(y, d0 + timedelta(days=1)), grid_upto("0.50")),
    ]
    labelled = own + other
    target = own[0]
    fits = m.cross_validate(m.fold_complement(labelled))
    oof, fold = m.s_text_out_of_fold(target, fits)
    assert fold == m.fold_of(x)
    assert (fits[fold]["tau_d"], fits[fold]["m_d"]) == (0.5, 3)
    assert oof is False
    fit_all = m.fit_detection(labelled)
    assert fit_all["tau_d"] == 0.2
    assert target.grid[(Decimal(str(fit_all["tau_d"])), fit_all["m_d"])] is True  # the leak would flip it


def test_f1_objective_and_tie_breaks():
    d0 = date(2026, 9, 1)
    s1, s2 = split_day(1, d0), split_day(2, d0)
    n1, n2 = single_day(3, d0), single_day(4, d0)
    # tau <= 0.50: only s1 flagged (F1 2/3, precision 1); tau > 0.50: all flagged (F1 2/3, precision 1/2)
    low = {(t, mm): t <= Decimal("0.50") for t in m.TAU_D_GRID for mm in m.M_D_GRID}
    all_ = {(t, mm): t > Decimal("0.50") for t in m.TAU_D_GRID for mm in m.M_D_GRID}
    train = [m.GridDay(s1, {k: low[k] or all_[k] for k in low}), m.GridDay(s2, all_),
             m.GridDay(n1, all_), m.GridDay(n2, all_)]
    fit = m.fit_detection(train)
    assert (fit["tau_d"], fit["m_d"]) == (0.5, 3)  # precision beats tau; then highest tau/m in the tie
    assert fit["training_f1"] == pytest.approx(2 / 3) and fit["training_precision"] == 1.0
    # flat grid: highest tau, highest m
    flat = [m.GridDay(s1, grid_upto("0.90")), m.GridDay(n1, grid_upto("0.10"))]
    assert (m.fit_detection(flat)["tau_d"], m.fit_detection(flat)["m_d"]) == (0.9, 3)
    # F1 dominates precision
    better = [m.GridDay(s1, grid_upto("0.30")), m.GridDay(s2, grid_upto("0.30")), m.GridDay(n1, grid_upto("0.30")),
              m.GridDay(n2, grid_upto("0.10"))]
    assert m.fit_detection(better)["tau_d"] == 0.3  # F1 0.8 at <=0.30 vs 0 above


def test_s_text_needs_two_codes_at_detection_thresholds():
    day = crew_day((DRY, "8"))
    segs = (u750.SegmentScore({DRY: 1.0}, {DRY: 3}), u750.SegmentScore({PROT: 0.3, CHAN: 0.7}, {PROT: 1, CHAN: 1}))
    an = u750.DayAnalysis(day, segs, ())
    g = m.detection_grid(an)
    assert g[(Decimal("0.70"), 1)] is True     # DRY + CHAN (share 0.7)
    assert g[(Decimal("0.75"), 1)] is False    # CHAN below tau: only DRY
    assert g[(Decimal("0.20"), 2)] is False    # CHAN support 1 < m 2
    one = u750.DayAnalysis(day, (segs[0], segs[0]), ())
    assert not any(m.detection_grid(one).values())  # the same code twice is not multi-task
    assert m.TAU_D_GRID[0] == Decimal("0.20") and m.TAU_D_GRID[-1] == Decimal("0.90") and len(m.TAU_D_GRID) == 15


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

SINGLE = u750.single(DRY, H8, "arm_b")
SPLIT = u750.Proposal("split", ((DRY, Decimal("4")), (PROT, Decimal("4"))), "arm_b")


def test_headline_rule_branches():
    assert m.decide("headline", m.Signals(True, False, SINGLE)).action == m.FLAG
    assert m.decide("headline", m.Signals(False, True, SINGLE)).action == m.FLAG
    assert m.decide("headline", m.Signals(False, False, SPLIT)).action == m.FLAG
    o = m.decide("headline", m.Signals(False, False, SINGLE))
    assert o.action == m.PROPOSE and o.proposal is SINGLE
    assert m.decide("headline", m.Signals(False, False, None)).action == m.ABSTAIN
    assert m.decide("headline", m.Signals(True, False, None)).action == m.FLAG
    assert m.decide("R_text", m.Signals(False, True, SINGLE)).action == m.PROPOSE
    assert m.decide("R_text_or_sonnet", m.Signals(False, True, SPLIT)).action == m.FLAG
    with pytest.raises(ValueError):
        m.decide("nope", m.Signals(False, False, None))


def test_haiku_unavailable_is_never_false():
    sig = m.Signals(False, None, SINGLE)
    for rule in m.RULE_DETECTORS:
        if m.uses_haiku(rule):
            assert m.decide(rule, sig) is None
        else:
            assert m.decide(rule, sig) is not None
    assert m.detect(sig, ("text", "haiku")) is None


def _score(day, sig, hit=True):
    if not hit:
        return m.DayScore(day, False, None, None, 0, {})
    outs = {r: m.decide(r, sig) for r in m.RULE_DETECTORS}
    outs[m.U751_REFERENCE_RULE] = u751.Outcome(m.ABSTAIN)
    return m.DayScore(day, True, "ok", sig, 0, outs)


def test_not_run_propagates_to_rules_and_detectors():
    scores = [_score(single_day(1, date(2026, 9, 1)), m.Signals(False, None, SINGLE))]
    for rule in m.RULES:
        r = m.rule_metrics(scores, rule, haiku_run=False)
        assert (r == {"status": "not_run"}) == m.uses_haiku(rule)
    for name, sigs in m.DETECTORS.items():
        assert (m.detector_metrics(scores, name, haiku_run=False) == {"status": "not_run"}) == ("haiku" in sigs)
    # run, but one day had no answer -> excluded, counted, verdict incomplete (never computed as False)
    r = m.rule_metrics(scores, "headline", haiku_run=True)
    assert r["verdict_A"] == r["verdict_B"] == "incomplete" and r["metrics"]["haiku_missing_days"] == 1
    assert r["metrics"]["days_scored"] == 0


def test_coverage_a_vs_b_arithmetic():
    d0 = date(2026, 9, 1)
    scores = [
        _score(single_day(1, d0), m.Signals(False, False, SINGLE)),   # propose, exact
        _score(split_day(2, d0), m.Signals(False, False, SINGLE)),    # propose single on split: wrong
        _score(split_day(3, d0), m.Signals(True, False, SINGLE)),     # flag, split
        _score(single_day(4, d0), m.Signals(False, True, None)),      # flag, single truth
        _score(single_day(5, d0), m.Signals(False, False, None)),     # abstain
        _score(single_day(6, d0), None, hit=False),                   # cache miss
    ]
    mt = m.rule_metrics(scores, "headline", haiku_run=True)["metrics"]
    assert (mt["days_scored"], mt["cache_misses"], mt["proposals"], mt["flags"], mt["abstains"]) == (5, 1, 2, 2, 1)
    assert mt["coverage_A"] == 0.4 and mt["coverage_B"] == 0.8
    assert mt["exact_correct_count"] == 1 and mt["flag_precision"] == 0.5 and mt["split_recall"] == 0.5
    assert mt["single_proposals_on_split_truth"] == 1
    det = m.detector_metrics(scores, "S_text_or_S_haiku_or_S_sonnet", haiku_run=True)
    assert (det["days"], det["flags"], det["recall"], det["precision"], det["flags_on_single_truth"]) == (5, 2, 0.5, 0.5, 1)
    assert det["f1"] == 0.5


def _vm(props: int, exact: int, flags: int, scored: int) -> dict:
    return {"proposals": props, "exact_correct_count": exact, "flags": flags, "days_scored": scored}


def test_verdicts_a_and_b_boundaries():
    # A uses proposals / scored; B uses (proposals + flags) / scored
    assert u751.verdict(_vm(20, 16, 0, 40)) == "PASS" and m.verdict_b(_vm(20, 16, 0, 40)) == "PASS"
    assert u751.verdict(_vm(20, 16, 10, 50)) == "FAIL"           # coverage A 0.40
    assert m.verdict_b(_vm(20, 16, 5, 50)) == "PASS"             # coverage B exactly 0.50
    assert m.verdict_b(_vm(20, 16, 5, 49)) == "PASS"             # 25/49
    assert m.verdict_b(_vm(20, 16, 4, 50)) == "FAIL"             # 24/50 < 0.50
    assert m.verdict_b(_vm(20, 15, 30, 50)) == "FAIL"            # exact 0.75
    assert m.verdict_b(_vm(19, 19, 30, 50)) == "insufficient_support"
    r = m.rule_metrics([_score(single_day(1, date(2026, 9, 1)), m.Signals(False, False, SINGLE))], "R_text",
                       haiku_run=False)
    assert r["verdict_A"] == r["verdict_B"] == "insufficient_support"


# ---------------------------------------------------------------------------
# S_haiku
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ('{"multi_task": true, "tasks": ["a", "b"]}', (True, "ok", ("a", "b"))),
    ('```json\n{"multi_task": false, "tasks": []}\n```', (False, "ok", ())),
    ("not json", (False, "invalid_json", ())),
    ("", (False, "invalid_json", ())),
    (None, (False, "invalid_json", ())),
    ("[1, 2]", (False, "invalid_json", ())),
    ('{"multi_task": "yes", "tasks": []}', (False, "invalid_shape", ())),
    ('{"multi_task": 1, "tasks": []}', (False, "invalid_shape", ())),
    ('{"multi_task": true}', (False, "invalid_shape", ())),
    ('{"multi_task": true, "tasks": [1]}', (False, "invalid_shape", ())),
])
def test_parse_haiku_response(text, expected):
    assert m.parse_haiku_response(text) == expected


def test_haiku_model_is_the_ladder_rung(monkeypatch):
    from intelligence.cascade import ladder_config
    from intelligence.cascade.ladder_config import Rung

    assert m.haiku_model_id() == HAIKU
    monkeypatch.setattr(ladder_config, "DEFAULT_BILL_CODING_LADDER_V2",
                        (Rung("anthropic", "claude-sonnet-5-5", accept_threshold=0.0),))
    with pytest.raises(RuntimeError):
        m.haiku_model_id()


def test_haiku_prompt_and_key_k_independent_and_stable():
    day = next(d for d in u750.build_crew_days(hp2_lines()) if d.contract_labor_id == 1000)
    user = m.build_haiku_prompt(day)
    assert day.note_text in user and "Total hours: 8" in user and "2026" not in user
    assert m.MODEL_VERSION in m.HAIKU_SYSTEM
    key = m.haiku_cache_key(HAIKU, day)
    assert key == m.haiku_cache_key(HAIKU, day) == u750.prompt_hash(HAIKU, m.HAIKU_SYSTEM, user)
    assert key != m.haiku_cache_key(SONNET, day)


def _hold_days():
    return [d for d in u750.build_crew_days(hp2_lines()) if d.timelog_sourced and HOLD[0] <= d.line_date <= HOLD[1]]


def test_one_haiku_call_per_crew_day_across_k(tmp_path):
    t = FakeTransport(text=HAIKU_TEXT)
    seen: list = []

    def haiku_fn(days):
        seen.append(list(days))
        return m.run_haiku(days, transport=t, model=HAIKU, cache=u750.LlmCache(tmp_path / "h"),
                           budget_usd=Decimal("2"), max_calls=400)

    caches = {7: u750.LlmCache(tmp_path / "s7"), 14: u750.LlmCache(tmp_path / "s14")}
    res, labels = m.run_detector(_corpus(), sonnet_caches=caches, sonnet_model=SONNET, cal=CAL, hold=HOLD,
                                 haiku_fn=haiku_fn)
    assert len(seen) == 1  # once for the whole run, shared by K=7 and K=14
    n_prompts = len({m.build_haiku_prompt(d) for d in seen[0]})
    assert t.calls == n_prompts and res["s_haiku"]["calls"] == n_prompts
    assert labels == {"cornerbeadwork", "doorwrap"}
    # a re-run is fully cached: zero new calls
    t2 = FakeTransport(text=HAIKU_TEXT)
    out, st, _, _ = m.run_haiku(seen[0], transport=t2, model=HAIKU, cache=u750.LlmCache(tmp_path / "h"),
                                budget_usd=Decimal("2"), max_calls=400)
    assert t2.calls == 0 and st.cache_hits == len(seen[0]) and all(v.multi_task for v in out.values())


def test_haiku_budget_refusal_before_any_call(tmp_path):
    t = FakeTransport(text=HAIKU_TEXT)
    with pytest.raises(u750.LlmBudgetRefused):
        m.run_haiku(_hold_days(), transport=t, model=HAIKU, cache=u750.LlmCache(tmp_path / "h"),
                    budget_usd=Decimal("0.000001"), max_calls=10)
    assert t.calls == 0 and not (tmp_path / "h").exists()


def test_haiku_per_call_abort(tmp_path):
    spent = {"v": Decimal("0")}
    t = FakeTransport(text=HAIKU_TEXT, on_call=lambda: spent.__setitem__("v", Decimal("1.9999")))
    days = [crew_day((DRY, "8"), cl=7000 + i) for i in range(3)]
    days = [u750.build_crew_days([ln(d.line_date, DRY, "8", cl=d.contract_labor_id, note=f"mud {i}")])[0]
            for i, d in enumerate(days)]
    with pytest.raises(u750.LlmBudgetAbort) as ei:
        m.run_haiku(days, transport=t, model=HAIKU, cache=u750.LlmCache(tmp_path / "h"),
                    budget_usd=Decimal("2"), max_calls=10, spend_fn=lambda: spent["v"])
    assert ei.value.token == "per_call_estimate_exceeds_budget" and t.calls == 1
    assert len(list((tmp_path / "h").glob("*.json"))) == 1


def test_haiku_invalid_truncated_and_transport_error_statuses(tmp_path):
    days = [u750.build_crew_days([ln(date(2026, 9, 1), DRY, "8", cl=8000 + i, note=f"mud {i}")])[0] for i in range(4)]
    out, st, _, _ = m.run_haiku(days, transport=FakeTransport(text="nope", errors={2}), model=HAIKU,
                                cache=u750.LlmCache(tmp_path / "a"), budget_usd=Decimal("2"), max_calls=10)
    assert st.statuses["invalid_json"] == 3 and st.transport_errors == 1 and len(out) == 3
    assert all(v.multi_task is False and v.status == "invalid_json" for v in out.values())
    out, st, _, _ = m.run_haiku(days[:1], transport=FakeTransport(text=HAIKU_TEXT, stop_reason="max_tokens"),
                                model=HAIKU, cache=u750.LlmCache(tmp_path / "b"), budget_usd=Decimal("2"), max_calls=10)
    assert list(out.values()) == [m.HaikuResult(False, "truncated")] and not (tmp_path / "b").exists()
    out, st, _, _ = m.run_haiku(days, transport=FakeTransport(text=HAIKU_TEXT), model=HAIKU,
                                cache=u750.LlmCache(tmp_path / "c"), budget_usd=Decimal("2"), max_calls=2)
    assert st.calls == 2 and st.not_run == 2 and len(out) == 2


# ---------------------------------------------------------------------------
# No-LLM guards, CLI, outputs
# ---------------------------------------------------------------------------


def _forbid_llm(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no LLM / transport allowed here")

    import intelligence.transport.anthropic as anth

    monkeypatch.setattr(anth, "AnthropicTransport", boom)
    monkeypatch.setattr(u750, "call_llm", boom)
    monkeypatch.setattr(u750, "run_arm_b", boom)
    monkeypatch.setattr(u750, "estimate_call_cost_usd", boom)
    monkeypatch.setattr(m, "run_haiku", boom)


@pytest.fixture
def no_llm(monkeypatch):
    _forbid_llm(monkeypatch)


def test_sonnet_path_is_cache_only(tmp_path, no_llm):
    caches = {7: u750.LlmCache(tmp_path / "empty")}
    res, labels = m.run_detector(_corpus(), sonnet_caches=caches, sonnet_model=SONNET, cal=CAL, hold=HOLD)
    k7 = res["by_k"]["k7"]
    assert k7["cache_hits"] == 0 and k7["cache_misses"] == k7["holdout_days"] > 0
    assert res["s_haiku"] == {"status": "not_run"} and labels == set()
    assert k7["verdict_A"] == "not_run"
    src = Path(m.__file__).read_text()
    assert "run_arm_b(" not in src and "build_llm_prompt" not in src  # Sonnet answers come only from U-751's cache read


def test_dry_run_touches_no_db_or_llm(capsys, no_llm):
    def boom():
        raise AssertionError("dry-run must not load the corpus")

    assert m.main([], corpus_loader=boom, sonnet_model_fn=lambda: SONNET) == 0
    out = capsys.readouterr().out
    assert "dry-run plan" in out and HAIKU in out and SONNET in out
    with pytest.raises(SystemExit):
        m.main(["--llm-run"], corpus_loader=boom, sonnet_model_fn=lambda: SONNET)


def test_out_dir_outside_data_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(u750, "DATA_ROOT", tmp_path / "data")
    (tmp_path / "data").mkdir()
    for d in (tmp_path / "elsewhere", Path(m.__file__).resolve().parents[1] / "docs" / "design" / "u775"):
        with pytest.raises(SystemExit):
            m.main(["--run", "--out-dir", str(d), "--sonnet-cache-k7", str(tmp_path), "--sonnet-cache-k14",
                    str(tmp_path)], corpus_loader=_corpus, sonnet_model_fn=lambda: SONNET)
    with pytest.raises(SystemExit):
        m.main(["--run"], corpus_loader=_corpus, sonnet_model_fn=lambda: SONNET)


def _seeded(tmp_path, monkeypatch):
    cache_dir = tmp_path / "armB" / "llm_cache"
    corpus = _corpus()
    _seed_cache_via_u750(cache_dir, corpus)  # U-750's own path with a fake transport: single 35.00 everywhere
    monkeypatch.setattr(u750, "DATA_ROOT", tmp_path)
    return corpus, cache_dir


def test_run_without_llm_never_calls(tmp_path, monkeypatch):
    corpus, cache_dir = _seeded(tmp_path, monkeypatch)
    _forbid_llm(monkeypatch)  # after seeding: the seed itself uses U-750's (fake-transport) arm-B path
    out = tmp_path / "u775"
    assert m.main(["--run", "--k", "7", "--out-dir", str(out), "--sonnet-cache-k7", str(cache_dir)],
                  corpus_loader=lambda: corpus, sonnet_model_fn=lambda: SONNET,
                  transport_factory=lambda: (_ for _ in ()).throw(AssertionError("no transport"))) == 0
    data = json.loads(next(out.glob("u775_report_*.json")).read_text())
    k7 = data["by_k"]["k7"]
    assert data["s_haiku"]["status"] == "not_run" and k7["cache_misses"] == 0
    assert k7["rules"]["headline"]["uncoded_at_T_k7"] == {"status": "not_run"}
    assert k7["rules"]["R_text"]["uncoded_at_T_k7"]["status"] == "scored"
    assert len(k7["s_text_cv"]["fits"]) == 5
    assert not (out / "haiku").exists() and not claude_budget.is_isolated_spend_ledger()


def test_llm_run_end_to_end_isolated_and_private(tmp_path, monkeypatch):
    corpus, cache_dir = _seeded(tmp_path, monkeypatch)
    out = tmp_path / "u775"
    seen = {}

    def factory():
        seen["ledger_during"] = None
        return FakeTransport(text=HAIKU_TEXT, on_call=lambda: seen.__setitem__(
            "ledger_during", claude_budget.is_isolated_spend_ledger()))

    rc = m.main(["--run", "--llm-run", "--k", "7", "--out-dir", str(out), "--sonnet-cache-k7", str(cache_dir)],
                corpus_loader=lambda: corpus, sonnet_model_fn=lambda: SONNET, transport_factory=factory)
    assert rc == 0 and seen["ledger_during"] is True and not claude_budget.is_isolated_spend_ledger()
    (js,) = out.glob("u775_report_*.json")
    (md,) = out.glob("u775_report_*.md")
    body = js.read_text() + md.read_text()
    assert "cornerbeadwork" not in body and "doorwrap" not in body
    for x in hp2_lines():
        assert x.note_text not in body
    data = json.loads(js.read_text())
    assert data["s_haiku"]["spend_ledger"] == "isolated" and data["s_haiku"]["model"] == HAIKU
    k7 = data["by_k"]["k7"]
    head = k7["rules"]["headline"]["uncoded_at_T_k7"]
    assert head["status"] == "scored" and head["metrics"]["flags"] == head["metrics"]["days_scored"]  # Haiku says multi
    assert k7["verdict_A"] == head["verdict_A"]
    named = {c["contract_labor_id"]: c for c in data["named_case"]["crew_days"]}
    assert set(named) == {1000, 1001}
    assert named[1000]["s_haiku"] == {"multi_task": True, "status": "ok"}
    assert named[1000]["by_k"]["k7"]["outcomes"]["headline"] == {"action": m.FLAG}
    assert list((out / "haiku" / "llm_cache").glob("*.json"))


def test_isolated_ledger_cleared_on_error(tmp_path, monkeypatch):
    corpus, cache_dir = _seeded(tmp_path, monkeypatch)
    argv = ["--run", "--llm-run", "--k", "7", "--out-dir", str(tmp_path / "o"), "--sonnet-cache-k7", str(cache_dir)]

    def explode(*a, **k):
        assert claude_budget.is_isolated_spend_ledger()
        raise RuntimeError("boom")

    monkeypatch.setattr(m, "run_haiku", explode)
    with pytest.raises(RuntimeError):
        m.main(argv, corpus_loader=lambda: corpus, sonnet_model_fn=lambda: SONNET, transport_factory=FakeTransport)
    assert not claude_budget.is_isolated_spend_ledger()
    monkeypatch.undo()
    monkeypatch.setattr(u750, "DATA_ROOT", tmp_path)
    rc = m.main(argv, corpus_loader=lambda: corpus, sonnet_model_fn=lambda: SONNET,
                transport_factory=lambda: FakeTransport(error_code="claude_unavailable:daily_cap"))
    assert rc == 1 and not claude_budget.is_isolated_spend_ledger()
    assert not list((tmp_path / "o").glob("u775_report_*"))
    rc = m.main(argv + ["--budget-usd", "0.000001"], corpus_loader=lambda: corpus, sonnet_model_fn=lambda: SONNET,
                transport_factory=FakeTransport)
    assert rc == 2 and not claude_budget.is_isolated_spend_ledger()


def test_privacy_guard_rejects_note_and_task_label(tmp_path):
    note = hp2_lines()[0].note_text
    base = {"by_k": {}, "sonnet_model": SONNET, "s_haiku": {"status": "run"}, "population": {}}
    m.write_outputs(base, out_dir=tmp_path / "ok", stamp="S", forbidden_texts=[note], task_labels=["drywall"])
    with pytest.raises(ValueError):
        m.write_outputs({**base, "leak": note}, out_dir=tmp_path / "bad", stamp="S", forbidden_texts=[note])
    assert not list((tmp_path / "bad").iterdir())
    for label in ("drywall", "corner-bead-finishing"):  # a short single word AND a long one
        with pytest.raises(ValueError):
            m.write_outputs({**base, "leak": label}, out_dir=tmp_path / f"bad_{label}", stamp="S",
                            forbidden_texts=[], task_labels=[label.upper()])
        assert not list((tmp_path / f"bad_{label}").iterdir())


# ---------------------------------------------------------------------------
# Fix round 1 (Codex Pass 1)
# ---------------------------------------------------------------------------

A_CL, B_CL, C_CL = 1, 4, 5  # folds 4, 0, 2 (distinct; asserted below)


def _leak_corpus(a_label: int) -> u750.Corpus:
    """Held-out CL A (single-code day) is the only evidence for the 2nd segment of training CL B's split day."""
    lines = [
        ln(date(2026, 9, 21), DRY, "8", cl=C_CL, note="drywall mud tape"),
        ln(date(2026, 9, 22), a_label, "8", cl=A_CL, note="door plastic protection"),
        ln(date(2026, 9, 24), DRY, "4", cl=B_CL, note="drywall mud tape; door plastic protection"),
        ln(date(2026, 9, 24), PROT, "4", cl=B_CL, note="drywall mud tape; door plastic protection"),
    ]
    return u750.Corpus(lines=lines, catalog=CATALOG, project_abbr={7: "HP2"})


def test_fold_fit_never_depends_on_held_out_fold_labels(tmp_path):
    """P1: the fit for fold f must be identical whatever the labels of fold f's lines are, because
    training grids are built from evidence that excludes every line of a fold-f ContractLabor."""
    assert len({m.fold_of(x) for x in (A_CL, B_CL, C_CL)}) == 3
    fa = m.fold_of(A_CL)
    fits = []
    for label in (PROT, DRY):
        res, _ = m.run_detector(_leak_corpus(label), sonnet_caches={7: u750.LlmCache(tmp_path / "s")},
                                sonnet_model=SONNET, cal=CAL, hold=HOLD)
        fits.append(next(f for f in res["by_k"]["k7"]["s_text_cv"]["fits"] if f["fold"] == fa))
    assert fits[0] == fits[1]


def _same_prompt_days(n: int):
    return [u750.build_crew_days([ln(date(2026, 9, 1), DRY, "8", cl=20_000 + i, note="same mud")])[0]
            for i in range(n)]


def test_haiku_transport_error_limit_uses_actual_attempts(tmp_path):
    """P2: 400 identical prompts, max_calls=4, every call fails -> the limit is max(3, 5% of 4) = 3."""
    t = FakeTransport(text=HAIKU_TEXT, errors={1, 2, 3, 4})
    with pytest.raises(u750.LlmTransportAbort):
        m.run_haiku(_same_prompt_days(400), transport=t, model=HAIKU, cache=u750.LlmCache(tmp_path / "h"),
                    budget_usd=Decimal("2"), max_calls=4)
    assert t.calls == 4


def test_haiku_truncation_limit_uses_actual_attempts(tmp_path):
    t = FakeTransport(text=HAIKU_TEXT, stop_reason="max_tokens")
    with pytest.raises(u750.LlmTransportAbort):
        m.run_haiku(_same_prompt_days(400), transport=t, model=HAIKU, cache=u750.LlmCache(tmp_path / "h"),
                    budget_usd=Decimal("2"), max_calls=4)
    assert not (tmp_path / "h").exists()  # truncated answers never cached


@pytest.mark.parametrize("envelope", ["[]", "null", "42", '"text"'])
def test_malformed_haiku_cache_envelope_is_a_miss(tmp_path, envelope):
    """P2: valid JSON that is not an object must be a miss (re-asked), never a crash."""
    (day,) = _same_prompt_days(1)
    cache = u750.LlmCache(tmp_path / "h")
    cache.root.mkdir(parents=True)
    (cache.root / f"{m.haiku_cache_key(HAIKU, day)}.json").write_text(envelope, encoding="utf-8")
    t = FakeTransport(text=HAIKU_TEXT)
    out, st, _, _ = m.run_haiku([day], transport=t, model=HAIKU, cache=cache, budget_usd=Decimal("2"), max_calls=4)
    assert t.calls == 1 and st.cache_hits == 0 and out[day.key] == m.HaikuResult(True, "ok")


@pytest.mark.parametrize("envelope", ["[]", "null", "42", '"text"'])
def test_malformed_sonnet_cache_envelope_is_a_miss(tmp_path, envelope, no_llm):
    corpus = _corpus()
    pop = u751.build_population(corpus, ks=(7,), cal=CAL, hold=HOLD)
    root = tmp_path / "s"
    root.mkdir()
    for a, ev in pop.by_k[7][0]:
        (root / f"{u751.cache_key(a.day, ev, CAT, SONNET)[0]}.json").write_text(envelope, encoding="utf-8")
    res, _ = m.run_detector(corpus, sonnet_caches={7: u750.LlmCache(root)}, sonnet_model=SONNET, cal=CAL, hold=HOLD)
    assert res["by_k"]["k7"]["cache_hits"] == 0 and res["by_k"]["k7"]["cache_misses"] > 0


def test_cross_validate_refuses_a_training_set_holding_its_own_fold():
    # Mutation M3: the guard is the last line of defence if a training builder ever leaks a held-out day.
    day = m.GridDay(single_day(42, date(2026, 9, 1)), grid_upto("0.5"))
    own = m.fold_of(42)
    with pytest.raises(AssertionError, match="own days"):
        m.cross_validate(lambda f: [day] if f == own else [])
