"""U-698 phase 2 — decision model + backtest harness (fakes only; no DB, no LLM, no blob)."""
from __future__ import annotations

import csv
from collections import defaultdict
import json
import math
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from entities.coding_suggestion.business.decision_model import features as F
from entities.coding_suggestion.business.decision_model import learn as L
from entities.coding_suggestion.business.decision_model import stats as S
from scripts import u698_decision_model_backtest as bt
from scripts.u653b_backtest import BillRow

T0 = datetime(2025, 10, 1, 9, 0, 0)


def _pb(bid, vendor, created, scc, project=1, amount=100.0, has_pdf=False, coded_at=None):
    return F.PriorBill(bid, vendor, created, scc, project, amount, has_pdf, coded_at)


def _q(bid, vendor, created, amount=100.0, project=1, has_text=True):
    return F.QueryBill(bid, vendor, created, amount, project, has_text, 50 if has_text else 0)


# ---------------------------------------------------------------------------------------------
# Leakage: as-of features
# ---------------------------------------------------------------------------------------------


def test_vendor_priors_are_strictly_earlier_and_exclude_equal_timestamps():
    t = T0 + timedelta(days=10)
    idx = F.AsOfIndex([
        _pb(1, 7, T0, 10),
        _pb(2, 7, t, 11),               # same timestamp as the query: must NOT count
        _pb(3, 7, t + timedelta(seconds=1), 12),
        _pb(4, 7, t - timedelta(seconds=1), 13),
    ])
    ids = [p.bill_id for p in idx.vendor_priors(7, as_of=t, exclude_bill_id=99)]
    assert ids == [1, 4]


def test_base_rate_is_strictly_earlier():
    t = T0 + timedelta(days=1)
    idx = F.AsOfIndex([_pb(1, 1, T0, 10), _pb(2, 2, t, 10), _pb(3, 3, t, 11)])
    assert idx.base_rate(10, as_of=t) == 1.0
    assert idx.base_rate(11, as_of=t) == 0.0


def _ctx_features(idx, q, mode="A", nbrs=()):
    ctx = F.build_context(idx, q, mode=mode, neighbours=nbrs)
    return ctx.candidates, [F.m0_features(ctx, c) for c in ctx.candidates], ctx


def test_features_for_bill_t_never_see_bills_at_or_after_t():
    rng = random.Random(3)
    early = [_pb(i, 5, T0 + timedelta(hours=i), rng.choice([10, 11, 12]), project=rng.choice([1, 2]),
                 amount=rng.uniform(10, 500)) for i in range(1, 30)]
    t = T0 + timedelta(hours=40)
    q = _q(500, 5, t)
    base = _ctx_features(F.AsOfIndex(early), q)
    # Add a flood of bills at the same instant and later, all with a NEW code: nothing may move.
    later = [_pb(1000 + i, 5, t + timedelta(minutes=i), 99, amount=1e6) for i in range(0, 50)]
    same = [_pb(2000, 5, t, 99)]
    after = _ctx_features(F.AsOfIndex(early + later + same), q)
    assert base[0] == after[0] and base[1] == after[1]
    assert 99 not in after[0]


def test_coded_cutoff_drops_priors_coded_after_query():
    t = T0 + timedelta(days=5)
    idx = F.AsOfIndex([
        _pb(1, 7, T0, 10, coded_at=T0 + timedelta(days=1)),
        _pb(2, 7, T0, 11, coded_at=t + timedelta(days=1)),
        _pb(3, 7, T0, 12, coded_at=None),
    ], cutoff=F.PRIOR_CUTOFF_CODED, unknown=F.UNKNOWN_CODED_DROP)
    assert [p.bill_id for p in idx.vendor_priors(7, as_of=t)] == [1]


def test_neighbour_not_strictly_earlier_is_rejected():
    t = T0 + timedelta(days=1)
    idx = F.AsOfIndex([])
    bad = F.NeighbourEvidence(0.5, 0.5, 10, True, 2, t)  # same instant
    with pytest.raises(F.LeakageError):
        F.build_context(idx, _q(1, 1, t), mode="A", neighbours=[bad])
    ok = F.NeighbourEvidence(0.5, 0.5, 10, True, 2, t - timedelta(seconds=1))
    assert F.build_context(idx, _q(1, 1, t), mode="A", neighbours=[ok]).vote_top == 10


def test_feature_set_carries_no_coding_time_text():
    names = " ".join(F.M0_FEATURES + F.M1_FEATURES).lower()
    for banned in ("desc", "memo", "line_item", "creator", "intake", "bill_number"):
        assert banned not in names
    for sql in (bt._EXTRA_SQL, bt._CATALOG_SQL):
        assert "Description" not in sql and "Memo" not in sql and "[Name]" not in sql


# ---------------------------------------------------------------------------------------------
# Label
# ---------------------------------------------------------------------------------------------


def test_label_is_dominant_by_absolute_amount_ties_to_lowest_line():
    lines = [(5, 10, Decimal("100")), (6, 11, Decimal("-150")), (7, 10, Decimal("40"))]
    assert F.dominant_label(lines) == 11          # |−150| beats 100+40=140
    assert F.line1_label(lines) == 10             # lowest line id
    tie = [(9, 20, 50), (3, 21, 25), (4, 21, 25)]  # 50 vs 50: SCC 21 holds line 3
    assert F.dominant_label(tie) == 21


def test_label_matches_u653b_definition():
    from scripts.u653b_backtest import dominant_scc_from_line_amounts

    rng = random.Random(11)
    for _ in range(300):
        lines = [(rng.randrange(1, 60), rng.choice([1, 2, 3]), rng.choice([10, -10, 25, 5, 0]))
                 for _ in range(rng.randrange(1, 6))]
        lines = list({ln[0]: ln for ln in lines}.values())
        ref, _p, _m = dominant_scc_from_line_amounts([(i, s, None, a) for i, s, a in lines])
        assert F.dominant_label(lines) == ref


# ---------------------------------------------------------------------------------------------
# History parity with U-653b
# ---------------------------------------------------------------------------------------------


def test_history_scope_matches_u653b_predict_modes():
    from intelligence.cascade.bill_coding_payload import PriorBillVote
    from scripts.u653b_backtest import predict_mode_a, predict_mode_b

    rng = random.Random(5)
    for trial in range(200):
        n = rng.randrange(0, 12)
        priors = [_pb(i, 1, T0 + timedelta(hours=i), rng.choice([10, 11, 12, 13]),
                      project=rng.choice([1, 2, None])) for i in range(n)]
        votes = [PriorBillVote(p.bill_id, p.created, p.label_scc_id, p.project_id) for p in priors]
        proj = rng.choice([1, 2, None])
        row = BillRow(999, 1, T0 + timedelta(days=9), 10, proj, Decimal(1), False)
        for mode, pred in (("A", predict_mode_a(row, votes)), ("B", predict_mode_b(row, votes))):
            h, _scoped = F.history_scope(priors, mode=mode, project_id=proj)
            assert h.top == pred.predicted_scc_id, (trial, mode)
            if pred.raw_score is not None and h.available:
                assert math.isclose(h.top_share, float(pred.raw_score), rel_tol=1e-9)


def test_strong_threshold_matches_live_constant():
    from entities.coding_suggestion.business.bill_suggestion_service import HISTORY_STRONG_RAW_SCORE
    from scripts.u653b_backtest import MIN_PRIOR_BILLS

    assert float(HISTORY_STRONG_RAW_SCORE) == F.HISTORY_STRONG_RAW_SCORE
    assert MIN_PRIOR_BILLS == F.MIN_PRIOR_BILLS


# ---------------------------------------------------------------------------------------------
# Candidates, ranking, abstain, explanation
# ---------------------------------------------------------------------------------------------


def test_candidates_union_dedupe_and_catalog_filter():
    t = T0 + timedelta(days=30)
    priors = [_pb(i, 1, T0 + timedelta(hours=i), c) for i, c in enumerate([10, 10, 10, 11, 12, 13], 1)]
    nb = [F.NeighbourEvidence(0.9, 0.8, 10, True, 1, T0), F.NeighbourEvidence(0.5, 0.4, 20, False, 2, T0),
          F.NeighbourEvidence(0.4, 0.4, 21, False, 3, T0), F.NeighbourEvidence(0.3, 0.3, 22, False, 4, T0)]
    llm = F.LlmAnswers(haiku_scc_id=30, haiku_conf=0.8, sonnet_scc_id=10, sonnet_conf=0.9)
    ctx = F.build_context(F.AsOfIndex(priors), _q(100, 1, t), mode="B", neighbours=nb,
                          choosable=lambda c: c != 21, llm=llm)
    assert ctx.candidates == (10, 11, 12, 20, 22, 30)
    assert 21 not in ctx.vote_weights  # filtered neighbours never vote


