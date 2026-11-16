"""U-797a: pure scorecard math for the Contract Labor coding shadow."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from entities.contract_labor.business.coding_scorecard import build_cl_coding_scorecard


def _row(**over):
    base = {
        "work_date": date(2026, 11, 10),
        "decision_source": "model",
        "sub_cost_code_id": 5,
        "confidence": None,
        "may_split": False,
        "final_code_ids": (5,),
    }
    base.update(over)
    return base


def _rows(n, **over):
    return [_row(work_date=date(2026, 9, 1), **over) for _ in range(n)]


def test_single_day_exact_when_suggestion_matches_the_one_final_code():
    card = build_cl_coding_scorecard([_row(sub_cost_code_id=5, final_code_ids=(5,))])
    assert card["exact"]["correct"] == 1
    assert card["exact"]["scored"] == 1


def test_single_day_wrong_when_suggestion_differs():
    card = build_cl_coding_scorecard([_row(sub_cost_code_id=5, final_code_ids=(6,))])
    assert card["exact"]["correct"] == 0
    assert card["exact"]["scored"] == 1


def test_split_day_is_not_exact_for_a_single_suggestion():
    card = build_cl_coding_scorecard(
        [_row(sub_cost_code_id=5, may_split=True, final_code_ids=(5, 6))]
    )
    assert card["exact"]["correct"] == 0
    assert card["split_flag"]["flagged_and_split"] == 1
    assert card["split_flag"]["recall"] == 1.0
    assert card["split_flag"]["precision"] == 1.0


def test_split_flag_precision_and_recall_count_unsuggested_days_too():
    rows = [
        _row(sub_cost_code_id=None, decision_source="none", may_split=True, final_code_ids=(5, 6)),
        _row(sub_cost_code_id=5, may_split=True, final_code_ids=(5,)),
        _row(sub_cost_code_id=5, may_split=False, final_code_ids=(5, 6)),
    ]
    card = build_cl_coding_scorecard(rows)
    assert card["split_flag"]["flagged"] == 2
    assert card["split_flag"]["flagged_and_split"] == 1
    assert card["split_flag"]["split_days"] == 2
    assert card["split_flag"]["precision"] == 0.5
    assert card["split_flag"]["recall"] == 0.5


def test_unfinalized_rows_are_excluded_from_rates_and_counted_apart():
    rows = [_row(final_code_ids=None), _row(final_code_ids=(5,))]
    card = build_cl_coding_scorecard(rows)
    assert card["population"]["crew_days"] == 2
    assert card["population"]["finalized"] == 1
    assert card["population"]["unfinalized"] == 1
    assert card["exact"]["scored"] == 1


def test_coverage_is_suggested_over_finalized():
    rows = [
        _row(sub_cost_code_id=5, final_code_ids=(5,)),
        _row(sub_cost_code_id=None, decision_source="none", final_code_ids=(5,)),
        _row(final_code_ids=None),
    ]
    card = build_cl_coding_scorecard(rows)
    assert card["coverage"]["suggested"] == 1
    assert card["coverage"]["finalized"] == 2
    assert card["coverage"]["rate"] == 0.5


def test_confident_wrong_uses_confidence_at_or_above_point_seven():
    rows = [
        _row(decision_source="history", sub_cost_code_id=5, confidence=Decimal("0.7000"), final_code_ids=(6,)),
        _row(decision_source="history", sub_cost_code_id=5, confidence=Decimal("0.6900"), final_code_ids=(6,)),
    ]
    card = build_cl_coding_scorecard(rows)
    assert card["confident_wrong"]["count"] == 1
    assert card["confident_wrong"]["scored"] == 2


def test_breakdown_by_decision_source():
    rows = [
        _row(decision_source="history", sub_cost_code_id=5, final_code_ids=(5,)),
        _row(decision_source="model", sub_cost_code_id=5, final_code_ids=(6,)),
    ]
    card = build_cl_coding_scorecard(rows)
    assert card["by_source"]["history"]["exact"]["correct"] == 1
    assert card["by_source"]["model"]["exact"]["correct"] == 0
    assert card["by_source"]["history"]["exact"]["scored"] == 1


def test_exact_interval_is_wilson_95_percent():
    card = build_cl_coding_scorecard(_rows(10, final_code_ids=(5,)))
    low, high = card["exact"]["ci95"]
    assert 0.7 < low < 0.75
    assert high == 1.0
    assert card["exact"]["pct"] == 100.0


def test_since_filters_by_work_date():
    rows = [
        _row(work_date=date(2026, 10, 1), final_code_ids=(5,)),
        _row(work_date=date(2026, 11, 10), final_code_ids=(5,)),
    ]
    card = build_cl_coding_scorecard(rows, since=date(2026, 11, 1))
    assert card["population"]["crew_days"] == 1


def _ship(scored: int, correct: int) -> dict:
    rows = [_row(sub_cost_code_id=5, final_code_ids=(5,)) for _ in range(correct)]
    rows += [_row(sub_cost_code_id=5, final_code_ids=(6,)) for _ in range(scored - correct)]
    return build_cl_coding_scorecard(rows)["ship_bar"]


def test_ship_bar_requires_forty_scored_days():
    assert _ship(39, 39)["met"] is False


def test_ship_bar_met_at_exactly_forty_and_sixty_four_percent():
    # 40 scored: 25.6 needed, so 26/40 = 65 % is met; 25/40 = 62.5 % is not.
    assert _ship(40, 26)["met"] is True
    assert _ship(40, 25)["met"] is False


def test_ship_bar_sixty_four_percent_edge_on_a_hundred():
    assert _ship(100, 64)["met"] is True
    assert _ship(100, 63)["met"] is False


def test_ship_bar_block_reports_the_thresholds():
    bar = _ship(40, 26)
    assert bar["min_scored"] == 40
    assert bar["min_exact"] == 0.64
    assert bar["scored"] == 40


def test_availability_failures_are_excluded_from_every_rate():
    rows = [
        _row(failure_reason="model_unavailable", sub_cost_code_id=None, decision_source="none", final_code_ids=(5,)),
        _row(failure_reason="claude_unavailable", sub_cost_code_id=5, final_code_ids=(6,)),
        _row(final_code_ids=(5,)),
    ]
    card = build_cl_coding_scorecard(rows)
    assert card["population"]["crew_days"] == 1
    assert card["coverage"]["finalized"] == 1
    assert card["exact"]["scored"] == 1
    assert card["exact"]["correct"] == 1
    assert card["by_source"]["model"]["exact"]["scored"] == 1


def test_real_failures_still_count_as_scored_rows():
    rows = [_row(failure_reason="invalid_json", sub_cost_code_id=5, final_code_ids=(5,))]
    card = build_cl_coding_scorecard(rows)
    assert card["exact"]["scored"] == 1
