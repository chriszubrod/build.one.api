"""U-698 phase 2 — decision model + backtest harness (fakes only; no DB, no LLM, no blob)."""
from __future__ import annotations

import csv
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


def _fake_neighbours(pop, *, idf_since, idf_until, idf_before, upto, coded_unknown=None, top_k=10):
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