def test_rank_candidates_argmax_runner_up_and_abstain():
    d = L.rank_candidates([5, 6, 7], [0.2, 0.9, 0.4])
    assert (d.scc_id, d.runner_up, d.raw_prob) == (6, 7, 0.9)
    d2 = L.rank_candidates([5, 6], [0.2, 0.6], calibrate=lambda p: p / 2, abstain_below=0.5)
    assert d2.abstained and d2.prob == 0.3
    assert L.rank_candidates([], []).abstained
    assert L.rank_candidates([9, 4], [0.5, 0.5]).scc_id == 9  # full tie -> candidate order, never scc id


def test_explanation_contributions_sum_to_the_logit():
    rng = random.Random(1)
    x = [[rng.gauss(0, 1) for _ in range(4)] for _ in range(400)]
    y = [1 if rng.random() < 1 / (1 + math.exp(-(r[0] - r[2]))) else 0 for r in x]
    m = L.fit_logistic(x, y, ("a", "b", "c", "d"), l2=0.1)
    probe = [0.7, 0.0, -1.2, 0.3]
    contribs = m.contributions(probe)
    assert math.isclose(m.bias + sum(v for _n, v in contribs), m.logit(probe), abs_tol=1e-9)
    top = L.top_contributions(contribs, k=3)
    assert len(top) == 3 and all(n != "b" for n, _ in top)  # zero feature contributes 0
    assert abs(top[0][1]) >= abs(top[1][1]) >= abs(top[2][1])


# ---------------------------------------------------------------------------------------------
# Learners
# ---------------------------------------------------------------------------------------------


def test_logistic_regression_recovers_known_weights():
    rng = random.Random(42)
    true_w, true_b = [1.5, -2.0, 0.5], -0.3
    x, y = [], []
    for _ in range(20000):
        r = [rng.gauss(0, 1), rng.gauss(0, 1) if rng.random() < 0.5 else 0.0, rng.gauss(0, 2)]
        t = true_b + sum(w * v for w, v in zip(true_w, r))
        x.append(r)
        y.append(1 if rng.random() < 1 / (1 + math.exp(-t)) else 0)
    m = L.fit_logistic(x, y, ("a", "b", "c"), l2=1e-3)
    assert m.converged
    for w, s, tw in zip(m.weights, m.scales, true_w):
        assert abs(w / s - tw) < 0.12
    assert abs(m.bias - true_b) < 0.1
    again = L.fit_logistic(x, y, ("a", "b", "c"), l2=1e-3)
    assert again.weights == m.weights  # deterministic


def test_sign_constraint_pins_a_negative_weight_to_zero():
    rng = random.Random(7)
    x, y = [], []
    for _ in range(3000):
        r = [rng.gauss(0, 1), rng.gauss(0, 1)]
        t = 1.2 * r[0] - 1.0 * r[1]
        x.append(r)
        y.append(1 if rng.random() < 1 / (1 + math.exp(-t)) else 0)
    m = L.fit_logistic(x, y, ("pos", "neg"), l2=1.0, signs=(1, 1))
    assert m.weights[1] == 0.0 and m.weights[0] > 0 and m.pinned == ("neg",)


def test_isotonic_is_monotone_and_calibrates_a_skewed_set():
    rng = random.Random(9)
    scores, y = [], []
    for _ in range(5000):
        s = rng.random()
        scores.append(s)
        y.append(1 if rng.random() < s ** 3 else 0)  # raw score badly over-confident
    iso = L.fit_isotonic(scores, y)
    grid = [i / 100 for i in range(101)]
    vals = [iso(g) for g in grid]
    assert all(a <= b + 1e-12 for a, b in zip(vals, vals[1:]))
    assert S.ece(scores, [bool(v) for v in y]) > 0.15
    cal = [iso(s) for s in scores]
    assert S.ece(cal, [bool(v) for v in y]) < 0.03
    assert abs(iso(0.9) - 0.9 ** 3) < 0.12


def test_calibrator_choice_by_sample_size():
    assert isinstance(L.fit_calibrator([0.5] * 400, [1, 0] * 200), L.IsotonicMap)
    rng = random.Random(2)
    s = [rng.random() for _ in range(100)]
    yy = [1 if rng.random() < v else 0 for v in s]
    assert isinstance(L.fit_calibrator(s, yy), L.PlattMap)
    assert isinstance(L.fit_calibrator([0.5] * 5, [1] * 5), L.IdentityMap)


# ---------------------------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------------------------


def test_sign_test_matches_u697_and_known_values():
    from scripts.u697_vector_similar_bills_backtest import sign_test_p as ref

    for f_, b_ in [(0, 0), (5, 0), (11, 9), (29, 13), (24, 20), (40, 15)]:
        assert S.sign_test_p(f_, b_) == ref(f_, b_)
    assert S.sign_test_p(0, 0) is None
    assert math.isclose(S.sign_test_p(5, 0), 0.0625)


def test_paired_and_bootstrap():
    base = [True] * 50 + [False] * 50
    other = [True] * 60 + [False] * 40
    p = S.paired(base, other)
    assert (p["fixed"], p["broke"], p["diff_points"]) == (10, 0, 10.0)
    ci = S.bootstrap_diff_ci(base, other, resamples=500, seed=1)
    assert ci[0] > 0 and ci[0] <= 10.0 <= ci[1]
    assert ci == S.bootstrap_diff_ci(base, other, resamples=500, seed=1)
    assert S.bootstrap_diff_ci(base, base, resamples=200) == (0.0, 0.0)


def test_coverage_at_bars():
    rows = S.coverage_at_bars([0.95, 0.85, 0.6, None], [True, False, True, False], (0.5, 0.9))
    assert rows[0] == {"bar": 0.5, "coverage": 0.75, "precision": 2 / 3, "n_kept": 3}
    assert rows[1]["coverage"] == 0.25 and rows[1]["precision"] == 1.0


# ---------------------------------------------------------------------------------------------
# Harness pieces
# ---------------------------------------------------------------------------------------------


def test_choosable_matches_live_catalog_rules():
    from intelligence.cascade.bill_coding_payload import build_candidate_sccs_for_bill
    from intelligence.cascade.bill_coding import SUSPENSE_SCC_NUMBER

    cat = {
        1: bt.CatalogRef(1, "01.10", T0),
        2: bt.CatalogRef(2, "01.20", T0 + timedelta(days=10)),      # created later
        3: bt.CatalogRef(3, "02.10", T0, holding=True),             # holding
        4: bt.CatalogRef(4, "03.10", T0), 5: bt.CatalogRef(5, "03.10", T0),  # ambiguous
        6: bt.CatalogRef(6, SUSPENSE_SCC_NUMBER, T0),               # suspense
    }
    ch = bt.Choosable(cat).at(T0 + timedelta(days=5))
    assert [c for c in cat if ch(c)] == [1]
    # As-of ambiguity (P2-d): a duplicate number created LATER does not block the code earlier.
    cat2 = {7: bt.CatalogRef(7, "04.10", T0), 8: bt.CatalogRef(8, "04.10", T0 + timedelta(days=20))}
    assert bt.Choosable(cat2).at(T0 + timedelta(days=5))(7)
    assert not bt.Choosable(cat2).at(T0 + timedelta(days=21))(7)
    live = build_candidate_sccs_for_bill(
        {k: v for k, v in cat.items() if not v.holding}, as_of=T0 + timedelta(days=5),
        ambiguous_numbers=frozenset({"03.10"}))
    assert [r["number"] for r in live] == ["01.10"]
    assert bt.Choosable(cat).at(T0 + timedelta(days=11))(2)


