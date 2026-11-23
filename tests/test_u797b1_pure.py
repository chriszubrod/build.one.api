"""U-797b1 pure logic: the suggestion wait (hold), the display state of one suggestion row, and the
lines fingerprint that b2 reuses. No database, no network: the rows are the real repo dataclass."""

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from entities.contract_labor.business import coding_shadow_service as css
from entities.contract_labor.persistence.coding_suggestion_repo import SuggestionRow
from entities.review.business import review_notification_service as rns

NOW = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)


def _row(**overrides):
    base = dict(
        id=1,
        contract_labor_id=10,
        status="done",
        decision_source="history",
        sub_cost_code_id=100,
        sub_cost_code_number="65.02",
        sub_cost_code_name="Misc Labor",
        is_holding_code=False,
        may_split=False,
        model_split_code_ids=(),
        contract_labor_modified_datetime=NOW - timedelta(minutes=5),
    )
    base.update(overrides)
    return SuggestionRow(**base)


# ---- the wait (hold) -----------------------------------------------------------------------

def test_wait_constant_is_thirty_minutes():
    assert css.CL_SUGGESTION_WAIT_MINUTES == 30


def test_no_rows_never_holds():
    assert css.suggestion_hold([], now=NOW) is False


def test_pending_row_with_recent_submit_holds():
    rows = [_row(status="pending", contract_labor_modified_datetime=NOW - timedelta(minutes=10))]
    assert css.suggestion_hold(rows, now=NOW) is True


def test_pending_row_at_exactly_thirty_minutes_releases():
    rows = [_row(status="pending", contract_labor_modified_datetime=NOW - timedelta(minutes=30))]
    assert css.suggestion_hold(rows, now=NOW) is False


def test_pending_row_at_twenty_nine_minutes_still_holds():
    rows = [_row(status="pending", contract_labor_modified_datetime=NOW - timedelta(minutes=29))]
    assert css.suggestion_hold(rows, now=NOW) is True


def test_all_terminal_recent_rows_release():
    rows = [
        _row(id=1, contract_labor_id=10, status="done"),
        _row(id=2, contract_labor_id=11, status="failed", decision_source=None, sub_cost_code_id=None),
    ]
    assert css.suggestion_hold(rows, now=NOW) is False


def test_one_pending_among_terminal_holds_on_the_crew_latest_submit():
    rows = [
        _row(id=1, contract_labor_id=10, status="done",
             contract_labor_modified_datetime=NOW - timedelta(minutes=40)),
        _row(id=2, contract_labor_id=11, status="pending",
             contract_labor_modified_datetime=NOW - timedelta(minutes=5)),
    ]
    assert css.suggestion_hold(rows, now=NOW) is True


def test_latest_submit_is_the_max_across_the_crew():
    rows = [
        _row(id=1, contract_labor_id=10, status="pending",
             contract_labor_modified_datetime=NOW - timedelta(minutes=45)),
        _row(id=2, contract_labor_id=11, status="pending",
             contract_labor_modified_datetime=NOW - timedelta(minutes=3)),
    ]
    assert css.suggestion_hold(rows, now=NOW) is True


def test_pending_without_any_submit_stamp_releases():
    rows = [_row(status="pending", contract_labor_modified_datetime=None)]
    assert css.suggestion_hold(rows, now=NOW) is False


def test_naive_utc_stamp_is_read_as_utc():
    naive = (NOW - timedelta(minutes=5)).replace(tzinfo=None)
    rows = [_row(status="pending", contract_labor_modified_datetime=naive)]
    assert css.suggestion_hold(rows, now=NOW) is True


# ---- display state of one row ----------------------------------------------------------------

def test_absent_row_is_no_row():
    d = css.suggestion_display(None, {})
    assert (d.state, d.code_id, d.code_label, d.suggestion_id, d.source, d.split_shown) == (
        "no_row", None, None, None, None, None)


def test_history_clean_row_shows_number_and_name():
    d = css.suggestion_display(_row(), {})
    assert d.state == "clean"
    assert d.code_label == "65.02 Misc Labor"
    assert (d.code_id, d.suggestion_id, d.source) == (100, 1, "history")
    assert d.split_shown is False
    assert d.candidate_labels == ()


