"""Pure-logic tests for U-691 similar-bills backtest."""
from __future__ import annotations

import importlib
import random
import string
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from scripts import u691_similar_bills_backtest as u691
from scripts.u653b_backtest import BillRow, ModePrediction, validate_windows
from scripts.u654a_text_backtest import assert_outputs_privacy_safe


def _bill(
    bill_id: int,
    vendor_id: int,
    created: str,
    scc: int,
    project: int | None = 10,
    *,
    amount: str = "100",
    text: str = "",
    memo: str = "",
    line_count: int = 1,
) -> u691.EnrichedBill:
    br = BillRow(
        bill_id=bill_id,
        vendor_id=vendor_id,
        created=datetime.fromisoformat(created),
        label_scc_id=scc,
        label_project_id=project,
        total_abs_amount=Decimal(amount),
        mixed_code=False,
    )
    return u691.EnrichedBill(
        bill=br,
        memo=memo,
        line_descriptions=text,
        line_count=line_count,
    )


def test_tokenizer_stops_punctuation_digits_dedupe():
    toks = u691.tokenize_bill_text("Invoice #123 — PLUMBING, plumbing!!! the and a")
    assert "plumbing" in toks
    assert "invoice" not in toks
    assert "the" not in toks
    assert "123" not in toks
    assert len([t for t in toks if t == "plumbing"]) == 1


def test_idf_rarer_token_weighs_more():
    bills = [
        _bill(1, 1, "2025-11-01T10:00:00", 1, text="alpha beta"),
        _bill(2, 1, "2025-11-02T10:00:00", 1, text="alpha gamma"),
        _bill(3, 1, "2025-11-03T10:00:00", 1, text="alpha delta"),
    ]
    idf = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10)
    )
    assert idf["alpha"] < idf["beta"]


def test_similarity_identical_gt_partial_gt_none():
    idf = {"wire": 1.0, "pipe": 0.5, "hvac": 0.5}
    a = _bill(1, 1, "2026-01-01T10:00:00", 1, text="wire pipe", amount="100")
    b = _bill(2, 1, "2026-01-02T10:00:00", 1, text="wire pipe", amount="100")
    c = _bill(3, 1, "2026-01-03T10:00:00", 1, text="wire hvac", amount="100")
    d = _bill(4, 1, "2026-01-04T10:00:00", 1, text="roof tile", amount="100")
    full = u691.text_similarity(a, b, idf, band_pct=0.25, cap_ratio=4.0)
    part = u691.text_similarity(a, c, idf, band_pct=0.25, cap_ratio=4.0)
    none = u691.text_similarity(a, d, idf, band_pct=0.25, cap_ratio=4.0)
    assert full > part > none
    assert none == 0.0


def test_amount_band_reduces_similarity():
    idf = {"wire": 1.0}
    a = _bill(1, 1, "2026-01-01T10:00:00", 1, text="wire", amount="100")
    b_near = _bill(2, 1, "2026-01-02T10:00:00", 1, text="wire", amount="110")
    b_far = _bill(3, 1, "2026-01-03T10:00:00", 1, text="wire", amount="500")
    near = u691.text_similarity(a, b_near, idf, band_pct=0.25, cap_ratio=4.0)
    far = u691.text_similarity(a, b_far, idf, band_pct=0.25, cap_ratio=4.0)
    assert near > far
    assert near == pytest.approx(1.0, abs=0.01)


def test_no_leakage_self_and_same_day_and_future():
    bills = [
        _bill(1, 1, "2026-01-01T10:00:00", 1, text="alpha wire"),
        _bill(2, 1, "2026-01-01T10:00:00", 2, text="alpha wire"),
        _bill(3, 1, "2026-01-02T10:00:00", 1, text="alpha wire"),
    ]
    idf = {"alpha": 1.0, "wire": 0.8}
    scored = u691.score_all_rolling(bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0)
    first = scored[0]
    assert first.similar["V_same_vendor"].top_k_used == 0
    second = scored[1]
    assert second.similar["V_same_vendor"].top_k_used == 0
    third = scored[2]
    assert third.similar["V_same_vendor"].top_k_used == 2