def test_cv_folds_cover_the_eval_window_without_gaps():
    folds = bt.cv_folds(date(2026, 4, 11), date(2026, 10, 10))
    assert len(folds) == 6 and folds[0][0] == date(2026, 4, 11) and folds[-1][1] == date(2026, 10, 10)
    for (a, b), (c, _d) in zip(folds, folds[1:]):
        assert b + timedelta(days=1) == c


def test_load_e1_rows_reads_llm_columns_from_committed_csv():
    rows = bt.load_e1_rows(bt.DEFAULT_BILL_IDS_FROM)
    assert len(rows) == 1002
    esc = [r for r in rows.values() if r.llm.sonnet_scc_id is not None]
    assert 200 <= len(esc) <= 270
    assert all(r.llm.haiku_conf is None or 0 <= r.llm.haiku_conf <= 1 for r in rows.values())


def _scored(bid, created, mode, cands):
    b = BillRow(bid, 1, created, cands[0], 1, Decimal(10), False)
    ctx = F.build_context(F.AsOfIndex([]), _q(bid, 1, created), mode=mode,
                          llm=F.LlmAnswers(haiku_scc_id=cands[0], haiku_conf=0.8))
    ctx = F.BillContext(**{**ctx.__dict__, "candidates": tuple(cands)})
    return bt.Scored(bt.Row(b, mode, ctx), L.Decision(cands[0], 0.5, 0.5, None), "cv0",
                     {c: 0.5 for c in cands})


def test_m1_out_of_fold_never_sees_its_own_fold(monkeypatch):
    items = [_scored(i, T0 + timedelta(days=i), m, [10 + i % 3, 20]) for i in range(50) for m in "AB"]
    seen: list[set[int]] = []
    real = bt.m1_rows_xy

    def spy(train):
        seen.append({s.row.bill.bill_id for s in train})
        return real(train)

    monkeypatch.setattr(bt, "m1_rows_xy", spy)
    dec, fold_of, models = bt.m1_cross_fit(items, l2=1.0, k=5)
    folds = bt.time_folds({s.row.bill.bill_id: s.row.bill.created for s in items}, 5)
    assert len(models) == 5 and len(seen) == 5
    for f, train_ids in enumerate(seen):
        test_ids = {b for b, ff in folds.items() if ff == f}
        assert test_ids and not (train_ids & test_ids)
        assert all(fold_of[(b, m)] == f for b in test_ids for m in "AB")
    assert set(dec) == {s.row.key for s in items}


def test_m1_skips_mode_a_strong_history():
    priors = [_pb(i, 1, T0 + timedelta(hours=i), 10) for i in range(1, 8)]
    llm = F.LlmAnswers(haiku_scc_id=11, haiku_conf=0.8)
    t = T0 + timedelta(days=3)
    b = BillRow(500, 1, t, 10, 1, Decimal(1), False)
    for mode, expect in (("A", False), ("B", True)):
        ctx = F.build_context(F.AsOfIndex(priors), _q(500, 1, t), mode=mode, llm=llm)
        assert ctx.history.strong
        assert bt.m1_eligible(bt.Row(b, mode, ctx)) is expect


# ---------------------------------------------------------------------------------------------
# End-to-end on a synthetic population (fake neighbours; nothing external)
# ---------------------------------------------------------------------------------------------


def _synthetic_population(seed=4):
    rng = random.Random(seed)
    start = datetime(2025, 9, 1)
    bills, pdf, line1 = [], {}, {}
    vendors = {v: [100 + v, 200 + (v % 4), 300 + (v % 7)] for v in range(1, 25)}
    for i in range(1, 1601):
        v = rng.randrange(1, 25)
        codes = vendors[v]
        r = rng.random()
        label = codes[0] if r < 0.6 else codes[1] if r < 0.85 else codes[2]
        created = start + timedelta(hours=6 * i + rng.random())
        bills.append(BillRow(i, v, created, label, rng.choice([1, 2, 3]),
                             Decimal(str(round(rng.uniform(20, 3000), 2))), rng.random() < 0.1))
        line1[i] = label
        if created >= datetime(2025, 12, 1) and rng.random() < 0.7:
            pdf[i] = frozenset({f"t{label}", f"v{v}"})
    catalog = {c: bt.CatalogRef(c, f"{c // 100:02d}.{c % 100:02d}", datetime(2020, 1, 1))
               for cs in vendors.values() for c in cs}
    return bt.Population(bills=bills, line1_by_bill=line1,
                         coded_at_by_bill={b.bill_id: b.created + timedelta(days=3) for b in bills},
                         catalog=catalog, pdf_tokens_by_bill_id=pdf)


def _fake_neighbours(pop, *, idf_since, idf_until, idf_before, upto, coded_unknown=None, top_k=10,
                     max_tokens_per_bill=None):
    rng = random.Random(1)
    by_id = {b.bill_id: b for b in pop.bills}
    pdf = sorted((by_id[i] for i in pop.pdf_tokens_by_bill_id), key=lambda b: (b.created, b.bill_id))
    out = {}
    for k, b in enumerate(pdf):
        if b.created.date() > upto:
            continue
        prior = [p for p in pdf[:k] if p.created < b.created][-40:]
        scored = [(0.9 if p.label_scc_id == b.label_scc_id and rng.random() < 0.7 else 0.3 * rng.random(), p)
                  for p in prior]
        scored.sort(key=lambda t: -t[0])
        out[b.bill_id] = [F.NeighbourEvidence(s, s, p.label_scc_id, p.vendor_id == b.vendor_id,
                                              p.bill_id, p.created) for s, p in scored[:top_k]]
    return out


def _synthetic_e1(pop):
    rng = random.Random(8)
    out = {}
    ev = [b for b in pop.bills if b.bill_id in pop.pdf_tokens_by_bill_id
          and date(2026, 4, 11) <= b.created.date() <= date(2026, 10, 10)]
    for b in ev[:150]:
        for m in "AB":
            h = b.label_scc_id if rng.random() < 0.65 else b.label_scc_id + 1
            out[(b.bill_id, m)] = bt.E1Row(b.bill_id, m, "cold_start", h,
                                           F.LlmAnswers(haiku_scc_id=h, haiku_conf=0.8))
    return out


@pytest.fixture(scope="module")
def e2e():
    pop = _synthetic_population()
    e1 = _synthetic_e1(pop)
    opts = bt.Options(bootstrap=50)
    res = bt.run_backtest(pop, e1, opts, neighbours_fn=_fake_neighbours, log=lambda _m: None)
    return pop, opts, res


def test_end_to_end_backtest_produces_all_blocks(e2e):
    _pop, _opts, res = e2e
    assert res["chosen_variant"] in bt.VARIANTS
    for v in bt.VARIANTS:
        cv = res["variants"][v]["cv_pdf_eval"]
        assert cv["A"]["n"] > 0 and cv["A"]["m0"]["bill"] is not None
        assert len(res["variants"][v]["folds"]) == 6
    assert set(res["gates"]) == {"G1", "G2", "G3", "G4", "G5"}
    assert res["e1_table"]["A"]["n"] == 150
    assert res["fixed_split"]["B"]["n"] > 0
    assert 0 <= res["coded_time_leakage"]["share_coded_after_of_known"] <= 1
    # The model must learn the obvious signal: better than history alone on this synthetic set.
    cv = res["variants"][res["chosen_variant"]]["cv_pdf_eval"]
    assert cv["A"]["m0"]["bill"] >= cv["A"]["history_alone"]["bill"]


def test_oof_decisions_come_from_folds_trained_strictly_before(e2e):
    _pop, _opts, res = e2e
    folds = res["variants"][res["chosen_variant"]]["folds"]
    starts = {f["fold"]: f["start"] for f in folds}
    for s in res["_artefacts"]["oof"]:
        assert s.row.bill.created.date() >= starts[s.fold]


def test_write_outputs_are_privacy_safe(e2e, tmp_path):
    pop, opts, res = e2e
    paths = bt.write_outputs(res, pop, opts, tmp_path, "20261029T000000Z")
    assert len(paths) == 4
    with paths[0].open() as fh:
        header = next(csv.reader(fh))
    assert header == bt.PRED_COLUMNS
    json.loads(paths[1].read_text())
    json.loads(paths[2].read_text())


