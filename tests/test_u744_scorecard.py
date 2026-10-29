"""U-744: pure live bill-coding scorecard (no I/O)."""
from __future__ import annotations

import math
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from entities.bill_intake_prediction.business import coding_scorecard as sc

FIN = datetime(2026, 10, 29, 12, 0, 0)


def _row(**kw):
    base = {
        "model_version": "u701b-text-v2",
        "coding_source": "primary",
        "coding_mode": "A",
        "intake_source": "pdf_drop",
        "pred_sub_cost_code_id": 10,
        "pred_project_id": 1,
        "final_sub_cost_code_id": 10,
        "final_project_id": 1,
        "finalized_datetime": FIN,
        "created_datetime": datetime(2026, 10, 29, 1, 0, 0),
    }
    base.update(kw)
    return base


def test_right_wrong_abstain_and_both():
    rows = [
        _row(),  # right / right
        _row(pred_sub_cost_code_id=11),  # code wrong, project right
        _row(pred_sub_cost_code_id=None),  # code abstain
        _row(pred_project_id=None),  # project abstain, both abstain
    ]
    card = sc.build_coding_scorecard(rows)
    code, project, both = (card["overall"][k] for k in ("code", "project", "both"))
    assert (code["n"], code["right"], code["abstain"]) == (4, 2, 1)
    assert (project["n"], project["right"], project["abstain"]) == (4, 3, 1)
    assert (both["n"], both["right"], both["abstain"]) == (4, 1, 2)
    assert code["rate_pct"] == 50.0


def test_null_prediction_counts_wrong_not_dropped():
    card = sc.build_coding_scorecard([_row(pred_sub_cost_code_id=None)])
    code = card["overall"]["code"]
    assert code["n"] == 1 and code["right"] == 0 and code["abstain"] == 1
    assert code["rate_pct"] == 0.0


def test_unknown_final_is_not_scored():
    card = sc.build_coding_scorecard([_row(final_sub_cost_code_id=None)])
    assert card["overall"]["code"]["n"] == 0
    assert card["overall"]["code"]["rate_pct"] is None
    assert card["overall"]["project"]["n"] == 1
    assert card["overall"]["both"]["n"] == 0


def test_only_finalized_rows_scored():
    rows = [_row(), _row(finalized_datetime=None, pred_sub_cost_code_id=99)]
    card = sc.build_coding_scorecard(rows)
    assert card["n_finalized"] == 1
    assert card["overall"]["code"]["n"] == 1
    assert card["overall"]["code"]["right"] == 1


def test_grouping_by_version_source_mode_intake_and_text_decision():
    rows = [
        _row(),
        _row(model_version="u654b-text-v1", coding_source=None, pred_sub_cost_code_id=3),
        _row(coding_source="history", coding_mode="B", intake_source="list_drop",
             text_decision_source="escalate"),
    ]
    card = sc.build_coding_scorecard(rows)
    assert card["by_model_version"]["u701b-text-v2"]["code"]["n"] == 2
    assert card["by_model_version"]["u701b-text-v2"]["code"]["right"] == 2
    assert card["by_model_version"]["u654b-text-v1"]["code"]["n"] == 1
    assert card["by_model_version"]["u654b-text-v1"]["code"]["right"] == 0
    assert set(card["by_coding_source"]) == {"primary", "history", sc.UNSET}
    assert set(card["by_coding_mode"]) == {"A", "B"}
    assert set(card["by_intake_source"]) == {"pdf_drop", "list_drop"}
    assert card["by_text_decision_source"][sc.NO_TEXT_ROW]["code"]["n"] == 2
    assert card["by_text_decision_source"]["escalate"]["code"]["n"] == 1


def test_wilson_bounds():
    assert sc.wilson_interval(0, 0) is None
    lo, hi = sc.wilson_interval(64, 100)
    assert lo == pytest.approx(0.5424, abs=1e-4) and hi == pytest.approx(0.7273, abs=1e-4)
    lo, hi = sc.wilson_interval(0, 10)
    assert lo == 0.0 and 0.27 < hi < 0.28
    lo, hi = sc.wilson_interval(10, 10)
    assert hi == pytest.approx(1.0) and 0.72 < lo < 0.73
    for k, n in ((1, 3), (7, 9), (50, 51)):
        lo, hi = sc.wilson_interval(k, n)
        assert 0.0 <= lo <= k / n <= hi <= 1.0
        assert not math.isnan(lo)