def test_vote_weighted_and_tie_breaks_on_scc_id():
    query = _bill(10, 1, "2026-03-01T10:00:00", 5, text="shared token")
    priors = [
        _bill(1, 1, "2026-01-01T10:00:00", 2, text="shared token"),
        _bill(2, 1, "2026-01-02T10:00:00", 3, text="shared token"),
    ]
    idf = {"shared": 1.0, "token": 1.0}
    index: dict[str, set[int]] = {"shared": {1, 2}, "token": {1, 2}}
    by_id = {p.bill.bill_id: p for p in priors}
    pred = u691.predict_similarity(
        "V_same_vendor",
        query,
        priors,
        idf,
        top_k=10,
        band_pct=0.25,
        cap_ratio=4.0,
        token_index=index,
        prior_by_id=by_id,
    )
    assert pred.predicted_scc_id == 2
    assert pred.raw_score == Decimal("0.5")


def test_variant_pools():
    query = _bill(
        10, 1, "2026-03-01T10:00:00", 1, project=10, text="roof shingle"
    )
    priors = [
        _bill(1, 1, "2026-01-01T10:00:00", 1, project=10, text="roof shingle"),
        _bill(2, 2, "2026-01-02T10:00:00", 2, project=10, text="roof shingle"),
        _bill(3, 3, "2026-01-03T10:00:00", 3, project=99, text="roof shingle"),
    ]
    idf = {"roof": 1.0, "shingle": 0.9}
    index: dict[str, set[int]] = {}
    by_id = {p.bill.bill_id: p for p in priors}
    for p in priors:
        for t in u691.bill_tokens(p):
            index.setdefault(t, set()).add(p.bill.bill_id)

    same = u691.predict_similarity(
        "V_same_vendor",
        query,
        priors,
        idf,
        top_k=10,
        band_pct=0.25,
        cap_ratio=4.0,
        token_index=index,
        prior_by_id=by_id,
    )
    any_v = u691.predict_similarity(
        "V_any_vendor",
        query,
        priors,
        idf,
        top_k=10,
        band_pct=0.25,
        cap_ratio=4.0,
        token_index=index,
        prior_by_id=by_id,
    )
    proj = u691.predict_similarity(
        "V_any_vendor_same_project",
        query,
        priors,
        idf,
        top_k=10,
        band_pct=0.25,
        cap_ratio=4.0,
        token_index=index,
        prior_by_id=by_id,
    )
    assert same.top_k_used == 1
    assert any_v.top_k_used == 3
    assert proj.top_k_used == 2


def test_coverage_uses_top_k_not_token_overlap():
    """M3: token overlap without positive similarity does not count as coverage."""
    row = u691.FullyScoredBill(
        enriched=_bill(1, 1, "2026-04-11T10:00:00", 1, text="alpha"),
        history_a=ModePrediction("A", None, None, "none", None, [], 0),
        history_b=ModePrediction("B", None, None, "none", None, [], 0),
        similar={
            "V_same_vendor": u691.SimilarPrediction(None, None, 0, 0),
            "V_any_vendor": u691.SimilarPrediction(None, None, 0, 0),
            "V_any_vendor_same_project": u691.SimilarPrediction(
                1, Decimal("0.9"), 1, 1
            ),
        },
    )
    fill = u691.compute_fill_in_metrics([row])
    assert fill.coverage_with_neighbour["V_any_vendor"] == Decimal("0")
    assert fill.coverage_with_neighbour["V_any_vendor_same_project"] == Decimal("1")


def test_m1_common_set_combined_beats_history_top1_over_all():
    """History abstains on eval bill; similarity answers correctly → combined wins on common set."""
    cal = [
        _bill(1, 1, "2025-11-01T10:00:00", 1, text="seed alpha"),
        _bill(2, 1, "2025-12-01T10:00:00", 1, text="seed alpha"),
    ]
    eval_bill = _bill(
        3, 1, "2026-04-15T10:00:00", 42, text="uniquewidget alpha", amount="100"
    )
    prior_match = _bill(
        4, 2, "2026-01-15T10:00:00", 42, text="uniquewidget alpha", amount="100"
    )
    bills = cal + [prior_match, eval_bill]
    idf = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10)
    )
    scored = u691.score_all_rolling(bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0)
    eval_rows = u691.eval_window_rows(
        scored, eval_since=date(2026, 4, 11), eval_until=date(2026, 10, 10)
    )
    assert len(eval_rows) == 1
    row = eval_rows[0]
    assert u691.history_abstained(row.history_a)
    assert row.similar["V_any_vendor"].predicted_scc_id == 42

    hist_m = u691.top1_metrics(eval_rows, lambda r: r.history_a)
    comb_m = u691.top1_metrics(
        eval_rows,
        lambda r: u691._PredView(
            u691.combined_prediction(r, best_variant="V_any_vendor")
        ),
    )
    assert hist_m["bill"] == Decimal("0")
    assert comb_m["bill"] == Decimal("1")
    assert comb_m["bill"] > hist_m["bill"]