def test_privacy_guard_rejects_free_text_in_outputs(e2e, tmp_path, monkeypatch):
    pop, opts, res = e2e
    monkeypatch.setattr(
        bt, "render_report",
        lambda _r, _s: "| bill | note |\n|---|---|\n| 1 | framing lumber for the north wall |\n",
    )
    with pytest.raises(ValueError):
        bt.write_outputs(res, pop, opts, tmp_path, "20261029T000001Z")


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def test_dry_run_does_no_training_and_no_population_load(capsys):
    calls = {"dry": 0}

    def dry():
        calls["dry"] += 1
        return [BillRow(1, 1, datetime(2026, 5, 1), 10, 1, Decimal(1), False)], {1: "u"}

    def boom(*_a, **_k):
        raise AssertionError("must not load the population / train in dry-run")

    assert bt.main([], dry_loader=dry, pop_loader=boom, runner=boom) == 0
    out = capsys.readouterr().out
    assert calls["dry"] == 1 and "eval_window_pdf_linked: 1" in out and "--run" in out


# ---------------------------------------------------------------------------------------------
# Fix round 1 regressions
# ---------------------------------------------------------------------------------------------


def _two_candidate_scored(calibrator):
    """M0's RAW score prefers 20 (highest vote share) but 10 is FIRST in candidate order (history
    rank 1), so any tie that falls back to order or to the lowest scc id picks 10."""
    t = T0 + timedelta(days=3)
    priors = [_pb(i, 1, T0 + timedelta(hours=i), c) for i, c in enumerate([10, 10, 11, 12], 1)]
    nb = [F.NeighbourEvidence(0.9, 0.9, 20, True, 1, T0), F.NeighbourEvidence(0.3, 0.3, 10, True, 2, T0)]
    ctx = F.build_context(F.AsOfIndex(priors), _q(77, 1, t), mode="B", neighbours=nb,
                          llm=F.LlmAnswers(haiku_scc_id=10, haiku_conf=0.8))
    assert ctx.candidates[0] == 10 and 20 in ctx.candidates
    w = [0.0] * len(F.M0_FEATURES)
    w[F.M0_FEATURES.index("F7_vote_share")] = 2.0
    m0 = L.LogisticModel(F.M0_FEATURES, w, 0.0, [1.0] * len(w), 0.0)
    fit = bt.M0Fit(m0, calibrator, 0, 0)
    b = BillRow(77, 1, t, 20, 1, Decimal(10), False)
    return bt.score_rows(fit, [bt.Row(b, "B", ctx)], "cv0")[0]


def test_p1a_same_isotonic_block_keeps_m0_raw_order_through_m1():
    # One isotonic block: every raw score maps to the same calibrated value.
    flat = L.IsotonicMap([1.0], [0.5])
    s = _two_candidate_scored(flat)
    assert s.decision.scc_id == 20  # M0 itself ranks on raw
    w1 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]  # M1 that only reads M0's logit
    m1 = L.LogisticModel(F.M1_FEATURES, w1, 0.0, [1.0] * 6, 0.0)
    assert bt.m1_decide(m1, s).scc_id == 20  # not 10 (the lower scc id)


def test_p1a_rank_ties_fall_back_to_raw_m0_score_not_scc_id():
    d = L.rank_candidates([10, 20], [0.5, 0.5], tiebreak=[0.2, 0.9])
    assert d.scc_id == 20 and d.runner_up == 10


def test_p1b_coded_at_mapping():
    c, done, st_ = T0, T0 + timedelta(days=2), T0 + timedelta(days=3)
    assert bt.coded_at_for(c, done, st_, "completion") == (done, "completion_result")
    assert bt.coded_at_for(c, None, st_, "completion") == (st_, "status_completion")
    assert bt.coded_at_for(c, None, st_, "qbo_pull") == (c, "qbo_pull_created")
    assert bt.coded_at_for(c, None, st_, "backfill") == (None, "backfill_unknown")
    assert bt.coded_at_for(c, None, st_, "backfill", backfill_as_created=True) == (c, "backfill_created")
    assert bt.coded_at_for(c, None, None, "user") == (None, "unknown")


def test_p1b_coded_cutoff_admits_unknown_by_default_and_drops_on_request():
    t = T0 + timedelta(days=5)
    bills = [_pb(1, 7, T0, 10, coded_at=T0 + timedelta(days=1)),
             _pb(2, 7, T0, 11, coded_at=t + timedelta(days=1)),
             _pb(3, 7, T0, 12, coded_at=None)]
    admit = F.AsOfIndex(bills, cutoff=F.PRIOR_CUTOFF_CODED)
    drop = F.AsOfIndex(bills, cutoff=F.PRIOR_CUTOFF_CODED, unknown=F.UNKNOWN_CODED_DROP)
    assert [p.bill_id for p in admit.vendor_priors(7, as_of=t)] == [1, 3]
    assert [p.bill_id for p in drop.vendor_priors(7, as_of=t)] == [1]


def test_p1b_q4_share_is_over_known_coded_times():
    t = T0 + timedelta(days=5)
    rows = [BillRow(i, 7, T0 + timedelta(hours=i), 10, 1, Decimal(1), False) for i in (1, 2, 3, 4)]
    q = BillRow(9, 7, t, 10, 1, Decimal(1), False)
    pop = bt.Population(bills=rows + [q], coded_at_by_bill={
        1: T0 + timedelta(days=1), 2: t + timedelta(days=1), 3: None, 4: None, 9: None},
        coded_source_by_bill={1: "completion_result", 2: "completion_result", 3: "unknown", 4: "unknown"})
    out = bt.coded_time_leakage(pop, bt.build_index(pop), [q])
    assert out["priors"] == 4 and out["priors_known_coded_time"] == 2
    assert out["share_coded_after_of_known"] == 0.5 and out["share_unknown"] == 0.5
    assert out["coded_time_sources"] == {"completion_result": 2, "unknown": 2}


def test_p2a_forward_chained_m1_never_sees_its_own_or_later_folds(monkeypatch):
    items = [_scored(i, T0 + timedelta(days=i), m, [10 + i % 3, 20]) for i in range(50) for m in "AB"]
    seen: list[set[int]] = []
    real = bt.m1_rows_xy
    monkeypatch.setattr(bt, "m1_rows_xy", lambda tr: (seen.append({s.row.bill.bill_id for s in tr}), real(tr))[1])
    dec, fold_of, models = bt.m1_forward_chained(items, l2=1.0, k=5)
    folds = bt.time_folds({s.row.bill.bill_id: s.row.bill.created for s in items}, 5)
    assert [m["fold"] for m in models] == [1, 2, 3, 4]  # fold 0 has no earlier data -> M0 pick
    for train_ids, fm in zip(seen[1:] if len(seen) == 5 else seen, models):
        f = fm["fold"]
        assert train_ids and all(folds[b] < f for b in train_ids)
    assert not any(fold_of[k] == 0 for k in fold_of)
    assert all(folds[b] > 0 for b, _m in dec)


def test_p2b_g1_unadjudicated_outside_ladder_parity(e2e):
    import copy
    _pop, opts, res = e2e
    r = copy.deepcopy({k: v for k, v in res.items() if not k.startswith("_")})
    for m in "AB":
        t = r["e1_table"][m]
        t["m1_forward_vs_ladder"].update(diff_points=5.0, sign_test_p=0.001)
        t["ladder_parity_points"] = 0.3
    assert bt.gate_check(r, opts)["G1"]["status"] == "PASS"
    r["e1_table"]["B"]["ladder_parity_points"] = -0.8
    g1 = bt.gate_check(r, opts)["G1"]
    assert g1["status"] == "UNADJUDICATED" and g1["pass"] is False
    assert "cross_fit_diff_points" in g1 and g1["basis"] == "m1_forward_chained"


def test_p2c_variant_selected_on_calibration_slices_not_eval():
    variants = {
        bt.VARIANT_PDF: {"selection_cal_slice_pdf_top1": 0.70, "cv_pdf_eval": {"A": {"m0": {"bill": 0.50}}}},
        bt.VARIANT_PDF_HIST: {"selection_cal_slice_pdf_top1": 0.65, "cv_pdf_eval": {"A": {"m0": {"bill": 0.90}}}},
    }
    assert bt.select_variant(variants, bt.VARIANTS) == bt.VARIANT_PDF
    variants[bt.VARIANT_PDF_HIST]["selection_cal_slice_pdf_top1"] = 0.70
    assert bt.select_variant(variants, bt.VARIANTS) == bt.VARIANT_PDF  # tie keeps pdf_only


