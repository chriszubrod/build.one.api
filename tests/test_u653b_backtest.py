"""Pure-logic tests for U-653b backtest engine."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from scripts import u653b_backtest as u653b_mod
from scripts.u653b_backtest import (
    BillRow,
    BreakdownMetrics,
    CalibrationMap,
    ModePrediction,
    PriorBillVote,
    ScoredBill,
    _consistency_bucket,
    _count_votes,
    compute_breakdown_metrics,
    compute_mode_metrics,
    derive_bill_origin,
    dominant_scc_from_line_amounts,
    fit_calibration,
    predict_mode_a,
    priors_before,
    score_bills_rolling,
    validate_windows,
    verify_sample_against_procs,
    write_outputs,
)
from entities.coding_suggestion.business.bill_suggestion_service import _smoothed_share


def _bill(
    bill_id: int,
    vendor_id: int,
    created: str,
    scc: int,
    project: int | None = 10,
    *,
    amount: str = "100",
    mixed: bool = False,
    intake_source: str | None = None,
    source_email_message_id: int | None = None,
    creator_is_agent: bool = False,
    qbo_staging_created: datetime | None = None,
) -> BillRow:
    return BillRow(
        bill_id=bill_id,
        vendor_id=vendor_id,
        created=datetime.fromisoformat(created),
        label_scc_id=scc,
        label_project_id=project,
        total_abs_amount=Decimal(amount),
        mixed_code=mixed,
        intake_source=intake_source,
        source_email_message_id=source_email_message_id,
        creator_is_agent=creator_is_agent,
        qbo_staging_created=qbo_staging_created,
    )


def _empty_breakdown() -> BreakdownMetrics:
    return BreakdownMetrics({}, {}, "test origin note")


def test_dominant_scc_tie_breaks_on_lowest_line_id():
    scc, project, mixed = dominant_scc_from_line_amounts(
        [
            (5, 1, 10, 50.0),
            (3, 2, 20, 50.0),
        ]
    )
    assert scc == 2
    assert project == 20
    assert mixed is True


def test_dominant_scc_uses_abs_amount():
    scc, _, _ = dominant_scc_from_line_amounts([(1, 1, 10, -100.0), (2, 2, 10, 50.0)])
    assert scc == 1


def test_rolling_origin_excludes_self_and_same_timestamp():
    v = 100
    bills = [
        _bill(1, v, "2026-01-01T10:00:00", 1),
        _bill(2, v, "2026-01-01T10:00:00", 2),
        _bill(3, v, "2026-01-02T10:00:00", 1),
    ]
    scored = score_bills_rolling(bills)
    first = scored[0]
    assert first.mode_a.predicted_scc_id is None
    assert first.mode_a.prior_bill_count == 0

    second = scored[1]
    assert second.mode_a.prior_bill_count == 0
    assert second.mode_a.predicted_scc_id is None

    third = scored[2]
    assert third.mode_a.prior_bill_count == 2
    assert third.mode_a.predicted_scc_id is None


def test_min_support_three_prior_bills():
    v = 200
    bills = [
        _bill(i, v, f"2026-01-0{i}T10:00:00", 1, project=10)
        for i in range(1, 5)
    ]
    scored = score_bills_rolling(bills)
    assert scored[2].mode_a.predicted_scc_id is None
    assert scored[3].mode_a.predicted_scc_id == 1
    assert scored[3].mode_a.source == "vendor_project_history"


def test_mode_a_falls_back_to_vendor_wide():
    v = 300
    bills = []
    for i in range(3):
        bills.append(_bill(i + 1, v, f"2026-01-0{i + 1}T10:00:00", 2, project=20))
    bills.append(_bill(4, v, "2026-01-04T10:00:00", 2, project=10))
    scored = score_bills_rolling(bills)
    last = scored[-1]
    assert last.mode_a.source == "vendor_history"
    assert last.mode_a.predicted_scc_id == 2


def test_mode_b_predicts_project_then_scc():
    v = 400
    bills = [
        _bill(1, v, "2026-01-01T10:00:00", 1, project=10),
        _bill(2, v, "2026-01-02T10:00:00", 1, project=10),
        _bill(3, v, "2026-01-03T10:00:00", 1, project=10),
        _bill(4, v, "2026-01-04T10:00:00", 2, project=10),
    ]
    scored = score_bills_rolling(bills)
    pred = scored[-1].mode_b
    assert pred.predicted_project_id == 10
    assert pred.predicted_scc_id == 1


def test_scc_vote_tie_break_lowest_id():
    votes = [
        PriorBillVote(1, datetime(2026, 1, 1), 2, 10),
        PriorBillVote(2, datetime(2026, 1, 2), 1, 10),
        PriorBillVote(3, datetime(2026, 1, 3), 2, 10),
        PriorBillVote(4, datetime(2026, 1, 4), 1, 10),
        PriorBillVote(5, datetime(2026, 1, 5), 2, 10),
        PriorBillVote(6, datetime(2026, 1, 6), 1, 10),
    ]
    hist = _count_votes(votes)
    assert hist.top_count == hist.second_count == 3
    assert hist.top_scc_id == 1


def test_calibration_deciles_and_map():
    v = 500
    bills = []
    for i in range(20):
        bills.append(
            _bill(
                i + 1,
                v,
                f"2025-11-{i + 1:02d}T10:00:00",
                1 if i % 2 == 0 else 2,
                project=10,
            )
        )
    for i in range(3):
        bills.append(_bill(100 + i, v, f"2026-01-0{i + 1}T10:00:00", 1, project=10))
    for i in range(3):
        bills.append(_bill(200 + i, v, f"2026-01-0{i + 1}T10:00:00", 1, project=10))
    scored = score_bills_rolling(bills)
    cal = fit_calibration(
        scored,
        mode="A",
        since=date(2025, 10, 11),
        until=date(2026, 4, 10),
    )
    assert len(cal.bin_confidence) >= 1
    mapped = cal.calibrate(Decimal("0.5"))
    assert mapped is not None
    assert isinstance(mapped, Decimal)


def test_metrics_hand_computed_fixture():
    v = 600
    bills = [
        _bill(1, v, "2026-04-11T10:00:00", 1, amount="10"),
        _bill(2, v, "2026-04-12T10:00:00", 2, amount="90"),
    ]
    for i in range(3):
        bills.insert(
            0,
            _bill(10 + i, v, f"2026-03-0{i + 1}T10:00:00", 1, project=10),
        )
    scored = score_bills_rolling(bills)
    cal = CalibrationMap([0.0, 1.0], [Decimal("1.0")])
    metrics = compute_mode_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        calibration=cal,
    )
    assert metrics.coverage == Decimal("1")
    assert metrics.top1_bill_accuracy == Decimal("0.5")
    assert metrics.top1_dollar_accuracy == Decimal("0.1")


def test_csv_output_has_no_text_columns(tmp_path):
    v = 700
    bills = [
        _bill(1, v, "2026-03-01T10:00:00", 1),
        _bill(2, v, "2026-03-02T10:00:00", 1),
        _bill(3, v, "2026-03-03T10:00:00", 1),
        _bill(4, v, "2026-04-11T10:00:00", 1),
    ]
    scored = score_bills_rolling(bills)
    cal = CalibrationMap([0.0, 1.0], [Decimal("0.75")])
    metrics = compute_mode_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        calibration=cal,
    )
    bd = compute_breakdown_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        all_bills=bills,
    )
    report, csv_path, cal_path = write_outputs(
        out_dir=tmp_path,
        stamp="test",
        scored=scored,
        cal_a=cal,
        cal_b=cal,
        metrics_a=metrics,
        metrics_b=metrics,
        breakdown_a=bd,
        breakdown_b=bd,
        cal_since=date(2025, 10, 11),
        cal_until=date(2026, 4, 10),
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        bill_count=len(bills),
    )
    text = csv_path.read_text(encoding="utf-8")
    assert "Memo" not in text
    assert "Name" not in csv_path.read_text()
    header = text.splitlines()[0]
    assert header == (
        "BillId,VendorId,mode,predicted_scc_id,label_scc_id,"
        "raw_score,calibrated_confidence,correct"
    )
    assert report.exists()
    assert cal_path.exists()
    report_text = report.read_text(encoding="utf-8")
    assert "Vendor consistency breakdown" in report_text
    assert "Reliability (Mode B" in report_text


def test_priors_before_strict():
    votes = [
        PriorBillVote(1, datetime(2026, 1, 1, 10), 1, 10),
        PriorBillVote(2, datetime(2026, 1, 1, 10), 2, 10),
    ]
    priors = priors_before(
        votes, as_of=datetime(2026, 1, 1, 10), exclude_bill_id=99
    )
    assert len(priors) == 0


def test_r1_1_calibration_closed_bins_and_repeated_scores():
    score = _smoothed_share(9, 10)
    scored: list[ScoredBill] = []
    for i in range(10):
        label = 1 if i < 9 else 2
        bill = _bill(i + 1, 1, f"2025-11-{i + 1:02d}T12:00:00", label)
        pred = ModePrediction(
            mode="A",
            predicted_scc_id=1,
            predicted_project_id=10,
            source="vendor_history",
            raw_score=score,
            top3_scc_ids=[1],
            prior_bill_count=5,
        )
        scored.append(ScoredBill(bill=bill, mode_a=pred, mode_b=pred))
    cal = fit_calibration(
        scored,
        mode="A",
        since=date(2025, 11, 1),
        until=date(2025, 11, 30),
    )
    mapped = cal.calibrate(score)
    assert mapped is not None
    assert float(mapped) == pytest.approx(0.9, abs=0.01)
    assert cal.calibrate(Decimal("0.01")) is not None
    assert cal.calibrate(Decimal("0.99")) is not None


def test_r1_2_rejects_overlapping_windows():
    with pytest.raises(SystemExit, match="eval-since"):
        validate_windows(
            date(2025, 1, 1),
            date(2026, 10, 10),
            date(2026, 4, 11),
            date(2026, 10, 10),
        )
    with pytest.raises(SystemExit, match="reversed"):
        validate_windows(
            date(2026, 5, 1),
            date(2026, 1, 1),
            date(2026, 4, 11),
            date(2026, 10, 10),
        )


def test_r1_3_top3_same_source_as_top1():
    v = 801
    priors = []
    day = 1
    for i in range(6):
        priors.append(PriorBillVote(i + 1, datetime(2026, 1, day + i), 1, 99))
    for i in range(5):
        priors.append(PriorBillVote(10 + i, datetime(2026, 2, 1 + i), 2, 99))
    for i in range(4):
        priors.append(PriorBillVote(20 + i, datetime(2026, 2, 10 + i), 3, 99))
    for i in range(3):
        priors.append(PriorBillVote(30 + i, datetime(2026, 3, 1 + i), 4, 10))
    bill = _bill(99, v, "2026-02-01T10:00:00", 4, project=10)
    pred = predict_mode_a(bill, priors)
    assert pred.predicted_scc_id == 4
    assert pred.source == "vendor_project_history"
    assert pred.predicted_scc_id in pred.top3_scc_ids


def test_r1_4_mode_a_null_project_predicts_project_first():
    v = 802
    bills = []
    for i in range(3):
        bills.append(
            _bill(i + 1, v, f"2026-01-0{i + 1}T10:00:00", 2, project=10)
        )
    for i in range(5):
        bills.append(
            _bill(10 + i, v, f"2026-01-1{i}T10:00:00", 1, project=None)
        )
    bills.append(_bill(99, v, "2026-02-01T10:00:00", 2, project=None))
    scored = score_bills_rolling(bills)
    target = next(s for s in scored if s.bill.bill_id == 99)
    assert target.mode_a.predicted_scc_id == 2
    assert target.mode_a.source == "vendor_project_history"
    assert target.mode_a.predicted_project_id == 10


def test_r1_5_verify_aborts_on_mode_b_raw_score_mismatch(monkeypatch):
    bill = _bill(1, 1, "2026-01-01T10:00:00", 1)
    mode_a = ModePrediction(
        mode="A",
        predicted_scc_id=1,
        predicted_project_id=10,
        source="vendor_history",
        raw_score=Decimal("0.6"),
        top3_scc_ids=[1],
        prior_bill_count=3,
    )
    mem = ScoredBill(
        bill=bill,
        mode_a=mode_a,
        mode_b=MagicMock(
            predicted_scc_id=1,
            predicted_project_id=10,
            source="vendor_project_history",
            raw_score=Decimal("0.01"),
        ),
    )
    from types import SimpleNamespace

    # The live suggester agrees on Mode A and on Mode B's SCC/project/source,
    # but its Mode B raw score is 0.60 where the replay says 0.01.
    suggester = MagicMock()
    suggester.suggest.side_effect = [
        SimpleNamespace(sub_cost_code_id=1, project_id=10, source="vendor_history", raw_score=Decimal("0.6")),
        SimpleNamespace(sub_cost_code_id=1, project_id=10, source="vendor_project_history", raw_score=Decimal("0.60")),
    ]
    with pytest.raises(RuntimeError, match="Mode B mismatch"):
        verify_sample_against_procs([mem], sample_size=1, suggester=suggester)
    assert suggester.suggest.call_args_list[1].kwargs["project_id"] is None


def test_r1_6_reliability_uses_mean_calibrated_confidence():
    v = 803
    bills = [
        _bill(1, v, "2026-04-11T10:00:00", 1),
        _bill(2, v, "2026-04-12T10:00:00", 1),
    ]
    for i in range(3):
        bills.insert(0, _bill(10 + i, v, f"2026-03-0{i + 1}T10:00:00", 1))
    scored = score_bills_rolling(bills)
    cal = CalibrationMap([0.0, 1.0], [1.0])
    metrics = compute_mode_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        calibration=cal,
    )
    populated = [row for row in metrics.reliability if row[2] is not None]
    assert populated
    for _dec, _acc, mean_conf in populated:
        assert mean_conf == Decimal("1.0")


def test_r1_7_dollar_weight_uses_full_bill_total():
    v = 804
    bills = [
        _bill(1, v, "2026-04-11T10:00:00", 1, amount="1000"),
        _bill(2, v, "2026-04-12T10:00:00", 2, amount="100"),
    ]
    for i in range(3):
        bills.insert(0, _bill(10 + i, v, f"2026-03-0{i + 1}T10:00:00", 1))
    scored = score_bills_rolling(bills)
    cal = CalibrationMap([0.0, 1.0], [1.0])
    metrics = compute_mode_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        calibration=cal,
    )
    assert metrics.top1_dollar_accuracy == Decimal("1000") / Decimal("1100")


def test_r1_8_null_line_amount_treated_as_zero():
    scc, _, mixed = dominant_scc_from_line_amounts(
        [(1, 1, 10, None), (2, 2, 10, 100.0)]
    )
    assert scc == 2
    assert mixed is True


def test_r1_9_breakdown_sections_in_report(tmp_path):
    v = 805
    bills = [
        _bill(1, v, "2026-03-01T10:00:00", 1),
        _bill(2, v, "2026-03-02T10:00:00", 1),
        _bill(3, v, "2026-03-03T10:00:00", 1, creator_is_agent=True),
        _bill(4, v, "2026-04-11T10:00:00", 1),
    ]
    scored = score_bills_rolling(bills)
    cal = CalibrationMap([0.0, 1.0], [Decimal("0.9")])
    metrics_a = compute_mode_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        calibration=cal,
    )
    metrics_b = compute_mode_metrics(
        scored,
        mode="B",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        calibration=cal,
    )
    bd_a = compute_breakdown_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        all_bills=bills,
    )
    bd_b = compute_breakdown_metrics(
        scored,
        mode="B",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        all_bills=bills,
    )
    assert "person" in bd_a.origin
    report, _, _ = write_outputs(
        out_dir=tmp_path,
        stamp="bd",
        scored=scored,
        cal_a=cal,
        cal_b=cal,
        metrics_a=metrics_a,
        metrics_b=metrics_b,
        breakdown_a=bd_a,
        breakdown_b=bd_b,
        cal_since=date(2025, 10, 11),
        cal_until=date(2026, 4, 10),
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        bill_count=len(bills),
    )
    text = report.read_text(encoding="utf-8")
    assert ">=90%" in text
    assert "Origin breakdown (Mode B)" in text
    assert "Mixed-code top-1 accuracy" in text.split("Mode B")[1]


def test_r2_1_same_raw_score_maps_to_one_calibrated_value():
    score = Decimal("0.6")
    scored: list[ScoredBill] = []
    for i in range(3):
        label = 1 if i < 2 else 2
        bill = _bill(i + 1, 1, f"2025-11-0{i + 1}T12:00:00", label)
        pred = ModePrediction(
            mode="A",
            predicted_scc_id=1,
            predicted_project_id=10,
            source="vendor_history",
            raw_score=score,
            top3_scc_ids=[1],
            prior_bill_count=5,
        )
        scored.append(ScoredBill(bill=bill, mode_a=pred, mode_b=pred))
    cal = fit_calibration(
        scored,
        mode="A",
        since=date(2025, 11, 1),
        until=date(2025, 11, 30),
    )
    mapped = cal.calibrate(score)
    assert mapped is not None
    assert float(mapped) == pytest.approx(2 / 3, abs=0.01)
    assert float(mapped) != pytest.approx(1.0, abs=0.01)


def test_r2_2_rejects_calibration_window_after_evaluation(monkeypatch):
    with pytest.raises(SystemExit, match="eval-since"):
        validate_windows(
            date(2026, 5, 1),
            date(2026, 10, 10),
            date(2026, 4, 11),
            date(2026, 4, 30),
        )
    loader = MagicMock(side_effect=AssertionError("loader must not run"))
    monkeypatch.setattr(u653b_mod, "load_bills_from_db", loader)
    with pytest.raises(SystemExit):
        u653b_mod.main(
            [
                "--cal-since",
                "2026-05-01",
                "--cal-until",
                "2026-10-10",
                "--eval-since",
                "2026-04-11",
                "--eval-until",
                "2026-04-30",
                "--verify-sample",
                "0",
            ]
        )
    loader.assert_not_called()


def test_r2_9_vendor_consistency_uses_eval_cutoff_not_future_bills():
    v = 900
    bills = [
        _bill(i + 1, v, f"2026-03-0{i + 1}T10:00:00", 1, project=10)
        for i in range(4)
    ]
    bills.append(_bill(100, v, "2026-04-15T10:00:00", 1, project=10))
    for i in range(15):
        bills.append(
            _bill(
                200 + i,
                v,
                f"2026-12-{1 + (i % 28):02d}T10:00:00",
                2 + (i % 3),
                project=10,
            )
        )
    scored = score_bills_rolling(bills)
    bd = compute_breakdown_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 4, 30),
        all_bills=bills,
    )
    assert ">=90%" in bd.vendor_consistency
    assert bd.vendor_consistency[">=90%"][0] == 1


def test_r2_9_consistency_buckets_match_section_eight():
    assert _consistency_bucket(Decimal("0.91")) == ">=90%"
    assert _consistency_bucket(Decimal("0.75")) == "70-90%"
    assert _consistency_bucket(Decimal("0.55")) == "50-70%"
    assert _consistency_bucket(Decimal("0.49")) == "<50%"


def test_r2_9_origin_precedence_and_breakdown_keys():
    folder_bill = _bill(
        1,
        1,
        "2026-04-11T10:00:00",
        1,
        intake_source="bill_folder",
        source_email_message_id=99,
        creator_is_agent=True,
        qbo_staging_created=datetime(2026, 4, 11, 9, 0, 0),
    )
    assert derive_bill_origin(folder_bill) == "sharepoint_folder"

    email_bill = _bill(
        2,
        1,
        "2026-04-12T10:00:00",
        1,
        source_email_message_id=1,
    )
    assert derive_bill_origin(email_bill) == "email"

    v = 901
    bills = [
        _bill(1, v, "2026-03-01T10:00:00", 1),
        _bill(2, v, "2026-03-02T10:00:00", 1),
        _bill(3, v, "2026-03-03T10:00:00", 1),
        _bill(4, v, "2026-04-11T10:00:00", 1, intake_source="bill_folder"),
    ]
    scored = score_bills_rolling(bills)
    bd = compute_breakdown_metrics(
        scored,
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        all_bills=bills,
    )
    assert "sharepoint_folder" in bd.origin


def _scored_points(points):
    """[(raw_score, correct)] -> ScoredBill list in one November window."""
    out: list[ScoredBill] = []
    for i, (score, correct) in enumerate(points):
        bill = _bill(i + 1, 1, f"2025-11-{(i % 28) + 1:02d}T12:00:00", 1 if correct else 2)
        pred = ModePrediction(
            mode="A",
            predicted_scc_id=1,
            predicted_project_id=10,
            source="vendor_history",
            raw_score=Decimal(str(score)),
            top3_scc_ids=[1],
            prior_bill_count=5,
        )
        out.append(ScoredBill(bill=bill, mode_a=pred, mode_b=pred))
    return out


def _fit(points):
    return fit_calibration(
        _scored_points(points), mode="A", since=date(2025, 11, 1), until=date(2025, 11, 30)
    )


def test_r3_1_reviewer_fixture_bins_without_index_error():
    # Three 0.6 (2 correct, 1 incorrect), one 0.7 incorrect, sixteen 0.8 correct.
    points = [(0.6, 1), (0.6, 1), (0.6, 0), (0.7, 0)] + [(0.8, 1)] * 16
    cal = _fit(points)
    assert all(c is not None for c in cal.bin_confidence)
    assert len(cal.bin_edges) == len(cal.bin_confidence) + 1
    values = {float(cal.calibrate(Decimal("0.6"))) for _ in range(3)}
    assert len(values) == 1
    assert values.pop() != pytest.approx(1.0)
    assert float(cal.calibrate(Decimal("0.6"))) == pytest.approx(2 / 3)
    # The lone 0.7 does not fill a bin (target = ceil(20 / 10) = 2), so it
    # merges upward with the 0.8s: 16 correct of 17.
    assert cal.calibrate(Decimal("0.7")) == cal.calibrate(Decimal("0.8"))
    assert float(cal.calibrate(Decimal("0.8"))) == pytest.approx(16 / 17)


def test_r3_1_every_fitted_score_lands_in_the_bin_that_holds_it():
    import random

    rng = random.Random(653)
    for _ in range(200):
        n = rng.randint(1, 60)
        points = [(round(rng.choice([0.2, 0.35, 0.5, 0.6, 0.6, 0.7, 0.8, 0.9, 1.0]), 2), rng.randint(0, 1)) for _ in range(n)]
        cal = _fit(points)
        assert 1 <= len(cal.bin_confidence) <= 10
        # Recompute each bin's accuracy from the points the lookup sends to it.
        per_bin: dict[float, list[int]] = {}
        for score, correct in points:
            per_bin.setdefault(float(cal.calibrate(Decimal(str(score)))), []).append(correct)
        for conf, outcomes in per_bin.items():
            assert conf == pytest.approx(sum(outcomes) / len(outcomes))
        # Out-of-range scores clamp to the first / last bin.
        assert cal.calibrate(Decimal("0")) == cal.calibrate(Decimal(str(min(p[0] for p in points))))
        assert cal.calibrate(Decimal("5")) == cal.calibrate(Decimal(str(max(p[0] for p in points))))


def test_r3_9_origin_join_uses_dbo_native_qbo_identity():
    from scripts import u653b_backtest as mod

    sql = mod._LOAD_SQL
    assert "BillBill" not in sql.replace("qbo.BillBill was retired", "")
    assert "qb.[QboId] = b.[QboId]" in sql
    assert "qb.[RealmId] = b.[RealmId]" in sql
    assert not hasattr(mod, "_LOAD_SQL_NO_QBO")
    assert not hasattr(mod, "_LOAD_SQL_MINIMAL")


def test_r3_9_person_only_provenance_is_not_reported_unavailable():
    v = 902
    bills = [
        _bill(1, v, "2026-03-01T10:00:00", 1),
        _bill(2, v, "2026-03-02T10:00:00", 1),
        _bill(3, v, "2026-03-03T10:00:00", 1),
        _bill(4, v, "2026-04-11T10:00:00", 1),
    ]
    bd = compute_breakdown_metrics(
        score_bills_rolling(bills),
        mode="A",
        eval_since=date(2026, 4, 11),
        eval_until=date(2026, 10, 10),
        all_bills=bills,
    )
    assert set(bd.origin) == {"person"}
    assert "unavailable" not in bd.origin_note


def test_p2_every_metric_table_has_a_separator_row(tmp_path):
    """Pass 2: the Mode B metric table had no |---| row, so it rendered as text."""
    bills = [_bill(i, 1, f"2026-04-{10 + i:02d}T10:00:00", 1) for i in range(1, 6)]
    scored = score_bills_rolling(bills)
    report = _write_report_for_test(tmp_path, scored, bills)
    lines = report.read_text(encoding="utf-8").splitlines()
    headers = [i for i, line in enumerate(lines) if line.startswith("| Metric | Value |")]
    assert len(headers) == 2
    for i in headers:
        assert lines[i + 1].startswith("|--------|")


def _write_report_for_test(tmp_path, scored, bills):
    window = dict(eval_since=date(2026, 4, 11), eval_until=date(2026, 10, 10))
    cal = fit_calibration(scored, mode="A", since=date(2025, 10, 11), until=date(2026, 4, 10))
    metrics = {
        m: compute_mode_metrics(scored, mode=m, calibration=cal, **window) for m in ("A", "B")
    }
    breakdown = {
        m: compute_breakdown_metrics(scored, mode=m, all_bills=bills, **window) for m in ("A", "B")
    }
    report, _, _ = write_outputs(
        out_dir=tmp_path,
        stamp="sep",
        scored=scored,
        cal_a=cal,
        cal_b=cal,
        metrics_a=metrics["A"],
        metrics_b=metrics["B"],
        breakdown_a=breakdown["A"],
        breakdown_b=breakdown["B"],
        cal_since=date(2025, 10, 11),
        cal_until=date(2026, 4, 10),
        bill_count=len(bills),
        **window,
    )
    return report