def test_metric_carries_ci_percent():
    card = sc.build_coding_scorecard([_row()] * 64 + [_row(pred_sub_cost_code_id=2)] * 36)
    code = card["overall"]["code"]
    assert (code["ci95_low_pct"], code["ci95_high_pct"]) == (54.2, 72.7)


def test_per_rung_counterfactuals():
    rows = [
        _row(primary_model="haiku", primary_sub_cost_code_id=10,
             text_escalate_model="sonnet", text_escalate_sub_cost_code_id=11,
             fallback_model="ds", fallback_sub_cost_code_id=10,
             similar_pred_sub_cost_code_id=10, text_status="done", text_sub_cost_code_id=10),
        _row(primary_model="haiku", primary_sub_cost_code_id=None),  # attempted, abstained
        _row(coding_source="history"),
        _row(coding_source="history", pred_sub_cost_code_id=4),
        _row(),  # no rung data at all
    ]
    rungs = sc.build_coding_scorecard(rows)["rungs"]
    assert (rungs["primary"]["n"], rungs["primary"]["right"], rungs["primary"]["abstain"]) == (2, 1, 1)
    assert (rungs["escalate"]["n"], rungs["escalate"]["right"]) == (1, 0)
    assert (rungs["fallback"]["n"], rungs["fallback"]["right"]) == (1, 1)
    # V2 rows: the vote is attempted on every row, so the 4 rows without one abstain.
    assert (rungs["similar_vote"]["n"], rungs["similar_vote"]["right"]) == (5, 1)
    assert rungs["similar_vote"]["abstain"] == 4
    assert (rungs["history"]["n"], rungs["history"]["right"]) == (2, 1)
    assert (rungs["text_tick_decision"]["n"], rungs["text_tick_decision"]["right"]) == (1, 1)


def test_rungs_split_by_model_version():
    rows = [
        _row(primary_model="haiku", primary_sub_cost_code_id=10),
        _row(model_version="u654b-text-v1", primary_model="haiku", primary_sub_cost_code_id=9),
    ]
    by_v = sc.build_coding_scorecard(rows)["rungs_by_model_version"]
    assert by_v["u701b-text-v2"]["primary"]["right"] == 1
    assert by_v["u654b-text-v1"]["primary"]["right"] == 0


def test_empty_input():
    card = sc.build_coding_scorecard([])
    assert card["n_finalized"] == 0
    assert card["overall"]["code"] == {
        "n": 0, "right": 0, "abstain": 0, "rate_pct": None,
        "ci95_low_pct": None, "ci95_high_pct": None,
    }
    assert card["by_model_version"] == {}
    md = sc.format_coding_scorecard_markdown(card)
    assert "Live coding scorecard (U-744)" in md


def test_since_filter_uses_created_datetime():
    rows = [
        _row(created_datetime=datetime(2026, 10, 28, 23, 59)),
        _row(created_datetime=datetime(2026, 10, 29, 0, 0)),
        _row(created_datetime=None),
    ]
    card = sc.build_coding_scorecard(rows, since=date(2026, 10, 29))
    assert card["n_finalized"] == 1
    assert card["since"] == "2026-10-29"
    assert sc.build_coding_scorecard(rows)["n_finalized"] == 3


def test_reference_and_caveat_present():
    card = sc.build_coding_scorecard([_row()])
    ref = card["backtest_reference"]
    assert (ref["mode_a_code_pct"], ref["mode_b_code_pct"]) == (64.2, 64.4)
    assert "acceptance" in card["caveat"]
    md = sc.format_coding_scorecard_markdown(card)
    assert "64.2" in md and "64.4" in md and "acceptance" in md