def _calibration_selection_fixture() -> tuple[list[u691.EnrichedBill], str]:
    """
    Non-degenerate calibration selection: anchorzz overlaps priors; per-bill
    suffix keeps IDF non-zero. V_same_vendor wins calibration; eval favours V_any_vendor.
    """
    cal_bills = [
        _bill(1, 1, "2025-11-01T10:00:00", 10, project=10, text="voneonly calone"),
        _bill(2, 1, "2025-12-01T10:00:00", 10, project=10, text="voneonly caltwo"),
        _bill(3, 1, "2026-01-01T10:00:00", 10, project=10, text="voneonly calthree"),
        _bill(4, 1, "2026-02-01T10:00:00", 10, project=10, text="voneonly calfour"),
        _bill(45, 2, "2026-02-15T10:00:00", 20, project=20, text="voneonly calfourb"),
        _bill(
            5,
            2,
            "2026-03-01T10:00:00",
            20,
            project=20,
            text="vtwoonly voneonly calfive",
        ),
        _bill(8, 2, "2026-04-01T10:00:00", 20, project=20, text="vtwoonly calsix"),
    ]
    eval_bills = [
        _bill(6, 99, "2026-04-15T10:00:00", 10, project=99, text="voneonly evalsix"),
        _bill(7, 99, "2026-05-01T10:00:00", 10, project=99, text="voneonly evalseven"),
    ]
    return cal_bills + eval_bills, "V_same_vendor"


def test_m2_variant_selected_on_calibration_not_eval():
    """Hand-computed calibration winner ≠ eval winner; pick follows calibration."""
    bills, expected_cal_winner = _calibration_selection_fixture()
    idf_sel = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10)
    )
    sel = u691.pick_variants_on_calibration(
        bills,
        cal_since=date(2025, 10, 11),
        cal_until=date(2026, 4, 10),
        idf_for_selection=idf_sel,
        top_k=10,
        band_pct=0.25,
        cap_ratio=4.0,
    )
    assert sel.best_variant == expected_cal_winner
    cal_metrics = sel.calibration_metrics
    assert cal_metrics["V_same_vendor"]["bill"] == pytest.approx(5 / 7, rel=1e-3)
    assert cal_metrics["V_any_vendor"]["bill"] == pytest.approx(4 / 7, rel=1e-3)

    idf_eval = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10)
    )
    scored = u691.score_all_rolling(
        bills, idf_eval, top_k=10, band_pct=0.25, cap_ratio=4.0
    )
    eval_rows = u691.eval_window_rows(
        scored, eval_since=date(2026, 4, 11), eval_until=date(2026, 10, 10)
    )
    eval_best = max(
        u691.VARIANTS,
        key=lambda v: u691.top1_metrics(
            eval_rows, lambda r, variant=v: r.similar[variant]
        )["bill"]
        or Decimal("-1"),
    )
    assert eval_best == "V_any_vendor"
    assert eval_best != sel.best_variant