def test_p2e_calibrator_fit_on_pdf_rows_only(monkeypatch):
    pop = _synthetic_population()
    idx = bt.build_index(pop)
    ch = bt.Choosable(pop.catalog)
    rows = [bt.Row(b, m, bt.make_context(pop, idx, ch, b, m, {})) for b in pop.bills[:600] for m in "AB"]
    seen = {}
    real = bt.fit_calibrator
    monkeypatch.setattr(bt, "fit_calibrator", lambda sc, ok: (seen.setdefault("n", len(sc)), real(sc, ok))[1])
    fit = bt.fit_m0(rows, l2=1.0)
    fit_rows, cal_rows = bt.split_by_dates(rows)
    cut = bt.pdf_date_cut(rows)
    late_hist = [r for r in rows if not r.ctx.bill.has_text and (r.bill.created, r.bill.bill_id) >= cut]
    assert late_hist  # the fixture has history-only rows after the cut ...
    assert all(r.ctx.bill.has_text for r in cal_rows)  # ... and none reaches calibration
    assert not any(r in fit_rows for r in late_hist)    # ... nor the fit
    expect = sum(1 for r in cal_rows if r.ctx.candidates)
    assert seen["n"] == expect == fit.cal_pdf_n > 0


def test_p2f_step_halving_bottoming_out_is_not_converged(monkeypatch):
    rng = random.Random(3)
    x = [[rng.gauss(0, 1)] for _ in range(200)]
    y = [1 if r[0] > 0 else 0 for r in x]
    real = L._solve
    monkeypatch.setattr(L, "_solve", lambda h, g: [-v for v in real(h, g)])  # ascent direction
    m = L.fit_logistic(x, y, ("a",), l2=1.0)
    assert m.converged is False and m.iterations == 1


def test_live_mix_reweighting():
    ok = [True, False, True, True]
    weak = [True, True, False, False]
    out = bt.live_mix(ok, weak, 0.68)
    assert out["weak_top1"] == 0.5 and out["strong_top1"] == 1.0
    assert math.isclose(out["value"], 0.68 * 0.5 + 0.32 * 1.0)
    assert bt.live_mix([True], [True], 0.68)["value"] is None


def test_e2e_reports_forward_and_cross_fit_m1_and_convergence(e2e):
    _pop, _opts, res = e2e
    t = res["e1_table"]["A"]
    for k in ("m1_forward", "m1_cross_fit", "live_mix", "baseline_asymmetry", "ladder_parity_points"):
        assert k in t
    assert res["gates"]["G1"]["status"] in ("PASS", "FAIL", "UNADJUDICATED")
    assert isinstance(res["non_converged_fits"], list)
    assert "calibration slice" in res["chosen_rule"]
    for v in bt.VARIANTS:
        assert res["variants"][v]["selection_cal_slice_pdf_top1"] is not None


def test_round2_both_variants_calibrate_on_identical_pdf_bills_per_fold(e2e):
    _pop, _opts, res = e2e
    ids = res["_artefacts"]["cal_ids"]
    assert len(ids[bt.VARIANT_PDF]) == len(ids[bt.VARIANT_PDF_HIST]) == 6
    for a, b in zip(ids[bt.VARIANT_PDF], ids[bt.VARIANT_PDF_HIST]):
        assert a and a == b


def test_round2_selection_uses_no_bill_on_or_after_eval_since(e2e):
    _pop, opts, res = e2e
    for v in bt.VARIANTS:
        blk = res["variants"][v]
        assert blk["selection_cal_slice_max_created"].date() < opts.eval_since
        f0 = blk["folds"][0]
        assert blk["selection_cal_slice_pdf_rows"] == f0["cal_pdf_rows"]
    assert "fold 0" in res["chosen_rule"]


def test_round2_split_history_rows_before_cut_fit_after_cut_dropped():
    def row(bid, day, text):
        b = BillRow(bid, 1, T0 + timedelta(days=day), 10, 1, Decimal(1), False)
        q = _q(bid, 1, b.created, has_text=text)
        return bt.Row(b, "A", F.build_context(F.AsOfIndex([]), q, mode="A"))
    pdf = [row(i, i, True) for i in range(1, 11)]          # cut = PDF bill 9 (80 % of 10)
    hist = [row(100, 3, False), row(101, 9.5, False)]
    fit, cal = bt.split_by_dates(pdf + hist)
    assert {r.bill.bill_id for r in cal} == {9, 10}
    assert {r.bill.bill_id for r in fit} == set(range(1, 9)) | {100}


# ---------------------------------------------------------------------------------------------
# U-745a: --max-tokens-per-bill and --score-model
# ---------------------------------------------------------------------------------------------

from entities.coding_suggestion.business.decision_model import serve as SV  # noqa: E402

SHIPPED_M0 = SV.MODEL_PATH
U698_RUN_MODEL = Path(__file__).resolve().parents[1] / "docs/design/u698/u698_model_20261029T185458Z.json"


def _recording_neighbours(calls):
    def fn(pop, **kw):
        calls.append(kw)
        return _fake_neighbours(pop, **kw)
    return fn


def test_u745a_cap_defaults_to_none_everywhere():
    assert bt.Options().max_tokens_per_bill is None
    assert bt.Options().score_model is None
    opts = bt.options_from_args(bt.build_arg_parser().parse_args([]))
    assert opts.max_tokens_per_bill is None and opts.score_model is None
    import inspect
    assert inspect.signature(bt.neighbour_pass).parameters["max_tokens_per_bill"].default is None


def test_u745a_cap_reaches_every_neighbour_pass_and_is_validated():
    pop = _synthetic_population()
    calls = []
    # Frozen M0 keeps this fast; the neighbour passes are the same call sites as a fitted run.
    fast = dict(bootstrap=20, score_model=str(SHIPPED_M0))
    bt.run_backtest(pop, _synthetic_e1(pop), bt.Options(max_tokens_per_bill=50, **fast),
                    neighbours_fn=_recording_neighbours(calls), log=lambda _m: None)
    assert len(calls) == bt.CV_FOLDS + 1  # six CV folds + the fixed split
    assert all(c["max_tokens_per_bill"] == 50 for c in calls)
    calls.clear()
    bt.run_backtest(pop, _synthetic_e1(pop), bt.Options(**fast),
                    neighbours_fn=_recording_neighbours(calls), log=lambda _m: None)
    assert calls and all(c["max_tokens_per_bill"] is None for c in calls)
    with pytest.raises(SystemExit):
        bt.options_from_args(bt.build_arg_parser().parse_args(["--max-tokens-per-bill", "0"]))
    with pytest.raises(ValueError):
        bt.run_backtest(pop, {}, bt.Options(max_tokens_per_bill=0), neighbours_fn=_fake_neighbours,
                        log=lambda _m: None)


def test_u745a_neighbour_pass_puts_the_cap_on_token_source_config(monkeypatch):
    from scripts import u691_similar_bills_backtest as u691

    seen = []
    real = u691.TokenSourceConfig

    def spy(**kw):
        seen.append(kw.get("max_tokens_per_bill"))
        return real(**kw)

    monkeypatch.setattr(u691, "TokenSourceConfig", spy)
    pop = _cap_population()
    bt.neighbour_pass(pop, idf_since=date(2026, 1, 1), idf_until=date(2026, 12, 31), idf_before=None,
                      upto=date(2026, 12, 31), max_tokens_per_bill=7)
    bt.neighbour_pass(pop, idf_since=date(2026, 1, 1), idf_until=date(2026, 12, 31), idf_before=None,
                      upto=date(2026, 12, 31))
    assert seen == [7, None]


def _cap_population():
    """Q shares only the low-IDF token ``common`` with P; each also has a unique (high-IDF)
    token. A cap of 1 keeps only the unique token on BOTH sides, so P stops being Q's neighbour."""
    d = lambda day: datetime(2026, 5, day, 9)  # noqa: E731
    bills = [BillRow(1, 1, d(1), 101, 1, Decimal("100"), False),   # P
             BillRow(2, 2, d(2), 102, 1, Decimal("100"), False),   # filler
             BillRow(3, 3, d(3), 103, 1, Decimal("100"), False),   # filler
             BillRow(4, 4, d(4), 104, 1, Decimal("100"), False)]   # Q
    toks = {1: frozenset({"common", "punique"}), 2: frozenset({"gone"}), 3: frozenset({"gtwo"}),
            4: frozenset({"common", "qunique"})}
    return bt.Population(bills=bills, pdf_tokens_by_bill_id=toks)