def test_row_mapping_from_db_row():
    db = SimpleNamespace(
        Id=5, ModelVersion="u701b-text-v2", PredSubCostCodeId=1, FinalSubCostCodeId=1,
        FinalizedDatetime=FIN, TextEscalateSubCostCodeId=7, VendorName="SECRET VENDOR",
        PredBillNumber="SECRET-NO",
    )
    out = sc.scorecard_row_from_db(db)
    assert out["prediction_id"] == 5
    assert out["text_escalate_sub_cost_code_id"] == 7
    assert out["coding_mode"] is None
    assert "SECRET" not in repr(out)


def test_output_has_no_names_or_text():
    row = _row(vendor_name="SECRET VENDOR", pred_bill_number="SECRET-NO", pred_description="SECRET")
    card = sc.build_coding_scorecard([row])
    assert "SECRET" not in repr(card)
    assert "SECRET" not in sc.format_coding_scorecard_markdown(card)


# --- fix round 1 (P2-1 availability exclusion, P2-2 V2 vote always attempted) ---


def test_availability_failure_excluded_from_rung_and_counted():
    rows = [
        _row(primary_model="haiku", primary_sub_cost_code_id=None, primary_error="daily_cap"),
        _row(primary_model="haiku", primary_sub_cost_code_id=None, primary_error="http_529"),
        _row(primary_model="haiku", primary_sub_cost_code_id=10),
        _row(text_escalate_model="sonnet", text_escalate_sub_cost_code_id=None,
             text_escalate_error="no_key"),
    ]
    rungs = sc.build_coding_scorecard(rows)["rungs"]
    assert (rungs["primary"]["n"], rungs["primary"]["right"], rungs["primary"]["unavailable"]) == (1, 1, 2)
    assert rungs["primary"]["abstain"] == 0
    assert (rungs["escalate"]["n"], rungs["escalate"]["unavailable"]) == (0, 1)


def test_validation_failure_stays_scored_wrong():
    rows = [
        _row(primary_model="haiku", primary_sub_cost_code_id=None, primary_error="not_validated"),
        _row(primary_model="haiku", primary_sub_cost_code_id=None, primary_error="unparseable"),
        _row(text_escalate_model="sonnet", text_escalate_sub_cost_code_id=None,
             text_escalate_error="not_validated"),
    ]
    rungs = sc.build_coding_scorecard(rows)["rungs"]
    assert (rungs["primary"]["n"], rungs["primary"]["right"], rungs["primary"]["abstain"]) == (2, 0, 2)
    assert rungs["primary"]["unavailable"] == 0
    assert (rungs["escalate"]["n"], rungs["escalate"]["abstain"]) == (1, 1)


def test_availability_classifier():
    for code in ("daily_cap", "no_key", "credit", "disabled", "skipped", "error", "http_500"):
        assert sc.is_availability_error(code), code
    for code in (None, "", "not_validated", "unparseable"):
        assert not sc.is_availability_error(code), code


def test_v2_similar_vote_always_attempted_null_is_abstain():
    rows = [
        _row(similar_pred_sub_cost_code_id=None),
        _row(similar_pred_sub_cost_code_id=10),
    ]
    rung = sc.build_coding_scorecard(rows)["rungs"]["similar_vote"]
    assert (rung["n"], rung["right"], rung["abstain"]) == (2, 1, 1)
    assert rung["basis"] == sc.BASIS_EVERY_V2_ROW


def test_legacy_similar_vote_is_conditional_and_labelled():
    legacy = "u654b-text-v1"
    rows = [
        _row(model_version=legacy, similar_pred_sub_cost_code_id=None),
        _row(model_version=legacy, similar_pred_sub_cost_code_id=10),
    ]
    card = sc.build_coding_scorecard(rows)
    rungs = card["rungs"]
    assert rungs["similar_vote"]["n"] == 0
    leg = rungs["similar_vote_legacy"]
    assert (leg["n"], leg["right"], leg["abstain"]) == (1, 1, 0)
    assert leg["basis"] == sc.BASIS_CONDITIONAL
    assert card["rungs_by_model_version"][legacy]["similar_vote_legacy"]["n"] == 1
    md = sc.format_coding_scorecard_markdown(card)
    assert "conditional on answering" in md
