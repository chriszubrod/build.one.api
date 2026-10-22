"""Pure-logic tests for U-714 expense coding backtest."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from scripts.u714_expense_coding_backtest import (
    DEFAULT_CODING_LAG_DAYS,
    ExpenseLineRow,
    ExpensePriorVote,
    ReferenceData,
    ProjectRef,
    SubCostCodeRef,
    compute_all_metrics,
    find_project_for_address,
    find_sub_cost_code_for_hint,
    fit_calibration_from_scored,
    predict_engine,
    priors_as_of_expense_date,
    score_lines_rolling,
    segment_keys,
    vendor_dominant_scc_as_of,
    write_outputs,
)
from scripts.u653b_backtest import PriorBillVote


def _refs(
    *,
    holding: frozenset[int] | None = None,
    scc_qbo: bool = True,
) -> ReferenceData:
    return ReferenceData(
        projects=[
            ProjectRef(id=10, abbreviation="HE12", name="HE12 - 100 Main St", status="active"),
            ProjectRef(
                id=20,
                abbreviation="MR2",
                name="ZZZ - 200 Oak Rd",
                status="active",
            ),
            ProjectRef(
                id=30,
                abbreviation="AA1",
                name="AAA - 200 Oak Rd",
                status="active",
            ),
        ],
        sub_cost_codes=[
            SubCostCodeRef(
                id=1,
                number="13.01",
                name="Concrete",
                aliases=None,
                is_holding=False,
                qbo_id="q1" if scc_qbo else None,
            ),
            SubCostCodeRef(
                id=2,
                number="14.01",
                name="Framing",
                aliases=None,
                is_holding=False,
                qbo_id="q2" if scc_qbo else None,
            ),
            SubCostCodeRef(
                id=3,
                number="9.01",
                name="50% sale items",
                aliases="promo|50% off",
                is_holding=False,
                qbo_id="q3" if scc_qbo else None,
            ),
            SubCostCodeRef(
                id=99,
                number="00.02",
                name="Suspense",
                aliases=None,
                is_holding=True,
                qbo_id="q99",
            ),
        ],
        holding_ids=holding if holding is not None else frozenset({99}),
    )


def _line(
    line_id: int,
    vendor_id: int,
    expense_date: str,
    scc: int,
    project: int | None = 10,
    *,
    created: str | None = None,
    expense_id: int | None = None,
    memo: str | None = None,
    description: str | None = None,
    qbo: bool = True,
    qbo_via_mapping: bool = False,
    payment_type: str | None = "CreditCard",
) -> ExpenseLineRow:
    ed = datetime.fromisoformat(expense_date)
    cr = datetime.fromisoformat(created) if created else ed
    eid = expense_id if expense_id is not None else line_id * 100
    return ExpenseLineRow(
        line_id=line_id,
        expense_id=eid,
        vendor_id=vendor_id,
        expense_date=ed,
        created=cr,
        label_scc_id=scc,
        label_project_id=project,
        is_qbo_pulled=qbo or qbo_via_mapping,
        qbo_pulled_via_mapping_only=qbo_via_mapping and not qbo,
        payment_type=payment_type,
        memo=memo,
        description=description,
    )


def test_coding_lag_buffer_includes_and_excludes_by_expense_date():
    """10 calendar days earlier counts with buffer 7; 3 days earlier does not."""
    v = 1
    eval_date = date(2026, 6, 15)
    votes = [
        ExpensePriorVote(
            1,
            100,
            datetime(2026, 6, 5),
            datetime(2026, 9, 16),
            1,
            10,
        ),
        ExpensePriorVote(
            2,
            200,
            datetime(2026, 6, 12),
            datetime(2026, 9, 16),
            2,
            10,
        ),
    ]
    priors = priors_as_of_expense_date(
        votes,
        eval_expense_date=eval_date,
        coding_lag_days=7,
        exclude_line_id=99,
        exclude_expense_id=999,
    )
    assert [p.line_id for p in priors] == [1]

    priors_tight = priors_as_of_expense_date(
        votes,
        eval_expense_date=eval_date,
        coding_lag_days=7,
        exclude_line_id=2,
        exclude_expense_id=200,
    )
    assert len(priors_tight) == 1
    assert priors_tight[0].line_id == 1


def test_backfill_timestamps_do_not_block_expense_date_priors():
    """Earlier ExpenseDate counts even when CreatedDatetime is later (import/backfill)."""
    refs = _refs()
    v = 2
    lines = [
        _line(
            1,
            v,
            "2026-05-01T00:00:00",
            1,
            created="2026-09-16T12:00:00",
        ),
        _line(
            2,
            v,
            "2026-05-02T00:00:00",
            1,
            created="2026-09-16T12:00:00",
        ),
        _line(
            3,
            v,
            "2026-05-03T00:00:00",
            1,
            created="2026-09-16T12:00:00",
        ),
        _line(
            4,
            v,
            "2026-06-01T00:00:00",
            1,
            created="2026-01-30T08:00:00",
            memo="noise",
        ),
    ]
    scored = score_lines_rolling(lines, refs, coding_lag_days=7)
    assert scored[-1].vendor_has_history is True
    pred = scored[-1].predictions[("bill_style", False)]
    assert pred.predicted_scc_id == 1
    assert pred.prior_line_count == 3


def test_same_expense_lines_never_mutual_priors():
    v = 5
    day = "2026-06-01T00:00:00"
    lines = [
        _line(1, v, day, 1, expense_id=500),
        _line(2, v, day, 2, expense_id=500),
    ]
    refs = _refs()
    scored = score_lines_rolling(lines, refs, coding_lag_days=0)
    assert scored[0].predictions[("engine", False)].prior_line_count == 0
    assert scored[1].predictions[("engine", False)].prior_line_count == 0


def test_expense_date_priors_build_vendor_history():
    refs = _refs()
    v = 7
    lines = [
        _line(1, v, "2026-05-01T00:00:00", 1),
        _line(2, v, "2026-05-02T00:00:00", 1),
        _line(3, v, "2026-05-03T00:00:00", 1),
        _line(4, v, "2026-06-01T00:00:00", 1, memo="noise"),
    ]
    scored = score_lines_rolling(lines, refs, coding_lag_days=7)
    assert scored[-1].vendor_has_history is True
    pred = scored[-1].predictions[("bill_style", False)]
    assert pred.predicted_scc_id == 1


def test_manual_segment_reported_at_zero_when_all_qbo():
    refs = _refs()
    v = 8
    lines = [
        _line(1, v, "2026-05-01T00:00:00", 1),
        _line(2, v, "2026-05-02T00:00:00", 1),
        _line(3, v, "2026-05-03T00:00:00", 1),
        _line(4, v, "2026-06-01T00:00:00", 1, qbo=True),
    ]
    scored = score_lines_rolling(lines, refs)
    metrics = compute_all_metrics(
        scored,
        eval_since=date(2026, 6, 1),
        eval_until=date(2026, 6, 30),
        cal_since=date(2026, 5, 1),
        cal_until=date(2026, 5, 31),
    )
    manual = metrics["engine_memo_off"]["segments"]["manual"]
    assert manual["n_eval"] == 0
    assert metrics["engine_memo_off"]["segments"]["qbo_pulled"]["n_eval"] == 1


def test_both_correct_project_predicted_vs_label_none():
    refs = _refs()
    v = 50
    lines = [
        _line(1, v, "2026-05-01T00:00:00", 1, project=10),
        _line(2, v, "2026-05-02T00:00:00", 1, project=10),
        _line(3, v, "2026-05-03T00:00:00", 1, project=10),
        _line(
            4,
            v,
            "2026-06-01T00:00:00",
            1,
            project=10,
            memo="x - HE12 - 13.01",
        ),
        _line(
            5,
            v,
            "2026-06-02T00:00:00",
            1,
            project=None,
            memo="x - HE12 - 13.01",
        ),
    ]
    scored = score_lines_rolling(lines, refs)
    metrics = compute_all_metrics(
        scored,
        eval_since=date(2026, 6, 1),
        eval_until=date(2026, 6, 30),
        cal_since=date(2026, 5, 1),
        cal_until=date(2026, 5, 31),
    )
    engine_on = metrics["engine_memo_on"]["segments"]["all"]
    assert engine_on["n_eval"] == 2
    assert engine_on["both_correct"] == 0.5
    assert engine_on["project_coverage"] == 1.0
    assert engine_on["project_accuracy"] == 0.5


def test_find_project_tie_break_name_asc():
    refs = _refs()
    ranked = find_project_for_address(refs, "200 Oak Rd")
    ids = [pid for pid, _ in ranked]
    assert ids[:2] == [30, 20]


def test_find_scc_hint_percent_literal_match():
    refs = _refs()
    ranked = find_sub_cost_code_for_hint(refs, "50%")
    assert ranked and ranked[0][0] == 3


def test_find_scc_tie_break_number_asc():
    refs = _refs()
    refs2 = ReferenceData(
        projects=refs.projects,
        sub_cost_codes=[
            SubCostCodeRef(1, "1.01", "A", None, False, "q1"),
            SubCostCodeRef(2, "2.01", "B", "x|y", False, "q2"),
        ],
        holding_ids=frozenset(),
    )
    ranked = find_sub_cost_code_for_hint(refs2, "x")
    assert ranked[0][0] == 2


def test_reliability_decile_includes_n_and_mean_cal():
    refs = _refs()
    v = 200
    lines = []
    for i in range(12):
        lines.append(
            _line(
                i + 1,
                v,
                f"2026-05-{(i % 28) + 1:02d}T00:00:00",
                1 if i % 2 == 0 else 2,
                project=10,
            )
        )
    scored = score_lines_rolling(lines, refs)
    metrics = compute_all_metrics(
        scored,
        eval_since=date(2026, 5, 1),
        eval_until=date(2026, 5, 31),
        cal_since=date(2026, 5, 1),
        cal_until=date(2026, 5, 31),
    )
    rel = metrics["bill_style_memo_off"]["segments"]["all"]["reliability"]
    assert rel
    sample = rel[0]
    assert "n" in sample
    assert "mean_calibrated_confidence" in sample
    assert "observed_accuracy" in sample


def test_segment_qbo_via_mapping_only():
    line = _line(1, 1, "2026-06-01T00:00:00", 1, qbo=False, qbo_via_mapping=True)
    assert line.segment_qbo() == "qbo_pulled"
    assert line.qbo_pulled_via_mapping_only is True
    assert "qbo_pulled" in segment_keys(line, vendor_has_history=False)


def test_vendor_dominant_excludes_holding_and_requires_min_support():
    holding = frozenset({99})
    priors = [
        PriorBillVote(1, datetime(2026, 1, 1), 99, 10),
        PriorBillVote(2, datetime(2026, 1, 2), 1, 10),
        PriorBillVote(3, datetime(2026, 1, 3), 1, 10),
    ]
    assert vendor_dominant_scc_as_of(priors, holding) is None
    priors.append(PriorBillVote(4, datetime(2026, 1, 4), 1, 10))
    hist = vendor_dominant_scc_as_of(priors, holding)
    assert hist == (1, 3, 3)


def test_masked_mode_ignores_memo_project_hint():
    refs = _refs()
    line = _line(1, 5, "2026-06-01T00:00:00", 1, memo="Cardholder - HE12 - 13.01 - tail")
    with_memo = predict_engine(line, [], refs, use_memo=True)
    without = predict_engine(line, [], refs, use_memo=False)
    assert with_memo.predicted_project_id == 10
    assert with_memo.predicted_scc_id == 1
    assert without.predicted_project_id is None
    assert without.predicted_scc_id is None


def test_calibration_decile_nonempty():
    refs = _refs()
    v = 201
    lines = []
    for i in range(12):
        lines.append(
            _line(
                i + 1,
                v,
                f"2026-05-{(i % 28) + 1:02d}T00:00:00",
                1 if i % 2 == 0 else 2,
                project=10,
            )
        )
    scored = score_lines_rolling(lines, refs)
    cal = fit_calibration_from_scored(
        scored,
        suggester="bill_style",
        use_memo=False,
        since=date(2026, 5, 1),
        until=date(2026, 5, 31),
    )
    assert cal.bin_confidence
    assert all(c is not None for c in cal.bin_confidence)


def test_write_outputs_no_csv_by_default(tmp_path: Path):
    refs = _refs()
    line = _line(1, 1, "2026-06-15T00:00:00", 1)
    scored = score_lines_rolling([line], refs)
    metrics = compute_all_metrics(
        scored,
        eval_since=date(2026, 6, 1),
        eval_until=date(2026, 6, 30),
        cal_since=date(2026, 5, 1),
        cal_until=date(2026, 5, 31),
    )
    report, csv_path, json_path, att_path = write_outputs(
        out_dir=tmp_path,
        stamp="test",
        scored=scored,
        metrics=metrics,
        attachment_feasibility={"extraction_status_counts": {}},
        cal_since=date(2026, 5, 1),
        cal_until=date(2026, 5, 31),
        eval_since=date(2026, 6, 1),
        eval_until=date(2026, 6, 30),
        line_count=1,
        git_sha="deadbeef",
    )
    assert csv_path is None
    assert report.exists()
    assert json_path.exists()
    assert att_path.exists()
    report_text = report.read_text(encoding="utf-8")
    assert "manual" in report_text
    assert "QBO-pulled" in report_text


def test_write_outputs_contain_no_sensitive_text(tmp_path: Path):
    secret_memo = "PLANT_SECRET_MEMO_U714_XYZ"
    secret_desc = "PLANT_SECRET_DESC_U714_XYZ"
    secret_name = "PLANT_SECRET_PROJECT_NAME_U714"
    refs = ReferenceData(
        projects=[
            ProjectRef(
                id=99,
                abbreviation="XX",
                name=f"{secret_name} - 1 Main",
                status="active",
            ),
        ],
        sub_cost_codes=_refs().sub_cost_codes,
        holding_ids=frozenset({99}),
    )
    line = _line(
        1,
        1,
        "2026-06-15T00:00:00",
        1,
        memo=secret_memo,
        description=secret_desc,
    )
    scored = score_lines_rolling([line], refs)
    metrics = compute_all_metrics(
        scored,
        eval_since=date(2026, 6, 1),
        eval_until=date(2026, 6, 30),
        cal_since=date(2026, 5, 1),
        cal_until=date(2026, 5, 31),
    )
    att = {"extraction_status_counts": {"completed": 1}}
    report, _, json_path, att_path = write_outputs(
        out_dir=tmp_path,
        stamp="priv",
        scored=scored,
        metrics=metrics,
        attachment_feasibility=att,
        cal_since=date(2026, 5, 1),
        cal_until=date(2026, 5, 31),
        eval_since=date(2026, 6, 1),
        eval_until=date(2026, 6, 30),
        line_count=1,
        git_sha="deadbeef",
        write_lines=True,
    )
    csv_path = tmp_path / "u714_predictions_priv.csv"
    for path in (report, json_path, att_path, csv_path):
        text = path.read_text(encoding="utf-8")
        assert secret_memo not in text
        assert secret_desc not in text
        assert secret_name not in text


def test_default_coding_lag_matches_cli():
    assert DEFAULT_CODING_LAG_DAYS == 7