def test_main_run_passes_calibration_selection_idf(monkeypatch, tmp_path):
    bills, expected = _calibration_selection_fixture()
    captured: dict[str, object] = {}

    def fake_load():
        return bills

    _real_pick = u691.pick_variants_on_calibration

    def fake_pick(enriched, *, cal_since, cal_until, idf_for_selection, **kwargs):
        captured["cal_since"] = cal_since
        captured["cal_until"] = cal_until
        captured["idf_for_selection"] = idf_for_selection
        return _real_pick(
            enriched,
            cal_since=cal_since,
            cal_until=cal_until,
            idf_for_selection=idf_for_selection,
            **kwargs,
        )

    def fake_write(**kwargs):
        captured["summary"] = kwargs["summary"]
        captured["scored"] = kwargs["scored"]
        out = kwargs["out_dir"]
        out.mkdir(parents=True, exist_ok=True)
        p = out / "stub.json"
        p.write_text("{}", encoding="utf-8")
        return p, p, p

    monkeypatch.setattr(u691, "load_enriched_bills_from_db", fake_load)
    monkeypatch.setattr(u691, "pick_variants_on_calibration", fake_pick)
    monkeypatch.setattr(u691, "write_outputs", fake_write)

    assert u691.main(["--run", "--out-dir", str(tmp_path / "out")]) == 0
    assert captured["cal_since"] == date(2025, 10, 11)
    assert captured["cal_until"] == date(2026, 4, 10)
    expected_idf = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10)
    )
    assert captured["idf_for_selection"] == expected_idf
    summary = captured["summary"]
    assert summary["best_variant"] == expected
    scored = captured["scored"]
    eval_rows = u691.eval_window_rows(
        scored, eval_since=date(2026, 4, 11), eval_until=date(2026, 10, 10)
    )
    comb = u691.top1_metrics(
        eval_rows,
        lambda r: u691._PredView(
            u691.combined_prediction(r, best_variant=summary["best_variant"])
        ),
    )
    direct = u691.top1_metrics(
        eval_rows,
        lambda r: u691._PredView(
            u691.combined_prediction(r, best_variant="V_any_vendor")
        ),
    )
    assert comb["bill"] != direct["bill"]


def test_enriched_load_sql_anchors_unique():
    u691._assert_enriched_sql_anchors()
    sql = u691._enriched_load_sql()
    assert "LineDescriptions" in sql
    assert "LineText AS" in sql


def test_breakdown_uses_top1_over_all_not_covered_only():
    row_abstain = u691.FullyScoredBill(
        enriched=_bill(1, 1, "2026-04-11T10:00:00", 1, line_count=1),
        history_a=ModePrediction("A", None, None, "none", None, [], 0),
        history_b=ModePrediction("B", None, None, "none", None, [], 0),
        similar={
            "V_same_vendor": u691.SimilarPrediction(None, None, 0, 0),
            "V_any_vendor": u691.SimilarPrediction(None, None, 0, 0),
            "V_any_vendor_same_project": u691.SimilarPrediction(
                None, None, 0, 0
            ),
        },
    )
    row_ok = u691.FullyScoredBill(
        enriched=_bill(2, 1, "2026-04-12T10:00:00", 1, line_count=1),
        history_a=ModePrediction("A", None, None, "none", None, [], 0),
        history_b=ModePrediction("B", None, None, "none", None, [], 0),
        similar={
            v: u691.SimilarPrediction(1, Decimal("0.9"), 1, 1) for v in u691.VARIANTS
        },
    )
    bd = u691.breakdown_by_line_count([row_abstain, row_ok], "V_same_vendor")
    assert bd["1"]["n"] == 2
    assert bd["1"]["bill"] == pytest.approx(0.5)
    assert bd["1"]["coverage_bill"] == pytest.approx(0.5)
    assert bd["1"]["covered_bill"] == pytest.approx(1.0)


def test_vendor_consistency_breakdown_keeps_no_prior_bills():
    def scored(eb):
        return u691.FullyScoredBill(
            enriched=eb,
            history_a=ModePrediction("A", None, None, "none", None, [], 0),
            history_b=ModePrediction("B", None, None, "none", None, [], 0),
            similar={v: u691.SimilarPrediction(1, Decimal("0.9"), 1, 1) for v in u691.VARIANTS},
        )

    prior = _bill(1, 5, "2026-01-02T10:00:00", 1)
    later_same_vendor = _bill(2, 5, "2026-04-12T10:00:00", 1)
    cold_start = _bill(3, 9, "2026-04-12T11:00:00", 1)  # vendor 9 has no earlier bill
    rows = [scored(later_same_vendor), scored(cold_start)]
    bd = u691.breakdown_by_vendor_consistency(rows, [prior, later_same_vendor, cold_start], "V_any_vendor")
    assert bd["no_prior"]["n"] == 1
    assert sum(b["n"] for b in bd.values()) == len(rows)


