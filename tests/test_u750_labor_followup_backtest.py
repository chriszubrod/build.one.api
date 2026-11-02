"""U-750 labor follow-up coding backtest: pure-logic tests on synthetic fixtures (no DB, no network)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from intelligence.cascade import claude_budget
from intelligence.transport.base import Done, TextDelta, TransportError, TurnEnd, Usage
from scripts import u750_labor_followup_backtest as m
from scripts.u673_labor_coding_backtest import LaborLine, SccRef

P = 7
DRY, PROT, CHAN, FRAME = 135, 144, 151, 160
CATALOG = [
    SccRef(DRY, "35.00", "Drywall Labor"),
    SccRef(PROT, "44.00", "Protection Labor"),
    SccRef(CHAN, "51.00", "Shower Glass Labor"),
    SccRef(FRAME, "60.00", "Framing Labor"),
]
HP2_NOTE = (
    "installed L-beads and corner beads and applied drywall mud; protected the second-floor doors "
    "with plastic; installed a U-channel on one side of the master shower"
)

_ids = iter(range(1, 1_000_000))


def ln(day: date, scc: int, hours: str = "8", *, cl: int, note: str | None = None,
       project: int = P, vendor: int = 50, coded_at: datetime | None = None, te: int | None = -1) -> LaborLine:
    return LaborLine(
        line_id=next(_ids), contract_labor_id=cl, line_date=day, project_id=project, vendor_id=vendor,
        label_scc_id=scc, hours=Decimal(hours), source_time_entry_id=te, coded_at=coded_at,
        note_text=note,
    )


def day_of(*lines: LaborLine) -> m.CrewDay:
    (d,) = m.build_crew_days(list(lines))
    return d


# ---------------------------------------------------------------------------
# Evidence visibility
# ---------------------------------------------------------------------------

Q = date(2026, 9, 29)  # Tuesday


def vis(prior: LaborLine, k: int = 7, **kw) -> bool:
    return m.evidence_visible(prior, project_id=P, query_date=Q, k_days=k, **kw)


def test_same_date_crew_excluded_even_if_coded():
    other_crew = ln(Q, DRY, cl=2, coded_at=datetime(2026, 9, 29, 12))
    assert not vis(other_crew)


def test_other_project_excluded():
    assert not vis(ln(Q - timedelta(days=1), DRY, cl=3, project=99))


def test_later_dated_prior_visible_only_if_coded_before_T():
    later = date(2026, 10, 1)
    T = datetime(2026, 10, 6)  # Q + 7
    assert vis(ln(later, DRY, cl=4, coded_at=T - timedelta(seconds=1)))
    assert not vis(ln(later, DRY, cl=5, coded_at=T))
    assert not vis(ln(later, DRY, cl=6, coded_at=datetime(2026, 10, 2)), k=3)  # T = 10/2 00:00


def test_no_coded_at_fallback_lag_two_days():
    # T = Q + 3 = 10/2; a prior dated 9/30 is visible (9/30 + 2 <= 10/2), 10/1 is not
    assert vis(ln(date(2026, 9, 30), DRY, cl=7), k=3)
    assert not vis(ln(date(2026, 10, 1), DRY, cl=8), k=3)


def test_coded_at_overrides_fallback():
    # an earlier prior coded AFTER T is invisible even though its date is old
    assert not vis(ln(date(2026, 9, 22), DRY, cl=9, coded_at=datetime(2026, 10, 10)))


def test_ten_workday_window_symmetric():
    assert vis(ln(date(2026, 9, 15), DRY, cl=10))  # 10 workdays before
    assert not vis(ln(date(2026, 9, 14), DRY, cl=11))  # 11 workdays before
    assert m.workday_distance(Q, date(2026, 10, 13)) == 10
    assert m.workday_distance(date(2026, 10, 13), Q) == 10
    assert vis(ln(date(2026, 10, 13), DRY, cl=12, coded_at=datetime(2026, 10, 13, 18)), k=15)
    assert not vis(ln(date(2026, 10, 14), DRY, cl=13, coded_at=datetime(2026, 10, 14, 18)), k=20)


def test_evidence_until_caps_calibration_evidence():
    p = ln(date(2026, 10, 1), DRY, cl=14, coded_at=datetime(2026, 10, 1, 18))
    assert vis(p)
    assert not vis(p, evidence_until=date(2026, 9, 30))


# ---------------------------------------------------------------------------
# Split rule
# ---------------------------------------------------------------------------


def grp(d: date, cl: int, **hours) -> m.PrecedentGroup:
    codes = {"dry": DRY, "prot": PROT, "chan": CHAN, "frame": FRAME}
    return m.PrecedentGroup(cl, d, m._merge_hours((codes[k], Decimal(v)) for k, v in hours.items()))


def test_split_requires_exact_code_set():
    groups = [grp(date(2026, 9, 24), 1, dry="4", prot="2", chan="1", frame="1")]
    assert m.nearest_precedent_with_codes(groups, frozenset({DRY, PROT, CHAN}), Q) is None
    groups.append(grp(date(2026, 9, 21), 2, dry="6", prot="2", chan="1"))
    assert m.nearest_precedent_with_codes(groups, frozenset({DRY, PROT, CHAN}), Q).contract_labor_id == 2


def test_nearest_precedent_and_tie_breaks():
    D = frozenset({DRY, PROT})
    near = grp(date(2026, 9, 28), 5, dry="4", prot="4")      # 1 workday
    far = grp(date(2026, 9, 22), 1, dry="6", prot="2")       # 5 workdays
    assert m.nearest_precedent_with_codes([far, near], D, Q) is near
    before = grp(date(2026, 9, 28), 9, dry="4", prot="4")    # 1 workday before
    after = grp(date(2026, 9, 30), 3, dry="4", prot="4")     # 1 workday after
    assert m.nearest_precedent_with_codes([after, before], D, Q) is before  # earlier date
    a = grp(date(2026, 9, 28), 8, dry="4", prot="4")
    b = grp(date(2026, 9, 28), 4, dry="4", prot="4")
    assert m.nearest_precedent_with_codes([a, b], D, Q) is b  # lower CL id


def test_scale_hours_ratio_and_quarter_rounding_exact_totals():
    g = grp(date(2026, 9, 24), 1, dry="4", prot="2", chan="2")
    assert m.scale_hours(g, Decimal("8")) == ((DRY, Decimal("4")), (PROT, Decimal("2")), (CHAN, Decimal("2")))
    assert dict(m.scale_hours(g, Decimal("7"))) == {DRY: Decimal("3.5"), PROT: Decimal("1.75"), CHAN: Decimal("1.75")}
    odd = m.scale_hours(g, Decimal("7.1"))
    assert sum(h for _, h in odd) == Decimal("7.1")
    assert dict(odd)[DRY] == Decimal("3.6")  # remainder on the largest share
    eq = grp(date(2026, 9, 24), 1, dry="1", prot="1", chan="1")
    out = dict(m.scale_hours(eq, Decimal("8")))
    assert sum(out.values()) == Decimal("8")
    assert out == {DRY: Decimal("2.5"), PROT: Decimal("2.75"), CHAN: Decimal("2.75")}  # tie -> lowest id


# ---------------------------------------------------------------------------
# Arm A proposal paths
# ---------------------------------------------------------------------------


def seg(scores: dict[int, float], support: dict[int, int] | None = None) -> m.SegmentScore:
    return m.SegmentScore(scores, support or {k: 3 for k in scores})


def analysis(segs, precedents=(), *, hours="8") -> m.DayAnalysis:
    d = day_of(ln(Q, DRY, hours, cl=500, note="x"))
    return m.DayAnalysis(d, tuple(segs), tuple(precedents))


def test_single_when_one_code_resolves():
    p = m.propose_arm_a(analysis([seg({DRY: 0.9, PROT: 0.1}), seg({DRY: 1.0})]), 0.6, 2)
    assert p.kind == "single" and p.lines == ((DRY, Decimal("8")),) and not p.flags


def test_abstain_when_nothing_resolves():
    assert m.propose_arm_a(analysis([seg({DRY: 0.5, PROT: 0.5})]), 0.6, 1) is None
    assert m.propose_arm_a(analysis([seg({DRY: 1.0}, {DRY: 1})]), 0.6, 2) is None  # support < m
    assert m.propose_arm_a(analysis([]), 0.4, 1) is None


def test_unresolved_segment_flag():
    p = m.propose_arm_a(analysis([seg({DRY: 1.0}), seg({DRY: 0.5, PROT: 0.5})]), 0.6, 1)
    assert p.kind == "single" and "unresolved_segments" in p.flags


def test_split_only_with_matching_precedent_else_fallback():
    segs = [seg({DRY: 0.9, PROT: 0.1}), seg({PROT: 0.7, DRY: 0.3})]
    p = m.propose_arm_a(analysis(segs, [grp(date(2026, 9, 24), 1, dry="6", prot="2")]), 0.6, 1)
    assert p.kind == "split" and dict(p.lines) == {DRY: Decimal("6"), PROT: Decimal("2")}
    fb = m.propose_arm_a(analysis(segs, [grp(date(2026, 9, 24), 1, dry="6", chan="2")]), 0.6, 1)
    assert fb.kind == "single" and fb.lines == ((DRY, Decimal("8")),) and "split_fallback" in fb.flags


# ---------------------------------------------------------------------------
# Metrics + verdict
# ---------------------------------------------------------------------------


def test_metric_definitions():
    single_day = day_of(ln(Q, DRY, "8", cl=1))
    split_day = day_of(ln(Q, DRY, "6", cl=2), ln(Q, PROT, "2", cl=2))
    split_day2 = day_of(ln(Q, DRY, "4", cl=3), ln(Q, PROT, "4", cl=3))
    single_day2 = day_of(ln(Q, PROT, "8", cl=4))
    assert split_day.is_split and not single_day.is_split
    rows = [
        (single_day, m.single(DRY, Decimal("8"), "t")),                                     # exact
        (split_day, m.Proposal("split", ((DRY, Decimal("6")), (PROT, Decimal("2"))), "t")),  # exact split
        (split_day2, m.Proposal("split", ((DRY, Decimal("6")), (PROT, Decimal("2"))), "t")), # set-only
        (single_day2, m.Proposal("split", ((DRY, Decimal("4")), (PROT, Decimal("4"))), "t")),  # false split
        (day_of(ln(Q, CHAN, "8", cl=5)), None),                                             # abstain
        (day_of(ln(Q, DRY, "4", cl=6), ln(Q, CHAN, "4", cl=6)), m.single(DRY, Decimal("8"), "t")),  # missed
    ]
    a = m.compute_metrics(rows)["all"]
    assert a["days"] == 6 and a["proposals"] == 5 and a["coverage"] == round(5 / 6, 6)
    assert a["exact_correct"] == 0.4  # 2 / 5
    assert a["set_correct"] == 0.6  # 3 / 5
    assert a["single_truth_days"] == 3 and a["false_splits"] == 1 and a["false_split_rate"] == round(1 / 3, 6)
    assert a["split_truth_days"] == 3 and a["missed_splits"] == 1 and a["missed_split_rate"] == round(1 / 3, 6)
    assert a["split_proposals"] == 3 and a["split_precision"] == round(2 / 3, 6)
    # hours matched: 8 + 8 + (4+2) + 4 + 4 = 30 of 40 proposed hours
    assert a["hours_weighted_accuracy"] == 0.75
    assert m.exact_correct(split_day, m.Proposal("split", ((DRY, Decimal("6.01")), (PROT, Decimal("1.99"))), "t"))
    assert not m.exact_correct(split_day, m.Proposal("split", ((DRY, Decimal("6.02")), (PROT, Decimal("1.98"))), "t"))


def _m_all(proposals, exact, coverage, fsr):
    return {"proposals": proposals, "exact_correct": exact, "coverage": coverage, "false_split_rate": fsr}


def test_verdict():
    assert m.verdict(_m_all(19, 1.0, 1.0, 0.0)) == "insufficient_support"
    assert m.verdict(_m_all(20, 0.80, 0.50, 0.05)) == "PASS"
    assert m.verdict(_m_all(20, 0.79, 0.9, 0.0)) == "FAIL"
    assert m.verdict(_m_all(20, 0.9, 0.49, 0.0)) == "FAIL"
    assert m.verdict(_m_all(20, 0.9, 0.9, 0.06)) == "FAIL"


def test_wilson_bounds():
    lo, hi = m.wilson(8, 10)
    assert 0.4 < lo < 0.8 < hi < 1.0
    assert m.wilson(0, 0) is None


# ---------------------------------------------------------------------------
# HP2 worked example end-to-end
# ---------------------------------------------------------------------------


def hp2_lines() -> list[LaborLine]:
    split_note = "corner beads and drywall mud; protected doors with plastic; u-channel at master shower"
    return [
        # 9/24: the precedent split day 4:2:2
        ln(date(2026, 9, 24), DRY, "4", cl=901, note=split_note),
        ln(date(2026, 9, 24), PROT, "2", cl=901, note=split_note),
        ln(date(2026, 9, 24), CHAN, "2", cl=901, note=split_note),
        ln(date(2026, 9, 25), DRY, "8", cl=902, note="drywall mud and corner beads upstairs"),
        ln(date(2026, 9, 28), PROT, "8", cl=903, note="protected doors and floors with plastic"),
        ln(date(2026, 9, 30), CHAN, "8", cl=904, note="u-channel for shower glass master shower"),
        # far-away framing noise (outside the 10-workday window)
        ln(date(2026, 8, 3), FRAME, "8", cl=905, note="framing walls drywall mud doors plastic shower"),
        # the uncoded query day (truth 4:2:2)
        ln(Q, DRY, "4", cl=1000, note=HP2_NOTE),
        ln(Q, PROT, "2", cl=1000, note=HP2_NOTE),
        ln(Q, CHAN, "2", cl=1000, note=HP2_NOTE),
        # another crew member the same day: must never be evidence
        ln(Q, FRAME, "8", cl=1001, note="installed u-channel drywall mud plastic doors shower"),
    ]


def _idf_bits(lines):
    from entities.coding_suggestion.business.similar_bills import compute_idf_from_doc_token_sets

    tokens = {x.line_id: m.tokenize_labor_text(x.note_text) for x in lines}
    idf = compute_idf_from_doc_token_sets([t for t in tokens.values() if t])
    weights = {k: sum(idf.get(t, 0.0) for t in v) for k, v in tokens.items()}
    return tokens, weights, idf


def test_hp2_worked_example_end_to_end():
    lines = hp2_lines()
    tokens, weights, idf = _idf_bits(lines)
    query = next(d for d in m.build_crew_days(lines) if d.contract_labor_id == 1000)
    ev = m.evidence_for_day(query, lines, k_days=7)
    assert {x.contract_labor_id for x in ev} == {901, 902, 903, 904}
    an = m.analyze_day(query, ev, tokens, weights, idf)
    p = m.propose_arm_a(an, 0.4, 1)
    assert p is not None and p.kind == "split"
    assert p.lines == ((DRY, Decimal("4")), (PROT, Decimal("2")), (CHAN, Decimal("2")))
    assert m.exact_correct(query, p)


# ---------------------------------------------------------------------------
# Threshold fitting uses calibration only
# ---------------------------------------------------------------------------


def _fit_corpus(holdout_label: int) -> m.Corpus:
    lines = []
    cl = 2000
    d = date(2026, 7, 1)
    while d <= date(2026, 8, 15):
        if d.weekday() < 5:
            cl += 1
            scc, note = (DRY, "drywall mud tape") if d.day % 2 else (PROT, "plastic door protection")
            lines.append(ln(d, scc, "8", cl=cl, note=note))
        d += timedelta(days=1)
    # holdout rows right after the boundary: visible by T for an 8/14 query under K=7 hindsight
    lines.append(ln(date(2026, 8, 18), holdout_label, "8", cl=9001, note="drywall mud tape",
                    coded_at=datetime(2026, 8, 18, 17)))
    lines.append(ln(date(2026, 8, 19), holdout_label, "8", cl=9002, note="drywall mud tape",
                    coded_at=datetime(2026, 8, 19, 17)))
    return m.Corpus(lines=lines, catalog=CATALOG, project_abbr={P: "HP2"})


def test_fit_ignores_holdout_rows():
    cal = (date(2026, 6, 1), date(2026, 8, 15))
    hold = (date(2026, 8, 16), date(2026, 10, 15))
    a = m.run_backtest(_fit_corpus(DRY), ks=(7,), cal=cal, hold=hold)
    b = m.run_backtest(_fit_corpus(FRAME), ks=(7,), cal=cal, hold=hold)
    assert a["arms"]["arm_a"]["k7"]["fit"] == b["arms"]["arm_a"]["k7"]["fit"]
    assert a["arms"]["arm_a"]["k7"]["calibration"] == b["arms"]["arm_a"]["k7"]["calibration"]
    assert a["arms"]["arm_a"]["k7"]["fit"]["calibration_days"] > 20


def test_fit_reports_when_nothing_qualifies():
    fit = m.fit_thresholds([])
    assert fit["status"] == "no_pair_qualified_best_exact_used"


# ---------------------------------------------------------------------------
# Arm B: parsing, budget, cache
# ---------------------------------------------------------------------------

ALLOWED = {DRY: "35.00", PROT: "44.00"}


def test_parse_llm_valid_split_and_single():
    p, s = m.parse_llm_response(
        '{"proposal":"split","lines":[{"sub_cost_code_number":"35.00","hours":6},'
        '{"sub_cost_code_number":"44.00","hours":2}],"evidence_dates":["2026-09-24"]}',
        ALLOWED, Decimal("8"))
    assert s == "ok" and p.kind == "split" and dict(p.lines) == {DRY: Decimal("6"), PROT: Decimal("2")}
    p, s = m.parse_llm_response(
        '```json\n{"proposal":"single","lines":[{"sub_cost_code_number":"44.00","hours":7}]}\n```',
        ALLOWED, Decimal("8"))
    assert s == "ok" and p.lines == ((PROT, Decimal("8")),)


@pytest.mark.parametrize("text,status", [
    ("not json at all", "invalid_json"),
    ("", "invalid_json"),
    ('{"proposal":"abstain","lines":[]}', "abstain"),
    ('{"proposal":"single","lines":[{"sub_cost_code_number":"99.00","hours":8}]}', "unknown_code"),
    ('{"proposal":"maybe","lines":[]}', "invalid_shape"),
    ('{"proposal":"split","lines":[{"sub_cost_code_number":"35.00","hours":8}]}', "invalid_shape"),
    ('{"proposal":"single","lines":[{"sub_cost_code_number":"35.00","hours":-1}]}', "invalid_shape"),
    ('{"proposal":"single","lines":[{"sub_cost_code_number":"35.00","hours":true}]}', "invalid_shape"),
])
def test_parse_llm_defensive(text, status):
    p, s = m.parse_llm_response(text, ALLOWED, Decimal("8"))
    assert p is None and s == status


class FakeTransport:
    def __init__(self, text: str = '{"proposal":"single","lines":[{"sub_cost_code_number":"35.00","hours":8}]}',
                 error_code: str | None = None, stop_reason: str = "end_turn", errors: set[int] | None = None,
                 on_call=None) -> None:
        self.calls = 0
        self.text = text
        self.error_code = error_code
        self.stop_reason = stop_reason
        self.errors = errors or set()  # 1-based call numbers that fail with a non-budget error
        self.on_call = on_call
        self.kwargs: list[dict] = []

    async def stream(self, messages, model, system=None, max_tokens=4096, tools=None, extra_body=None):
        self.calls += 1
        self.kwargs.append({"max_tokens": max_tokens, "extra_body": extra_body, "model": model})
        if self.on_call:
            self.on_call()
        if self.error_code:
            yield TransportError(message="x", code=self.error_code)
            return
        if self.calls in self.errors:
            yield TransportError(message="boom", code="http_500")
            return
        yield TextDelta(text=self.text)
        yield TurnEnd(stop_reason=self.stop_reason)
        yield Done(usage=Usage(input_tokens=100, output_tokens=20))


def _jobs():
    lines = hp2_lines()
    query = next(d for d in m.build_crew_days(lines) if d.contract_labor_id == 1000)
    return [(query, m.evidence_for_day(query, lines, k_days=7))]


def test_prompt_contains_only_evidence_codes():
    (day, ev), = _jobs()
    user, allowed = m.build_llm_prompt(day, ev, {c.id: c for c in CATALOG})
    assert allowed == {DRY: "35.00", PROT: "44.00", CHAN: "51.00"}
    assert f"id {DRY} | number 35.00" in user
    assert "60.00" not in user and "2026-09-24" in user and "Total hours: 8" in user


def test_budget_refusal_when_estimate_exceeds_budget(tmp_path):
    t = FakeTransport()
    with pytest.raises(m.LlmBudgetRefused):
        m.run_arm_b(_jobs(), {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5",
                    cache=m.LlmCache(tmp_path / "llm_cache"), budget_usd=Decimal("0.000001"), max_calls=10)
    assert t.calls == 0


def test_cache_hit_avoids_a_call(tmp_path):
    cat = {c.id: c for c in CATALOG}
    cache = m.LlmCache(tmp_path / "llm_cache")
    t1 = FakeTransport()
    props, st, _ = m.run_arm_b(_jobs(), cat, transport=t1, model="claude-sonnet-5-5", cache=cache,
                               budget_usd=Decimal("5"), max_calls=10)
    assert t1.calls == 1 and st.calls == 1 and st.statuses["ok"] == 1
    t2 = FakeTransport()
    props2, st2, est2 = m.run_arm_b(_jobs(), cat, transport=t2, model="claude-sonnet-5-5", cache=cache,
                                    budget_usd=Decimal("0.000001"), max_calls=10)  # cached: no estimate needed
    assert t2.calls == 0 and st2.cache_hits == 1 and est2 == 0
    assert props2 == props


def test_max_calls_limits_new_calls(tmp_path):
    jobs = _jobs()
    extra = m.build_crew_days(hp2_lines())
    other = next(d for d in extra if d.contract_labor_id == 1001)
    jobs.append((other, jobs[0][1]))
    t = FakeTransport()
    props, st, _ = m.run_arm_b(jobs, {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5",
                               cache=m.LlmCache(tmp_path / "c"), budget_usd=Decimal("5"), max_calls=1)
    assert t.calls == 1 and st.not_run == 1 and len(props) == 1


def test_budget_skip_aborts_loudly(tmp_path):
    t = FakeTransport(error_code="claude_unavailable:daily_cap")
    with pytest.raises(m.LlmBudgetAbort) as ei:
        m.run_arm_b(_jobs(), {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5",
                    cache=m.LlmCache(tmp_path / "c"), budget_usd=Decimal("5"), max_calls=10)
    assert ei.value.token == "daily_cap"
    assert not (tmp_path / "c").exists() or not list((tmp_path / "c").iterdir())


def test_sonnet_model_is_the_ladder_rung():
    from intelligence.cascade.ladder_config import DEFAULT_BILL_CODING_LADDER_V2

    assert m.sonnet_model_id() in {r.model for r in DEFAULT_BILL_CODING_LADDER_V2}


# ---------------------------------------------------------------------------
# CLI + outputs + privacy guard
# ---------------------------------------------------------------------------


def _corpus() -> m.Corpus:
    return m.Corpus(lines=hp2_lines(), catalog=CATALOG, project_abbr={P: "HP2", 8: "XYZ"})


def test_dry_run_is_counts_only(capsys):
    called = {}

    def counts(cal, hold):
        called["yes"] = (cal, hold)
        return {"holdout": {"days": 3}}

    def boom():
        raise AssertionError("dry-run must not load the corpus")

    assert m.main([], counts_loader=counts, corpus_loader=boom) == 0
    assert called and "dry-run population" in capsys.readouterr().out


def test_day_population_counts():
    rows = [(date(2026, 7, 1), 2, True), (date(2026, 7, 2), 1, False), (date(2026, 9, 1), 1, True)]
    out = m.day_population_counts(rows, (date(2026, 6, 1), date(2026, 8, 15)), (date(2026, 8, 16), date(2026, 10, 15)))
    assert out["calibration"] == {"days": 2, "split_days": 1, "timelog_days": 1, "timelog_split_days": 1}
    assert out["holdout"]["days"] == 1


def test_llm_run_requires_run_and_out_dir_under_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DATA_ROOT", tmp_path / "data")
    with pytest.raises(SystemExit):
        m.main(["--llm-run"], counts_loader=lambda *a: {}, corpus_loader=_corpus)
    repo_dir = Path(m.__file__).resolve().parents[1] / "docs" / "design" / "u750"
    for argv in (["--run", "--llm-run"], ["--run"]):
        with pytest.raises(SystemExit):
            m.main(argv + ["--out-dir", str(repo_dir)], corpus_loader=_corpus, transport_factory=FakeTransport)
        with pytest.raises(SystemExit):
            m.main(argv + ["--out-dir", str(tmp_path / "elsewhere")], corpus_loader=_corpus,
                   transport_factory=FakeTransport)


def test_run_writes_privacy_safe_outputs_and_named_case(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DATA_ROOT", tmp_path)
    assert m.main(["--run", "--out-dir", str(tmp_path), "--k", "7"], corpus_loader=_corpus) == 0
    (js,) = tmp_path.glob("u750_report_*.json")
    (md,) = tmp_path.glob("u750_report_*.md")
    body = js.read_text() + md.read_text()
    for x in hp2_lines():
        assert x.note_text not in body
    data = json.loads(js.read_text())
    assert data["arms"]["arm_b"]["status"] == "skipped_no_llm_run"
    named = {c["contract_labor_id"]: c for c in data["named_case"]["crew_days"]}
    assert set(named) == {1000, 1001}
    assert [t["code"] for t in named[1000]["truth"]] == ["35.00", "44.00", "51.00"]
    assert "arm_a_k7" in named[1000]["proposals"]
    assert not list(tmp_path.glob(".u750_partial_*"))


def test_llm_run_end_to_end_isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DATA_ROOT", tmp_path)
    # holdout window covers 9/29; fake transport -> no network; ledger cleared after
    out = tmp_path / "data"
    rc = m.main(["--run", "--llm-run", "--budget-usd", "5", "--out-dir", str(out), "--k", "7"],
                corpus_loader=_corpus, transport_factory=FakeTransport)
    assert rc == 0
    data = json.loads(next(out.glob("u750_report_*.json")).read_text())
    arm_b = data["arms"]["arm_b"]
    assert arm_b["spend_ledger"] == "isolated" and arm_b["model"] == "claude-sonnet-5-5"
    assert arm_b["k7"]["calls"] >= 1
    assert list((out / "llm_cache").glob("*.json"))
    assert not claude_budget.is_isolated_spend_ledger()


def test_llm_run_budget_skip_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "DATA_ROOT", tmp_path)
    rc = m.main(["--run", "--llm-run", "--out-dir", str(tmp_path), "--k", "7"], corpus_loader=_corpus,
                transport_factory=lambda: FakeTransport(error_code="claude_unavailable:daily_cap"))
    assert rc == 1
    assert not list(tmp_path.glob("u750_report_*"))
    assert not claude_budget.is_isolated_spend_ledger()


def test_privacy_guard_blocks_note_text(tmp_path):
    result = m.run_backtest(_corpus(), ks=(7,), cal=(m.DEFAULT_CAL_SINCE, m.DEFAULT_CAL_UNTIL),
                            hold=(m.DEFAULT_HOLD_SINCE, m.DEFAULT_HOLD_UNTIL))
    m.write_outputs(result, out_dir=tmp_path / "ok", stamp="S", forbidden_texts=_corpus().free_texts())
    result["leak"] = "protected-doors-with-plastic"  # token-shaped, so only the forbidden-text check catches it
    with pytest.raises(ValueError):
        m.write_outputs(result, out_dir=tmp_path / "bad", stamp="S",
                        forbidden_texts=["protected-doors-with-plastic"])
    assert not list((tmp_path / "bad").iterdir())


# ---------------------------------------------------------------------------
# Fix round 1
# ---------------------------------------------------------------------------


def test_own_contract_labor_excluded_on_any_date():
    query = day_of(ln(Q, DRY, cl=777))
    own_other_date = ln(date(2026, 9, 25), PROT, cl=777)
    other = ln(date(2026, 9, 25), PROT, cl=778)
    assert m.evidence_for_day(query, [own_other_date, other], k_days=7) == [other]


def test_uncoded_at_T_stratum():
    T7 = datetime(2026, 10, 6)
    never = day_of(ln(Q, DRY, cl=1))
    late = day_of(ln(Q, DRY, cl=2, coded_at=T7))
    early = day_of(ln(Q, DRY, cl=3, coded_at=T7 - timedelta(seconds=1)))
    assert never.uncoded_at(7) and late.uncoded_at(7) and not early.uncoded_at(7)
    assert early.uncoded_at(3)
    rows = [(never, m.single(DRY, Decimal("8"), "t")), (late, None), (early, m.single(DRY, Decimal("8"), "t"))]
    mt = m.compute_metrics(rows, [7])
    u = mt["uncoded_at_T_k7"]
    assert u["days"] == 2 and u["proposals"] == 1
    v = m.verdicts(mt, [7])
    assert set(v) == {"all", "uncoded_at_T_k7"}
    assert m.verdicts(mt, [7], incomplete=True) == {"all": "incomplete", "uncoded_at_T_k7": "incomplete"}


def test_evidence_and_precedent_provenance():
    query = day_of(ln(Q, DRY, cl=1))
    ev = [ln(date(2026, 9, 25), DRY, cl=2, coded_at=datetime(2026, 9, 26)),
          ln(date(2026, 9, 30), PROT, cl=3)]
    prov = m.evidence_provenance([(query, ev)])
    assert prov["lines"] == 2 and prov["via_coded_at"] == 1 and prov["via_date_fallback"] == 1
    assert prov["earlier_dated"] == 1 and prov["later_dated"] == 1 and prov["later_dated_share"] == 0.5
    groups = m.precedent_groups(ev)
    assert [g.via_coded_at for g in groups] == [True, False]
    later = m.PrecedentGroup(9, date(2026, 9, 30), ((DRY, Decimal("6")), (PROT, Decimal("2"))), via_coded_at=False)
    p = m.propose_arm_a(analysis([seg({DRY: 1.0}), seg({PROT: 1.0})], [later]), 0.6, 1)
    assert p.kind == "split" and p.precedent is later
    pp = m.precedent_provenance([(query, p), (query, None)])
    assert pp["split_precedents"] == 1 and pp["later_dated"] == 1 and pp["via_date_fallback"] == 1


def test_past_only_excludes_later_dated():
    query = day_of(ln(Q, DRY, cl=1))
    later = ln(date(2026, 9, 30), PROT, cl=2, coded_at=datetime(2026, 9, 30, 18))
    earlier = ln(date(2026, 9, 28), PROT, cl=3)
    assert m.evidence_for_day(query, [later, earlier], k_days=7) == [later, earlier]
    assert m.evidence_for_day(query, [later, earlier], k_days=7, past_only=True) == [earlier]


def test_zero_hour_scaled_line_falls_back_and_zero_hour_truth_not_split():
    g = grp(date(2026, 9, 24), 1, dry="7.9", prot="0.1")
    assert m.scale_hours(g, Decimal("1")) is None  # 0.1/8 of 1h rounds to 0
    p = m.propose_arm_a(analysis([seg({DRY: 1.0}), seg({PROT: 1.0})], [g], hours="1"), 0.6, 1)
    assert p.kind == "single" and "split_fallback" in p.flags
    d = day_of(ln(Q, DRY, "8", cl=5), ln(Q, PROT, "0", cl=5))
    assert not d.is_split and d.truth == ((DRY, Decimal("8")),)
    assert m.exact_correct(d, m.single(DRY, Decimal("8"), "t"))


def test_run_backtest_new_sections_and_markdown(tmp_path):
    res = m.run_backtest(_corpus(), ks=(7,), cal=(m.DEFAULT_CAL_SINCE, m.DEFAULT_CAL_UNTIL),
                         hold=(m.DEFAULT_HOLD_SINCE, m.DEFAULT_HOLD_UNTIL))
    k7 = res["arms"]["arm_a"]["k7"]
    assert "uncoded_at_T_k7" in k7["holdout"]["metrics"] and "uncoded_at_T_k7" in k7["holdout"]["verdict"]
    assert k7["holdout"]["evidence_provenance"]["lines"] > 0
    assert "split_precedent_provenance" in k7["holdout"]
    assert "sensitivity_past_only_priors" in k7
    assert "uncoded_at_T_k7" in res["arms"]["baseline_s0_vendor_project"]["holdout"]["verdict"]
    assert res["population"]["holdout_uncoded_at_T_k7_days"] >= 1
    md = m.render_markdown(res, "S", None)
    assert "realistic" in md and k7["fit"]["status"] in md and "arm_a_past_only_priors" in md


def test_gen_params_and_cache_key(tmp_path, monkeypatch):
    t = FakeTransport()
    m.run_arm_b(_jobs(), {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5",
                cache=m.LlmCache(tmp_path / "c"), budget_usd=Decimal("5"), max_calls=10)
    assert t.kwargs[0]["extra_body"] == {"temperature": 0, "reasoning_effort": "minimal", "thinking": "off"}
    assert t.kwargs[0]["max_tokens"] == m.LLM_MAX_TOKENS
    h0 = m.prompt_hash("x", "s", "u")
    monkeypatch.setattr(m, "LLM_MAX_TOKENS", m.LLM_MAX_TOKENS + 1)
    h1 = m.prompt_hash("x", "s", "u")
    monkeypatch.setattr(m, "LLM_GEN_PARAMS", {"temperature": 1})
    assert len({h0, h1, m.prompt_hash("x", "s", "u")}) == 3


def test_truncated_response_not_cached(tmp_path):
    t = FakeTransport(stop_reason="max_tokens")
    props, st, _ = m.run_arm_b(_jobs(), {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5",
                               cache=m.LlmCache(tmp_path / "c"), budget_usd=Decimal("5"), max_calls=10)
    assert st.truncated == 1 and st.statuses["truncated"] == 1 and list(props.values()) == [None]
    assert not (tmp_path / "c").exists()


def test_per_call_budget_check_aborts_before_overrun(tmp_path):
    spent = {"v": Decimal("0")}
    jobs = _jobs() * 1
    other = next(d for d in m.build_crew_days(hp2_lines()) if d.contract_labor_id == 1001)
    jobs.append((other, jobs[0][1]))
    t = FakeTransport(on_call=lambda: spent.__setitem__("v", Decimal("4.999")))
    cache = m.LlmCache(tmp_path / "c")
    with pytest.raises(m.LlmBudgetAbort) as ei:
        m.run_arm_b(jobs, {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5", cache=cache,
                    budget_usd=Decimal("5"), max_calls=10, spend_fn=lambda: spent["v"])
    assert ei.value.token == "per_call_estimate_exceeds_budget" and t.calls == 1
    assert len(list((tmp_path / "c").glob("*.json"))) == 1  # the first answer is kept
    # spend so far counts against the up-front estimate too
    with pytest.raises(m.LlmBudgetRefused):
        m.run_arm_b(jobs, {c.id: c for c in CATALOG}, transport=FakeTransport(), model="claude-sonnet-5-5",
                    cache=cache, budget_usd=Decimal("5"), max_calls=10, spend_fn=lambda: Decimal("4.999"))


def _many_jobs(n: int):
    lines = hp2_lines()
    ev = m.evidence_for_day(next(d for d in m.build_crew_days(lines) if d.contract_labor_id == 1000), lines, k_days=7)
    return [(day_of(ln(Q, DRY, "8", cl=5000 + i, note=f"drywall mud {i}")), ev) for i in range(n)]


def test_transport_errors_excluded_not_scored(tmp_path):
    t = FakeTransport(errors={2})
    props, st, _ = m.run_arm_b(_many_jobs(4), {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5",
                               cache=m.LlmCache(tmp_path / "c"), budget_usd=Decimal("5"), max_calls=10)
    assert st.transport_errors == 1 and len(props) == 3  # the errored day is excluded, not an abstain


def test_too_many_transport_errors_abort(tmp_path):
    t = FakeTransport(errors={1, 2, 3, 4})
    with pytest.raises(m.LlmTransportAbort):
        m.run_arm_b(_many_jobs(6), {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5",
                    cache=m.LlmCache(tmp_path / "c"), budget_usd=Decimal("5"), max_calls=10)
    assert m.transport_error_limit(100) == 5 and m.transport_error_limit(10) == 3


def test_incomplete_arm_b_verdict_and_paired_arm_a():
    def fake_llm(jobs, k):
        first = jobs[0][0]
        return {first.key: m.single(DRY, first.total_hours, "arm_b")}, m.LlmState(calls=1), Decimal("0")

    corpus = _corpus()
    res = m.run_backtest(corpus, ks=(7,), cal=(m.DEFAULT_CAL_SINCE, m.DEFAULT_CAL_UNTIL),
                         hold=(m.DEFAULT_HOLD_SINCE, m.DEFAULT_HOLD_UNTIL), llm=fake_llm)
    b = res["arms"]["arm_b"]
    assert b["status"] == "incomplete"
    assert set(b["k7"]["holdout"]["verdict"].values()) == {"incomplete"}
    assert b["k7"]["days_attempted"] == 1 < b["k7"]["holdout_days"]
    assert b["k7"]["paired_arm_a_same_days"]["metrics"]["all"]["days"] == 1


def test_parse_hours_mismatch_duplicates_and_numeric_normalisation():
    split = '{"proposal":"split","lines":[{"sub_cost_code_number":"35.00","hours":5},{"sub_cost_code_number":"44.00","hours":2}]}'
    assert m.parse_llm_response(split, ALLOWED, Decimal("8")) == (None, "hours_mismatch")
    dup = '{"proposal":"split","lines":[{"sub_cost_code_number":"35.00","hours":4},{"sub_cost_code_number":"35","hours":4}]}'
    assert m.parse_llm_response(dup, ALLOWED, Decimal("8")) == (None, "invalid_shape")
    for num in ('"35"', "35", "35.0"):
        p, s = m.parse_llm_response(
            '{"proposal":"single","lines":[{"sub_cost_code_number":%s,"hours":8}]}' % num, ALLOWED, Decimal("8"))
        assert s == "ok" and p.lines == ((DRY, Decimal("8")),)


def test_parse_catalog_collision_resolved_by_id():
    collide = {DRY: "35.00", PROT: "35.00"}
    by_num = '{"proposal":"single","lines":[{"sub_cost_code_number":"35.00","hours":8}]}'
    assert m.parse_llm_response(by_num, collide, Decimal("8")) == (None, "ambiguous_code")
    by_id = '{"proposal":"single","lines":[{"sub_cost_code_id":%d,"sub_cost_code_number":"35.00","hours":8}]}' % PROT
    p, s = m.parse_llm_response(by_id, collide, Decimal("8"))
    assert s == "ok" and p.lines == ((PROT, Decimal("8")),)
    wrong = '{"proposal":"single","lines":[{"sub_cost_code_id":%d,"sub_cost_code_number":"44.00","hours":8}]}' % DRY
    assert m.parse_llm_response(wrong, ALLOWED, Decimal("8")) == (None, "invalid_shape")
    unknown = '{"proposal":"single","lines":[{"sub_cost_code_id":%d,"hours":8}]}' % FRAME
    assert m.parse_llm_response(unknown, ALLOWED, Decimal("8")) == (None, "unknown_code")


# ---------------------------------------------------------------------------
# Fix round 2
# ---------------------------------------------------------------------------


def test_zero_hour_lines_dropped_from_precedents_and_scoring():
    note = "drywall mud plastic doors"
    ev = [ln(date(2026, 9, 24), DRY, "6", cl=50, note=note), ln(date(2026, 9, 24), PROT, "2", cl=50, note=note),
          ln(date(2026, 9, 24), CHAN, "0", cl=50, note=note)]
    (g,) = m.precedent_groups(ev)
    assert g.codes == frozenset({DRY, PROT})
    assert m.nearest_precedent_with_codes([g], frozenset({DRY, PROT}), Q) is g
    assert m.precedent_groups([ln(date(2026, 9, 24), CHAN, "0", cl=51)]) == []
    tokens, weights, idf = _idf_bits(ev + [ln(date(2026, 9, 1), FRAME, "8", cl=52, note="unrelated framing")])
    sc = m.score_segment(m.tokenize_labor_text("drywall mud"), Q,
                         [(x, tokens[x.line_id], weights[x.line_id]) for x in ev], idf)
    assert CHAN not in sc.scores and CHAN not in sc.support and DRY in sc.scores


def test_dry_run_sql_counts_positive_hour_codes_only():
    assert "CASE WHEN li.[Hours] > 0 THEN li.[SubCostCodeId] END" in m._DAY_COUNTS_SQL


def test_estimate_is_padded():
    from intelligence.observability.pricing import compute_cost_usd

    user = "x" * 3000
    tokens_in = -(-(len(m.LLM_SYSTEM) + len(user)) // 3)
    raw = compute_cost_usd(provider="anthropic", model="claude-sonnet-5-5",
                           usage=Usage(input_tokens=tokens_in, output_tokens=m.LLM_MAX_TOKENS))
    assert m.estimate_call_cost_usd("claude-sonnet-5-5", m.LLM_SYSTEM, user) == (
        Decimal(f"{raw:.6f}") * Decimal("1.5")).quantize(Decimal("0.000001"))


def test_too_many_truncated_answers_abort(tmp_path):
    t = FakeTransport(stop_reason="max_tokens")
    with pytest.raises(m.LlmTransportAbort):
        m.run_arm_b(_many_jobs(6), {c.id: c for c in CATALOG}, transport=t, model="claude-sonnet-5-5",
                    cache=m.LlmCache(tmp_path / "c"), budget_usd=Decimal("5"), max_calls=10)
    assert not (tmp_path / "c").exists()


def _sensitivity_corpus() -> m.Corpus:
    c = _fit_corpus(DRY)
    lines = list(c.lines)
    cl = 7000
    d = date(2026, 8, 20)
    i = 0
    while d <= date(2026, 9, 18):
        if d.weekday() < 5:
            cl += 1
            i += 1
            scc, note = [(DRY, "drywall mud tape"), (PROT, "plastic door protection"),
                         (DRY, "drywall mud plastic door")][i % 3]
            lines.append(ln(d, scc, "8", cl=cl, note=note, coded_at=datetime.combine(d, datetime.min.time()) + timedelta(hours=18)))
        d += timedelta(days=1)
    return m.Corpus(lines=lines, catalog=CATALOG, project_abbr={})


def test_past_only_row_uses_the_fitted_pair():
    corpus = _sensitivity_corpus()
    cal = (m.DEFAULT_CAL_SINCE, m.DEFAULT_CAL_UNTIL)
    hold = (m.DEFAULT_HOLD_SINCE, m.DEFAULT_HOLD_UNTIL)
    res = m.run_backtest(corpus, ks=(7,), cal=cal, hold=hold)
    k7 = res["arms"]["arm_a"]["k7"]
    tau, mm = k7["fit"]["tau"], k7["fit"]["m"]
    tokens, weights, idf = _idf_bits(corpus.lines)
    days = [d for d in m.build_crew_days(corpus.lines) if d.timelog_sourced and hold[0] <= d.line_date <= hold[1]]
    an = [m.analyze_day(d, m.evidence_for_day(d, corpus.lines, k_days=7, past_only=True), tokens, weights, idf)
          for d in days]

    def recompute(t, mv):
        return m.compute_metrics([(a.day, m.propose_arm_a(a, t, mv)) for a in an], [7])

    assert k7["sensitivity_past_only_priors"]["metrics"] == recompute(tau, mm)
    others = [recompute(float(t), mv) for t in m.TAU_GRID for mv in m.M_GRID if (float(t), mv) != (tau, mm)]
    assert any(o != recompute(tau, mm) for o in others)  # the fixture discriminates between pairs