def test_u745a_cap_changes_neighbours_on_both_sides():
    pop = _cap_population()
    kw = dict(idf_since=date(2026, 1, 1), idf_until=date(2026, 12, 31), idf_before=None,
              upto=date(2026, 12, 31))
    uncapped = bt.neighbour_pass(pop, **kw)
    capped = bt.neighbour_pass(pop, **kw, max_tokens_per_bill=1)
    assert [n.prior_bill_id for n in uncapped[4]] == [1]
    assert capped[4] == []
    # A cap at or above every bill's token count is a no-op.
    assert bt.neighbour_pass(pop, **kw, max_tokens_per_bill=2) == uncapped


def _no_fit(*_a, **_k):
    raise AssertionError("score-model mode must never fit or calibrate")


@pytest.fixture(scope="module")
def frozen_run():
    pop = _synthetic_population()
    e1 = _synthetic_e1(pop)
    mp = pytest.MonkeyPatch()
    for name in ("fit_logistic", "fit_calibrator", "fit_m0", "m1_fit", "m1_cross_fit",
                 "m1_forward_chained"):
        mp.setattr(bt, name, _no_fit)
    try:
        opts = bt.Options(bootstrap=20, max_tokens_per_bill=50, score_model=str(SHIPPED_M0))
        res = bt.run_backtest(pop, e1, opts, neighbours_fn=_fake_neighbours, log=lambda _m: None)
    finally:
        mp.undo()
    return pop, opts, res


def test_u745a_score_model_never_fits_and_matches_serve_scoring(frozen_run):
    _pop, _opts, res = frozen_run
    assert res["m0_source"] == bt.M0_SOURCE_FROZEN and res["m1_status"] == bt.M1_STATUS_SKIPPED
    assert res["chosen_variant"] == bt.VARIANT_FROZEN
    shipped = SV.load_m0_model(SHIPPED_M0)
    oof = [s for s in res["_artefacts"]["oof"] if s.row.ctx.candidates]
    assert oof
    for s in oof[:40]:
        want = SV.m0_score(shipped, s.row.ctx)
        assert s.decision.scc_id == want.scc_id
        assert s.decision.prob == pytest.approx(want.prob, abs=1e-12)
        assert s.decision.raw_prob == pytest.approx(want.raw_prob, abs=1e-12)
    cv = res["variants"][bt.VARIANT_FROZEN]["cv_pdf_eval"]
    for m in "AB":  # same baselines and metrics as a fitted run
        assert cv[m]["m0_vs_baseline"]["sign_test_p"] is not None or cv[m]["m0_vs_baseline"]["fixed"] == 0
        assert {"baseline_hist_strong_else_vote", "history_alone", "ece", "coverage_at_bars",
                "strong_history_overrides", "live_mix"} <= set(cv[m])
        assert res["fixed_split"][m]["n"] > 0


def test_u745a_score_model_skips_m1_cleanly(frozen_run):
    _pop, _opts, res = frozen_run
    for m in "AB":
        t = res["e1_table"][m]
        assert t["m1_status"] == bt.M1_STATUS_SKIPPED
        assert t["m1_forward"] is None and t["m1_cross_fit"] is None
        assert t["m1_forward_vs_ladder"]["diff_points"] is None
        assert t["m0"] is not None  # M0 is still reported on the E1 bills
    assert res["m1_fold_models"] == [] and res["m1_all_bills_weights_for_review"] is None
    assert res["gates"]["G1"]["status"] == "SKIPPED" and not res["gates"]["G1"]["pass"]


def test_u745a_both_saved_model_formats_load_identically():
    a = bt.load_frozen_m0(SHIPPED_M0)
    b = bt.load_frozen_m0(U698_RUN_MODEL)  # no top-level "features" key
    assert a.model.weights == b.model.weights and a.model.scales == b.model.scales
    assert a.calibrator.upper == b.calibrator.upper and a.sha256 != b.sha256
    import hashlib
    assert a.sha256 == hashlib.sha256(SHIPPED_M0.read_bytes()).hexdigest()


@pytest.mark.parametrize("mutate", [
    lambda d: d["m0"]["features"].pop(),                                       # missing feature
    lambda d: d["m0"]["features"].append("F99_new"),                           # extra feature
    lambda d: d["m0"]["features"].reverse(),                                   # reordered
    lambda d: d["features"].__setitem__(0, "F0_other"),                        # top-level list
])
def test_u745a_feature_list_mismatch_raises(tmp_path, mutate):
    data = json.loads(SHIPPED_M0.read_text())
    mutate(data)
    bad = tmp_path / "bad_model.json"
    bad.write_text(json.dumps(data))
    with pytest.raises(SV.M0ModelError):
        bt.load_frozen_m0(bad)
    pop = _synthetic_population()
    with pytest.raises(SV.M0ModelError):
        bt.run_backtest(pop, {}, bt.Options(score_model=str(bad)), neighbours_fn=_fake_neighbours,
                        log=lambda _m: None)

    def boom(*_a, **_k):
        raise AssertionError("must fail before any population load")

    with pytest.raises(SV.M0ModelError):
        bt.main(["--run", "--score-model", str(bad)], dry_loader=boom, pop_loader=boom, runner=boom)


def test_u745a_outputs_record_cap_and_model_sha(frozen_run, tmp_path):
    pop, opts, res = frozen_run
    paths = bt.write_outputs(res, pop, opts, tmp_path, "20261101T000000Z")  # privacy guard runs inside
    sha = bt.load_frozen_m0(SHIPPED_M0).sha256
    by_kind = {p.name.split("_")[1]: p for p in paths}
    # Frozen mode writes NO model json: this run's data hash is not the model's training hash.
    assert "model" not in by_kind and not list(tmp_path.glob("u698_model_*.json"))
    summ = json.loads(by_kind["summary"].read_text())
    report = by_kind["report"].read_text()
    paths = [by_kind["predictions"], None, by_kind["summary"], by_kind["report"]]
    assert "model JSON not written" in report
    assert summ["score_model"]["data_hash"] == json.loads(SHIPPED_M0.read_text())["data_hash"]
    assert summ["score_model"]["trained_through"] == "2026-09-11"
    assert summ["max_tokens_per_bill"] == 50 and summ["options"]["max_tokens_per_bill"] == 50
    assert summ["score_model"]["sha256"] == sha and summ["score_model"]["path"] == str(SHIPPED_M0)
    assert "max_tokens_per_bill): 50" in report and sha in report and "FROZEN" in report
    with paths[0].open() as fh:  # M1 columns are blank, never a copy of M0
        rows = list(csv.DictReader(fh))
    assert rows and all(r["m1_forward_scc_id"] == "" and r["m1_forward_correct"] == "" for r in rows)


def test_u745a_uncapped_fitted_outputs_say_so(e2e, tmp_path):
    pop, opts, res = e2e
    assert res["max_tokens_per_bill"] is None and res["m0_source"] == "fitted"
    paths = bt.write_outputs(res, pop, opts, tmp_path, "20261101T000001Z")
    assert json.loads(paths[2].read_text())["max_tokens_per_bill"] is None
    assert "none, uncapped" in paths[3].read_text()



# ---------------------------------------------------------------------------------------------
# U-745a fix round 1
# ---------------------------------------------------------------------------------------------


def test_fr1_default_window_folds_are_unchanged():
    folds = bt.cv_folds(date(2026, 4, 11), date(2026, 10, 10))
    assert folds == [
        (date(2026, 4, 11), date(2026, 5, 10)), (date(2026, 5, 11), date(2026, 6, 10)),
        (date(2026, 6, 11), date(2026, 7, 10)), (date(2026, 7, 11), date(2026, 8, 10)),
        (date(2026, 8, 11), date(2026, 9, 10)), (date(2026, 9, 11), date(2026, 10, 10)),
    ]