def _zipf_bills(n: int, seed: int = 1) -> list[u691.EnrichedBill]:
    r = random.Random(seed)
    vocab = 3000
    common = ("labor", "materials", "supply", "service", "lumber", "delivery")
    words = [
        "".join(r.choice(string.ascii_lowercase) for _ in range(r.randint(4, 9)))
        for _ in range(vocab)
    ]
    weights = [1 / (i + 1) ** 1.05 for i in range(vocab)]
    base = datetime(2024, 1, 1)
    out: list[u691.EnrichedBill] = []
    for i in range(n):
        toks = r.choices(words, weights=weights, k=r.randint(4, 12))
        if r.random() < 0.5:
            toks.append(r.choice(common))
        out.append(
            _bill(
                i + 1,
                r.randint(1, 400),
                (base + timedelta(minutes=37 * i)).isoformat(),
                r.randint(1, 300),
                project=r.randint(1, 130),
                text=" ".join(toks),
                amount=str(r.randint(20, 20000)),
            )
        )
    return out


def _parity_bills(n: int, seed: int = 7) -> list[u691.EnrichedBill]:
    """Dense overlap on purpose: a small vocabulary, few vendors / projects, repeated texts
    and amounts — so pools exceed top_k and similarity ties occur at the k-th slot."""
    r = random.Random(seed)
    vocab = [f"w{chr(97 + a)}{chr(97 + b)}" for a in range(6) for b in range(6)]
    texts = [" ".join(r.sample(vocab, r.randint(3, 6))) for _ in range(40)]
    amounts = ["100", "250", "1000", "1200", "5000"]
    base = datetime(2026, 1, 1)
    return [
        _bill(
            i + 1,
            r.randint(1, 12),
            (base + timedelta(minutes=11 * i)).isoformat(),
            r.randint(1, 25),
            project=r.randint(1, 10),
            text=r.choice(texts),
            amount=r.choice(amounts),
        )
        for i in range(n)
    ]


def _parity_idf(bills: list[u691.EnrichedBill]) -> dict[str, float]:
    days = [b.bill.created.date() for b in bills]
    idf = u691.compute_idf_from_corpus(bills, since=min(days), until=max(days))
    assert idf, "parity fixture IDF must be non-empty or every similarity is 0"
    return idf


def test_predict_optimized_matches_reference():
    bills = _parity_bills(500)
    idf = _parity_idf(bills)
    ordered = sorted(bills, key=lambda eb: (eb.bill.created, eb.bill.bill_id))
    prior_profiles: list[u691.BillTokenProfile] = []
    prior_by_id: dict[int, u691.BillTokenProfile] = {}
    token_index: dict[str, set[int]] = {}
    compared = predicted = big_pool = 0
    for n, eb in enumerate(ordered):
        qprof = u691.token_profile(eb, idf)
        if n >= 400:  # compare the tail, where pools are large
            kwargs = dict(top_k=10, band_pct=0.25, cap_ratio=4.0,
                          token_index=token_index, prior_by_id=prior_by_id)
            opt = u691.predict_all_variants(qprof, prior_profiles, idf, **kwargs)
            ref = u691.predict_all_variants_reference(qprof, prior_profiles, idf, **kwargs)
            assert opt == ref
            compared += 1
            for sp in opt.values():
                if sp.predicted_scc_id is not None:
                    predicted += 1
                if sp.top_k_used == 10:
                    big_pool += 1
        prior_profiles.append(qprof)
        prior_by_id[eb.bill.bill_id] = qprof
        for tok in qprof.tokens:
            token_index.setdefault(tok, set()).add(eb.bill.bill_id)
    assert compared == 100
    # The comparison must exercise real predictions and full top-k pools, not abstentions.
    assert predicted >= compared  # on average at least one variant predicts per bill
    assert big_pool >= compared   # top-k is saturated often, so the heap path runs


def test_realistic_performance_zipf():
    """Guards against a gross slowdown only (runtime is quadratic in bill count; see README).
    Correctness of the optimized path is pinned by test_predict_optimized_matches_reference."""
    bills = _zipf_bills(1500, seed=42)
    idf = _parity_idf(bills)
    t0 = time.perf_counter()
    u691.score_all_rolling(bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0)
    assert time.perf_counter() - t0 < 15.0


def test_combined_policy_strong_history_wins():
    row = u691.FullyScoredBill(
        enriched=_bill(1, 1, "2026-04-11T10:00:00", 1),
        history_a=ModePrediction(
            "A", 99, 10, "vendor_history", Decimal("0.85"), [99], 5
        ),
        history_b=ModePrediction("B", None, None, "none", None, [], 0),
        similar={
            v: u691.SimilarPrediction(1, Decimal("0.9"), 3, 3) for v in u691.VARIANTS
        },
    )
    assert u691.combined_prediction(row, best_variant="V_any_vendor") == 99