def test_model_clean_row_shows_the_model_source():
    d = css.suggestion_display(_row(decision_source="model", id=7), {})
    assert d.state == "clean"
    assert d.source == "model"
    assert d.suggestion_id == 7


def test_split_row_with_code_marks_top_code_as_split_and_lists_candidates():
    labels = {1: ("1.00", "Alpha"), 3: ("3.00", "Gamma")}
    row = _row(may_split=True, model_split_code_ids=(1, 2, 3))
    d = css.suggestion_display(row, labels)
    assert d.state == "split"
    assert d.code_label == "65.02 Misc Labor"
    assert d.split_shown is True
    assert d.candidate_labels == ("1.00 Alpha", "3.00 Gamma")  # id 2 has no label and is dropped


def test_split_row_without_code_shows_no_suggestion_but_split_note():
    row = _row(decision_source="none", sub_cost_code_id=None, sub_cost_code_number=None,
               sub_cost_code_name=None, may_split=True, model_split_code_ids=(1,))
    d = css.suggestion_display(row, {1: ("1.00", "Alpha")})
    assert d.state == "split"
    assert d.code_id is None and d.code_label is None
    assert d.split_shown is True
    assert d.candidate_labels == ("1.00 Alpha",)


def test_abstain_row_is_no_suggestion_without_split():
    row = _row(decision_source="none", sub_cost_code_id=None, sub_cost_code_number=None,
               sub_cost_code_name=None)
    d = css.suggestion_display(row, {})
    assert d.state == "no_suggestion"
    assert d.code_id is None
    assert d.split_shown is False


def test_holding_code_is_never_shown_as_a_suggestion():
    row = _row(is_holding_code=True, may_split=True, model_split_code_ids=(1,))
    d = css.suggestion_display(row, {1: ("1.00", "Alpha")})
    assert d.state == "holding"
    assert d.code_id is None and d.code_label is None
    assert d.suggestion_id is None and d.source is None
    assert d.split_shown is False
    assert d.candidate_labels == ()


@pytest.mark.parametrize(
    "status, state",
    [("failed", "failed"), ("skipped", "skipped"), ("pending", "pending")],
)
def test_non_done_rows_show_nothing_and_no_split_claim(status, state):
    d = css.suggestion_display(_row(status=status, may_split=True), {})
    assert d.state == state
    assert d.code_id is None and d.code_label is None
    assert d.suggestion_id is None and d.source is None
    assert d.split_shown is None


def test_label_format_is_number_space_name():
    assert css.suggestion_label("8.11", "Crew Pay") == "8.11 Crew Pay"


# ---- the lines fingerprint (b2 reuses it) ---------------------------------------------------

def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_fingerprint_is_sha256_of_sorted_id_hours_pairs():
    pairs = [(2, Decimal("3.00")), (1, Decimal("2.50"))]
    assert rns.cl_lines_fingerprint(pairs) == _sha("1:2.50\n2:3.00")


def test_fingerprint_is_order_independent():
    a = [(1, Decimal("2.50")), (2, Decimal("3.00")), (10, Decimal("1.00"))]
    b = list(reversed(a))
    assert rns.cl_lines_fingerprint(a) == rns.cl_lines_fingerprint(b)


def test_fingerprint_is_deterministic():
    pairs = [(5, Decimal("4.00"))]
    assert rns.cl_lines_fingerprint(pairs) == rns.cl_lines_fingerprint(list(pairs))


def test_fingerprint_changes_when_hours_change():
    before = rns.cl_lines_fingerprint([(1, Decimal("2.50"))])
    after = rns.cl_lines_fingerprint([(1, Decimal("2.75"))])
    assert before != after


def test_fingerprint_changes_when_a_line_is_added_or_removed():
    one = rns.cl_lines_fingerprint([(1, Decimal("2.50"))])
    two = rns.cl_lines_fingerprint([(1, Decimal("2.50")), (2, Decimal("1.00"))])
    assert one != two