def test_fr1_short_window_folds_are_clamped_and_never_empty_or_reversed():
    since, until = date(2026, 9, 12), date(2026, 10, 31)
    folds = bt.cv_folds(since, until)
    assert folds == [(date(2026, 9, 12), date(2026, 10, 11)), (date(2026, 10, 12), date(2026, 10, 31))]
    for s_, e in folds:
        assert since <= s_ <= e <= until
    # one-day window
    assert bt.cv_folds(date(2026, 9, 12), date(2026, 9, 12)) == [(date(2026, 9, 12), date(2026, 9, 12))]


def test_fr1_short_window_run_scores_nothing_after_eval_until():
    pop = _synthetic_population()
    since, until = date(2026, 8, 1), date(2026, 9, 15)
    opts = bt.Options(bootstrap=20, eval_since=since, eval_until=until, score_model=str(SHIPPED_M0))
    res = bt.run_backtest(pop, {}, opts, neighbours_fn=_fake_neighbours, log=lambda _m: None)
    folds = res["variants"][bt.VARIANT_FROZEN]["folds"]
    assert len(folds) == 2 and all(f["start"] <= f["end"] <= until for f in folds)
    oof = res["_artefacts"]["oof"]
    assert oof and all(since <= s.row.bill.created.date() <= until for s in oof)
    n_expected = sum(1 for b in pop.bills if b.bill_id in pop.pdf_tokens_by_bill_id
                     and since <= b.created.date() <= until)
    assert res["variants"][bt.VARIANT_FROZEN]["cv_pdf_eval"]["A"]["n"] == n_expected
    assert res["fixed_split"]["A"]["n"] == n_expected


def test_fr1_in_sample_guard(frozen_run, tmp_path):
    pop, opts, res = frozen_run  # default window starts 2026-04-11 <= trained_through 2026-09-11
    assert res["in_sample"] is True
    paths = bt.write_outputs(res, pop, opts, tmp_path, "20261101T000002Z")
    summ = json.loads(next(p for p in paths if "summary" in p.name).read_text())
    report = next(p for p in paths if "report" in p.name).read_text()
    assert summ["in_sample"] is True
    assert "IN-SAMPLE: eval window overlaps model training through 2026-09-11" in report
    logs = []
    pop2 = _synthetic_population()
    out = bt.run_backtest(pop2, {}, bt.Options(bootstrap=20, eval_since=date(2026, 9, 12),
                                               eval_until=date(2026, 10, 5), score_model=str(SHIPPED_M0)),
                          neighbours_fn=_fake_neighbours, log=logs.append)
    assert out["in_sample"] is False and not any("IN-SAMPLE" in m for m in logs)
    logs.clear()
    bt.run_backtest(pop2, {}, bt.Options(bootstrap=20, eval_since=date(2026, 9, 11),
                                         eval_until=date(2026, 10, 5), score_model=str(SHIPPED_M0)),
                    neighbours_fn=_fake_neighbours, log=logs.append)
    assert any("IN-SAMPLE: eval window overlaps model training through 2026-09-11" in m for m in logs)


def test_fr1_fitted_run_has_no_in_sample_claim(e2e):
    assert e2e[2]["in_sample"] is None


def _nbr_scored(bid, label, pick, has_nbrs, mode="A"):
    q = _q(bid, 1, datetime(2026, 5, 1))
    nb = (F.NeighbourEvidence(0.5, 0.5, label, True, bid - 1000, datetime(2026, 4, 1)),) if has_nbrs else ()
    ctx = F.build_context(F.AsOfIndex([]), q, mode=mode, neighbours=nb)
    b = BillRow(bid, 1, datetime(2026, 5, 1), label, 1, Decimal("10"), False)
    d = L.Decision(scc_id=pick, prob=0.8, raw_prob=0.8, runner_up=None)
    return bt.Scored(bt.Row(b, mode, ctx), d, "cv0", {})


def test_fr2_live_abstain_counts_no_neighbour_rows_as_wrong():
    rows = [_nbr_scored(1, 10, 10, True), _nbr_scored(2, 10, 10, False),
            _nbr_scored(3, 10, 11, True), _nbr_scored(4, 10, 10, False)]
    blk = bt.mode_block(rows, auto_bar=0.9, bootstrap=10, seed=1)
    assert blk["m0"]["bill"] == pytest.approx(0.75)
    assert blk["m0_live_abstain"]["bill"] == pytest.approx(0.25)
    assert blk["no_neighbour_rows"] == 2
    assert blk["m0_live_abstain_vs_baseline"]["n"] == 4
    assert blk["strata"]["no_neighbours"]["n"] == 2 and blk["strata"]["has_neighbours"]["n"] == 2
    assert blk["strata"]["no_neighbours"]["m0_live_abstain"] == 0.0
    assert blk["strata"]["has_neighbours"]["m0_live_abstain"] == pytest.approx(0.5)


def test_fr2_live_abstain_matches_serve_m0_pick(frozen_run):
    """``live_abstain_pick`` equals the LIVE ``serve.m0_pick`` itself, fed the harness's inputs:
    the vendor's strictly-earlier priors, company-wide as-of label counts (so ``_LiveIndex``'s
    base rate equals the harness's as-of one), the RAW neighbour list and the as-of catalog."""
    pop, opts, res = frozen_run
    shipped = SV.load_m0_model(SHIPPED_M0)
    index = bt.build_index(pop)
    choosable = bt.Choosable(pop.catalog)
    raw_nbrs = _fake_neighbours(pop, idf_since=None, idf_until=None, idf_before=None, upto=opts.eval_until)
    def priors_for(b):
        counts = defaultdict(int)
        for pb in pop.bills:
            if pb.created < b.created:
                counts[pb.label_scc_id] += 1
        return SV.M0Priors(tuple(index.vendor_priors(b.vendor_id, as_of=b.created, exclude_bill_id=b.bill_id)),
                           dict(counts))

    def check(s, raw):
        b = s.row.bill
        live = SV.m0_pick(shipped, priors_for(b), bt.query_bill(pop, b), mode=s.row.mode,
                          neighbours=raw, choosable=choosable.at(b.created))
        assert bt.live_abstain_pick(s) == (live.scc_id if live else None)
        return live

    oof = res["_artefacts"]["oof"]
    picked = sum(check(s, raw_nbrs.get(s.row.bill.bill_id, [])) is not None for s in oof[:60])
    assert picked
    # The abstain branches: no neighbours at all, and neighbours the as-of catalog filters out.
    for s in oof[60:80]:
        b = s.row.bill
        bogus = [F.NeighbourEvidence(0.9, 0.9, 99999, False, n.prior_bill_id, n.prior_created)
                 for n in raw_nbrs.get(b.bill_id, [])]
        assert bogus
        for raw in ([], bogus):
            ctx = bt.make_context(pop, index, choosable, b, s.row.mode, {b.bill_id: raw})
            row = bt.Row(b, s.row.mode, ctx)
            d, cand_raw = bt.score_row(shipped.model, shipped.calibration, row)
            assert check(bt.Scored(row, d, "cv0", cand_raw), raw) is None


def test_fr3_report_header_prints_population_data_hash(e2e):
    pop, _opts, res = e2e
    assert res["population_data_hash"] == bt.data_hash(pop.bills)
    assert f"Population data_hash: `{bt.data_hash(pop.bills)}`" in bt.render_report(res, "x")