def test_combined_policy_weak_uses_similarity():
    row = u691.FullyScoredBill(
        enriched=_bill(1, 1, "2026-04-11T10:00:00", 1),
        history_a=ModePrediction(
            "A", 99, 10, "vendor_history", Decimal("0.40"), [99], 5
        ),
        history_b=ModePrediction("B", None, None, "none", None, [], 0),
        similar={
            "V_same_vendor": u691.SimilarPrediction(42, Decimal("0.7"), 2, 2),
            "V_any_vendor": u691.SimilarPrediction(None, None, 0, 0),
            "V_any_vendor_same_project": u691.SimilarPrediction(None, None, 0, 0),
        },
    )
    assert u691.combined_prediction(row, best_variant="V_same_vendor") == 42


def test_fill_in_and_trigger_metrics():
    bills = [
        _bill(1, 1, "2026-03-01T10:00:00", 1, text="alpha"),
        _bill(2, 1, "2026-03-02T10:00:00", 1, text="alpha"),
        _bill(3, 1, "2026-04-11T10:00:00", 2, text="alpha"),
    ]
    idf = u691.compute_idf_from_corpus(
        bills, since=date(2025, 10, 11), until=date(2026, 4, 10)
    )
    scored = u691.score_all_rolling(bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0)
    eval_rows = u691.eval_window_rows(
        scored, eval_since=date(2026, 4, 11), eval_until=date(2026, 10, 10)
    )
    fill = u691.compute_fill_in_metrics(eval_rows)
    assert fill.abstain_count >= 1
    trig = u691.compute_trigger_rates(eval_rows)
    assert all(v in trig for v in u691.VARIANTS)