def test_fingerprint_treats_missing_hours_as_zero_and_is_hex_64():
    fp = rns.cl_lines_fingerprint([(9, None)])
    assert fp == _sha("9:0")
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_of_no_lines_is_the_empty_string_digest():
    assert rns.cl_lines_fingerprint([]) == _sha("")


# ---- fix round 1 -------------------------------------------------------------------------------

def test_blank_code_row_shows_no_suggestion_and_stores_no_code_id():
    # F7: a code row whose Number and Name are both blank must not show a code nor store its id.
    d = css.suggestion_display(_row(sub_cost_code_number="", sub_cost_code_name=None), {})
    assert d.state == "no_suggestion"
    assert d.code_id is None and d.code_label is None
    assert d.suggestion_id is None and d.source is None


def test_blank_code_row_with_a_split_is_split_with_no_code():
    d = css.suggestion_display(
        _row(sub_cost_code_number=None, sub_cost_code_name=" ", may_split=True, model_split_code_ids=(1,)),
        {1: ("1.00", "Alpha")},
    )
    assert d.state == "split"
    assert d.code_id is None and d.code_label is None
    assert d.split_shown is True


def test_hold_ignores_members_that_are_not_submitted():
    # F4: only submitted members count in the hold, the same rule the sweep uses.
    rows = [_row(status="pending", contract_labor_status="ready",
                 contract_labor_modified_datetime=NOW - timedelta(minutes=5))]
    assert css.suggestion_hold(rows, now=NOW) is False


def test_row_contract_labor_status_defaults_to_submitted():
    assert _row().contract_labor_status == "submitted"


# ---- round 2 (H2): one definition of "pending among members with a submitted line" -------------

def test_members_need_submitted_status_and_a_line_on_the_project():
    rows = [
        _row(id=1, contract_labor_id=10),
        _row(id=2, contract_labor_id=11, contract_labor_status="ready"),
        SuggestionRow(id=3, contract_labor_id=12, status="pending", decision_source=None, sub_cost_code_id=None,
                      sub_cost_code_number=None, sub_cost_code_name=None, is_holding_code=False, may_split=None,
                      model_split_code_ids=(), contract_labor_modified_datetime=NOW, has_submitted_project_line=False),
    ]
    assert [r.id for r in css.crew_members(rows)] == [1]


def test_hold_ignores_pending_member_without_a_submitted_line_on_the_project():
    rows = [SuggestionRow(id=3, contract_labor_id=12, status="pending", decision_source=None,
                          sub_cost_code_id=None, sub_cost_code_number=None, sub_cost_code_name=None,
                          is_holding_code=False, may_split=None, model_split_code_ids=(),
                          contract_labor_modified_datetime=NOW - timedelta(minutes=5),
                          has_submitted_project_line=False)]
    assert css.suggestion_hold(rows, now=NOW) is False


def test_settled_and_hold_use_the_same_pending_rule():
    pending_member = _row(id=1, contract_labor_id=10, status="pending",
                          contract_labor_modified_datetime=NOW - timedelta(minutes=5))
    ignored = SuggestionRow(id=2, contract_labor_id=11, status="pending", decision_source=None,
                            sub_cost_code_id=None, sub_cost_code_number=None, sub_cost_code_name=None,
                            is_holding_code=False, may_split=None, model_split_code_ids=(),
                            contract_labor_modified_datetime=NOW - timedelta(minutes=5),
                            contract_labor_status="ready")
    assert css.crew_day_settled([pending_member]) is False
    assert css.suggestion_hold([pending_member], now=NOW) is True
    assert css.crew_day_settled([pending_member, ignored]) is False
    assert css.crew_day_settled([ignored]) is True
    assert css.suggestion_hold([ignored], now=NOW) is False


def test_a_code_from_an_abstained_row_is_never_shown_or_stored():
    # Mutation M07 (EM pin): only the history and model rungs produce a suggestion. A row whose
    # DecisionSource is 'none' never shows (or snapshots) a code, even if a SubCostCodeId is present.
    d = css.suggestion_display(_row(decision_source="none"), {})
    assert d.state == "no_suggestion"
    assert (d.code_id, d.code_label, d.suggestion_id, d.source) == (None, None, None, None)