def test_fr6_case_discriminates_one_sided_caps():
    """Two-sided capping gives no neighbour; query-only would give P1, priors-only P2."""
    from scripts import u691_similar_bills_backtest as u691

    d = lambda day: datetime(2026, 5, day, 9)  # noqa: E731
    bills = [BillRow(1, 1, d(1), 101, 1, Decimal("100"), False),   # P1 {a, x}
             BillRow(2, 2, d(2), 102, 1, Decimal("100"), False),   # P2 {b}
             BillRow(3, 3, d(3), 103, 1, Decimal("100"), False),   # Q  {a, b}
             BillRow(4, 4, d(4), 104, 1, Decimal("100"), False),   # later {b}
             BillRow(5, 5, d(5), 105, 1, Decimal("100"), False),   # later {g5}
             BillRow(6, 6, d(6), 106, 1, Decimal("100"), False)]   # later {g6}
    toks = {1: frozenset({"atok", "xtok"}), 2: frozenset({"btok"}), 3: frozenset({"atok", "btok"}),
            4: frozenset({"btok"}), 5: frozenset({"gfive"}), 6: frozenset({"gsix"})}
    pop = bt.Population(bills=bills, pdf_tokens_by_bill_id=toks)
    kw = dict(idf_since=date(2026, 1, 1), idf_until=date(2026, 12, 31), idf_before=None,
              upto=date(2026, 12, 31))
    assert sorted(n.prior_bill_id for n in bt.neighbour_pass(pop, **kw)[3]) == [1, 2]
    assert bt.neighbour_pass(pop, **kw, max_tokens_per_bill=1)[3] == []

    # What a ONE-sided cap would give, built from u691's own primitives.
    enriched = [u691.EnrichedBill(bill=b, memo="", line_descriptions="", line_count=0) for b in bills]
    capped = u691.TokenSourceConfig(text_source=u691.TEXT_SOURCE_PDF, pdf_tokens_by_bill_id=toks,
                                    max_tokens_per_bill=1)
    uncapped = u691.TokenSourceConfig(text_source=u691.TEXT_SOURCE_PDF, pdf_tokens_by_bill_id=toks)
    idf = u691.compute_idf_from_corpus(enriched, since=date(2026, 1, 1), until=date(2026, 12, 31),
                                       config=uncapped)

    def one_sided(qcfg, pcfg):
        priors = {eb.bill.bill_id: u691.token_profile(eb, idf, config=pcfg) for eb in enriched[:2]}
        index = defaultdict(set)
        for bid, prof in priors.items():
            for t in prof.tokens:
                index[t].add(bid)
        q = u691.token_profile(enriched[2], idf, config=qcfg)
        return sorted(n.prior.enriched.bill.bill_id for n in u691.top_neighbours_any_vendor(
            q, idf, top_k=10, token_index=index, prior_by_id=priors))

    assert one_sided(capped, uncapped) == [1]   # query-only cap
    assert one_sided(uncapped, capped) == [2]   # priors-only cap
    assert one_sided(capped, capped) == []      # two-sided == the harness


def test_fr7_model_file_is_read_once_and_hashed_from_those_bytes(monkeypatch, tmp_path):
    import hashlib
    target = tmp_path / "m0_copy.json"
    target.write_bytes(SHIPPED_M0.read_bytes())
    reads = []
    real_bytes, real_text = Path.read_bytes, Path.read_text

    def rb(self, *a, **k):
        if self == target:
            reads.append("bytes")
        return real_bytes(self, *a, **k)

    def rt(self, *a, **k):
        if self == target:
            reads.append("text")
        return real_text(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", rb)
    monkeypatch.setattr(Path, "read_text", rt)
    fz = bt.load_frozen_m0(target)
    assert reads == ["bytes"]
    assert fz.sha256 == hashlib.sha256(real_bytes(target)).hexdigest()


# ---------------------------------------------------------------------------------------------
# U-745a fix rounds 2-3: the two LIVE ladders (choose_final order, never weak history)
# ---------------------------------------------------------------------------------------------


def _strong_index(vendor=1, scc=10, n=6):
    return F.AsOfIndex([_pb(900 + i, vendor, datetime(2026, 1, 1 + i), scc) for i in range(n)])


def _weak_index(vendor=1, codes=(10, 10, 11, 12, 13)):
    return F.AsOfIndex([_pb(800 + i, vendor, datetime(2026, 1, 1 + i), c) for i, c in enumerate(codes)])


def _row_scored(index, bid, label, pick, has_nbrs, mode="A", nbr_scc=None):
    q = _q(bid, 1, datetime(2026, 5, 1))
    sc = label if nbr_scc is None else nbr_scc
    nb = (F.NeighbourEvidence(0.5, 0.5, sc, False, bid - 1000, datetime(2026, 4, 1)),) if has_nbrs else ()
    ctx = F.build_context(index, q, mode=mode, neighbours=nb)
    b = BillRow(bid, 1, datetime(2026, 5, 1), label, 1, Decimal("10"), False)
    return bt.Scored(bt.Row(b, mode, ctx), L.Decision(scc_id=pick, prob=0.8, raw_prob=0.8, runner_up=None),
                     "cv0", {})


def test_fr3_weak_no_neighbour_row_is_blank_in_both_live_ladders():
    """Reviewer's case: weak history (top 10 at 2/5) is right, no neighbours. Live never falls
    back to weak history, so both ladders are blank: a tie, not an M0 'broke'."""
    s = _row_scored(_weak_index(), 1, 10, None, has_nbrs=False)
    assert not s.row.ctx.history.strong and s.row.ctx.history.top == 10
    assert s.row.ctx.baseline_pick == 10            # the U-698 baseline DOES fall back to weak history
    assert bt.live_off_pick(s) is None and bt.live_on_pick(s) is None
    blk = bt.mode_block([s], auto_bar=0.9, bootstrap=10, seed=1)
    pr = blk["m0_live_on_vs_off"]
    assert (pr["fixed"], pr["broke"], pr["diff_points"]) == (0, 0, 0.0)
    assert blk["m0_live_on_vs_off_weak_only"]["n"] == 1


def test_fr3_m0_no_pick_with_neighbours_falls_through_to_the_vote():
    s = _row_scored(_weak_index(), 2, 12, None, has_nbrs=True, nbr_scc=12)  # M0 picks nothing
    assert s.row.ctx.neighbours and s.row.ctx.vote_top == 12
    assert bt.live_abstain_pick(s) is None
    assert bt.live_on_pick(s) == 12 == bt.live_off_pick(s)
    m0 = _row_scored(_weak_index(), 3, 11, 11, has_nbrs=True, nbr_scc=12)    # M0 picks: replaces the vote
    assert bt.live_on_pick(m0) == 11 and bt.live_off_pick(m0) == 12


def test_fr3_strong_history_wins_in_both_ladders():
    s = _row_scored(_strong_index(), 4, 10, 11, has_nbrs=True, nbr_scc=11)   # M0 would override
    assert bt.live_on_pick(s) == 10 == bt.live_off_pick(s)
    s2 = _row_scored(_strong_index(), 5, 10, None, has_nbrs=False)
    assert bt.live_on_pick(s2) == 10 == bt.live_off_pick(s2)


def test_fr3_ladders_on_the_frozen_run(frozen_run):
    _pop, _opts, res = frozen_run
    oof = res["_artefacts"]["oof"]
    strong = [s for s in oof if s.row.ctx.history.strong]
    weak_abstain = [s for s in oof if not s.row.ctx.history.strong and bt.live_abstain_pick(s) is None]
    assert strong
    for s in strong + weak_abstain:  # the ladders differ only where M0 actually picks on a weak row
        assert bt.live_on_pick(s) == bt.live_off_pick(s)
    for s in oof:  # live_off is the baseline except on weak rows with no filtered vote
        if s.row.ctx.history.strong or s.row.ctx.vote_top is not None:
            assert bt.live_off_pick(s) == s.row.ctx.baseline_pick
        else:
            assert bt.live_off_pick(s) is None
    cv = res["variants"][bt.VARIANT_FROZEN]["cv_pdf_eval"]
    for m in "AB":
        blk = cv[m]
        wk, full = blk["m0_live_on_vs_off_weak_only"], blk["m0_live_on_vs_off"]
        assert wk["n"] == sum(1 for s in oof if s.row.mode == m and not s.row.ctx.history.strong)
        assert (full["fixed"], full["broke"]) == (wk["fixed"], wk["broke"])
        assert {"diff_points", "sign_test_p", "bootstrap_ci_points"} <= set(full)
        assert blk["m0_live_on"]["bill"] is not None and blk["m0_live_off"]["bill"] is not None
        assert blk["live_mix"]["m0_live_on"]["value"] is not None
        assert blk["live_mix"]["m0_live_off"]["value"] is not None
        assert "m0_live_composite" not in blk and "m0_live_composite_vs_baseline" not in blk


def test_fr2r2_report_prints_n_beside_each_gate_and_the_live_ladders(e2e):
    _pop, _opts, res = e2e
    rep = bt.render_report(res, "x")
    assert "| gate | verdict | n |" in rep
    cv = res["variants"][res["chosen_variant"]]["cv_pdf_eval"]
    assert f"| G2 | {res['gates']['G2']['status']} | A={cv['A']['n']} B={cv['B']['n']} |" in rep
    assert "m0_live_on_vs_off" in rep and "m0_live_composite" not in rep