def test_privacy_guard_on_outputs(tmp_path):
    bills = [
        _bill(1, 1, "2026-03-01T10:00:00", 1),
        _bill(2, 1, "2026-03-02T10:00:00", 1),
        _bill(3, 1, "2026-03-03T10:00:00", 1),
        _bill(4, 1, "2026-04-11T10:00:00", 1),
    ]
    idf = {"x": 1.0}
    scored = u691.score_all_rolling(bills, idf, top_k=5, band_pct=0.25, cap_ratio=4.0)
    sel = u691.CalibrationSelection(
        best_variant="V_same_vendor",
        best_variant_mode_b="V_same_vendor",
        calibration_metrics={},
        warnings=(),
    )
    summary = u691.build_summary(
        scored,
        cal_since=date(2025, 10, 11),
        cal_until=date(2026, 4, 10),
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        best_variant="V_same_vendor",
        best_variant_mode_b="V_same_vendor",
        calibration_selection=sel,
        runtime_seconds=0.01,
        bill_count=4,
        constants={"top_k": 5, "amount_band_pct": 0.25, "amount_cap_ratio": 4.0},
    )
    report, csv_path, json_path = u691.write_outputs(
        out_dir=tmp_path,
        stamp="test",
        scored=scored,
        summary=summary,
    )
    assert_outputs_privacy_safe(csv_path)
    assert_outputs_privacy_safe(json_path)
    assert_outputs_privacy_safe(report)
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("BillId,memo\n1,secret vendor memo\n", encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden"):
        assert_outputs_privacy_safe(bad_csv)


def test_validate_windows_rejects_overlap():
    with pytest.raises(SystemExit, match="eval-since"):
        validate_windows(
            date(2025, 1, 1),
            date(2026, 10, 10),
            date(2026, 4, 11),
            date(2026, 10, 10),
        )


def test_dry_run_does_not_score(monkeypatch):
    enriched = [_bill(1, 1, "2026-01-01T10:00:00", 1)]
    monkeypatch.setattr(u691, "load_enriched_bills_from_db", lambda: enriched)
    called = {"score": False}

    def _score(*_a, **_k):
        called["score"] = True
        return []

    monkeypatch.setattr(u691, "score_all_rolling", _score)
    assert u691.main([]) == 0
    assert called["score"] is False


def test_allow_later_bill_red():
    bills = [
        _bill(0, 1, "2025-11-01T10:00:00", 1, text="epsilon"),
        _bill(1, 1, "2026-01-01T10:00:00", 1, text="foxtrot alpha"),
        _bill(2, 1, "2026-01-01T10:00:00", 2, text="foxtrot beta"),
    ]
    idf = {"foxtrot": 1.0, "alpha": 0.5, "beta": 0.5, "epsilon": 0.3}
    orig = u691.is_prior_eligible

    def loose(prior, query, *, as_of):
        pb = u691._as_bill_row(prior)
        qb = u691._as_bill_row(query)
        if pb.bill_id == qb.bill_id:
            return False
        return pb.created <= as_of

    scored_strict = u691.score_all_rolling(
        bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0
    )
    assert scored_strict[2].similar["V_same_vendor"].top_k_used == 0

    u691.is_prior_eligible = loose  # type: ignore[assignment]
    try:
        scored_loose = u691.score_all_rolling(
            bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0
        )
        assert scored_loose[2].similar["V_same_vendor"].top_k_used >= 1
    finally:
        u691.is_prior_eligible = orig  # type: ignore[assignment]


def test_drop_amount_band_red():
    bills = [
        _bill(0, 1, "2025-11-01T10:00:00", 1, text="epsilon"),
        _bill(
            1,
            1,
            "2026-01-01T10:00:00",
            1,
            text="foxtrot anchor",
            amount="100",
        ),
        _bill(
            2,
            1,
            "2026-02-01T10:00:00",
            1,
            text="foxtrot anchor",
            amount="5000",
        ),
    ]
    idf = {"foxtrot": 1.0, "anchor": 0.9, "epsilon": 0.3}
    with_band = u691.score_all_rolling(
        bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0
    )
    assert with_band[2].similar["V_same_vendor"].top_k_used == 0

    def jaccard_only(q, p, _idf, *, band_pct, cap_ratio):
        if isinstance(q, u691.BillTokenProfile):
            qt, qw, pt, pw = q.tokens, q.idf_weight_sum, p.tokens, p.idf_weight_sum
        else:
            qt = u691.bill_tokens(q)
            qw = sum(_idf.get(t, 0.0) for t in qt)
            pt = u691.bill_tokens(p)
            pw = sum(_idf.get(t, 0.0) for t in pt)
        return u691.idf_weighted_jaccard(qt, qw, pt, pw, _idf)

    orig = u691.text_similarity_profiles
    u691.text_similarity_profiles = jaccard_only  # type: ignore[assignment]
    try:
        no_band = u691.score_all_rolling(
            bills, idf, top_k=10, band_pct=0.25, cap_ratio=4.0
        )
        assert no_band[2].similar["V_same_vendor"].top_k_used >= 1
        assert no_band[2].similar["V_same_vendor"].predicted_scc_id == 1
    finally:
        u691.text_similarity_profiles = orig  # type: ignore[assignment]


def test_drop_self_exclusion_red():
    cal = _bill(0, 1, "2025-11-01T10:00:00", 1, text="foxtrot")
    query = _bill(1, 1, "2026-02-01T10:00:00", 2, text="foxtrot zeta")
    prior_same_id = _bill(1, 1, "2026-01-01T10:00:00", 1, text="foxtrot zeta")
    priors = [prior_same_id]
    idf = {"foxtrot": 1.0, "zeta": 0.8}
    index: dict[str, set[int]] = {}
    for p in priors:
        for t in u691.bill_tokens(p):
            index.setdefault(t, set()).add(p.bill.bill_id)
    by_id = {p.bill.bill_id: p for p in priors}

    strict = u691.predict_similarity(
        "V_same_vendor",
        query,
        priors,
        idf,
        top_k=10,
        band_pct=0.25,
        cap_ratio=4.0,
        token_index=index,
        prior_by_id=by_id,
    )
    assert strict.top_k_used == 0

    orig = u691.is_prior_eligible

    def allow_self(prior, query, *, as_of):
        return u691._as_bill_row(prior).created < as_of

    u691.is_prior_eligible = allow_self  # type: ignore[assignment]
    try:
        loose = u691.predict_similarity(
            "V_same_vendor",
            query,
            priors,
            idf,
            top_k=10,
            band_pct=0.25,
            cap_ratio=4.0,
            token_index=index,
            prior_by_id=by_id,
        )
        assert loose.top_k_used >= 1
        assert loose.predicted_scc_id == 1
    finally:
        u691.is_prior_eligible = orig  # type: ignore[assignment]


