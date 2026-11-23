"""U-797b1 CL review email. Flag ON (CL_SUGGESTIONS_IN_REVIEW_ENABLED): the per-crew table with the suggestion
states, Is Billable / Is Overhead columns, escaping and the new ask with decline guidance. Flag OFF: trunk's email
(old paragraphs, old ask). Lines use the ReadSubmittedContractLaborLinesByWorkDate column shape."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest

from entities.contract_labor.business import coding_shadow_service as css
from entities.contract_labor.persistence.coding_suggestion_repo import SuggestionRow
from entities.review.business import cl_notification_service as cl_ns

NOW = datetime(2026, 11, 22, 15, 0, tzinfo=timezone.utc)
WORK_DATE = "2026-11-19"


@dataclass(frozen=True)
class SubmittedLine:
    ContractLaborId: int
    EmployeeName: str
    ProjectId: int
    ProjectName: str
    ProjectAbbreviation: str
    LineItemId: int
    Hours: Optional[Decimal]
    IsBillable: Optional[bool]
    IsOverhead: Optional[bool]
    Description: Optional[str]


def _line(cl_id, name, line_id, hours="2.00", desc="Framing", billable=True, overhead=False):
    return SubmittedLine(
        ContractLaborId=cl_id, EmployeeName=name, ProjectId=5, ProjectName="Oak Lane",
        ProjectAbbreviation="OL", LineItemId=line_id, Hours=Decimal(hours),
        IsBillable=billable, IsOverhead=overhead, Description=desc,
    )


def _suggestion(cl_id, **overrides):
    base = dict(
        id=cl_id * 10, contract_labor_id=cl_id, status="done", decision_source="history",
        sub_cost_code_id=100, sub_cost_code_number="65.02", sub_cost_code_name="Misc Labor",
        is_holding_code=False, may_split=False, model_split_code_ids=(),
        contract_labor_modified_datetime=NOW - timedelta(minutes=50),
    )
    base.update(overrides)
    return SuggestionRow(**base)


def _body(lines, rows=None, labels=None):
    """Flag-on body, built the way _do_enqueue builds it."""
    svc = cl_ns.ContractLaborReviewNotificationService()
    by_cl = {r.contract_labor_id: r for r in (rows or [])}
    labels = labels or {}
    displays = {
        line.ContractLaborId: css.suggestion_display(by_cl.get(line.ContractLaborId), labels)
        for line in lines
    }
    return svc._build_body(
        project_label="OL", work_date=WORK_DATE, lines=lines, pms=[],
        suggestions=displays, suggestions_enabled=True,
    )


# ---- flag ON: the table ------------------------------------------------------------------------

def test_flag_on_has_no_paragraph_worker_blocks():
    body = _body([_line(1, "Ana", 100)])
    assert "Hours:" not in body
    assert "<table" in body


def test_flag_on_header_has_seven_columns_in_order_including_billable_and_overhead():
    body = _body([_line(1, "Ana", 100)])
    header = (
        "<tr><th align='left'>Worker</th><th align='left'>Hours</th>"
        "<th align='left'>Is Billable</th><th align='left'>Is Overhead</th>"
        "<th align='left'>Notes/Description</th><th align='left'>Suggested code</th>"
        "<th align='left'>Note</th></tr>"
    )
    assert header in body


def test_flag_on_billable_and_overhead_values_are_rendered_per_line():
    body = _body([_line(1, "Ana", 100, billable=None, overhead=None), _line(2, "Ben", 101, billable=False, overhead=True)])
    assert "<td>Yes</td><td>No</td>" in body  # None billable defaults to Yes, None overhead to No
    assert "<td>No</td><td>Yes</td>" in body


def test_no_suggestion_rows_render_no_suggestion_in_every_line():
    body = _body([_line(1, "Ana", 100), _line(2, "Ben", 101)])
    assert body.count("no suggestion") == 2


def test_clean_suggestion_shows_number_and_name_with_empty_note():
    body = _body([_line(1, "Ana", 100)], rows=[_suggestion(1)])
    assert "65.02 Misc Labor" in body
    assert "(split?)" not in body
    assert "no suggestion" not in body


def test_split_suggestion_marks_top_code_and_lists_candidates():
    row = _suggestion(1, may_split=True, model_split_code_ids=(1, 3))
    labels = {1: ("1.00", "Alpha"), 3: ("3.00", "Gamma")}
    body = _body([_line(1, "Ana", 100)], rows=[row], labels=labels)
    assert "65.02 Misc Labor (split?)" in body
    assert "possible split: 1.00 Alpha, 3.00 Gamma" in body


def test_split_without_a_code_shows_no_suggestion_and_the_split_note():
    row = _suggestion(1, decision_source="none", sub_cost_code_id=None, sub_cost_code_number=None,
                      sub_cost_code_name=None, may_split=True, model_split_code_ids=(1,))
    body = _body([_line(1, "Ana", 100)], rows=[row], labels={1: ("1.00", "Alpha")})
    assert "no suggestion" in body
    assert "possible split: 1.00 Alpha" in body


def test_holding_row_says_holding_and_never_shows_the_code():
    body = _body([_line(1, "Ana", 100)], rows=[_suggestion(1, is_holding_code=True)])
    assert "holding code, not suggested" in body
    assert "65.02 Misc Labor" not in body


@pytest.mark.parametrize(
    "status, note",
    [("failed", "suggestion failed"), ("skipped", "suggestion skipped"), ("pending", "suggestion pending when sent")],
)
def test_failed_skipped_and_pending_rows_carry_their_note(status, note):
    row = _suggestion(1, status=status, decision_source=None, sub_cost_code_id=None,
                      sub_cost_code_number=None, sub_cost_code_name=None)
    body = _body([_line(1, "Ana", 100)], rows=[row])
    assert note in body
    assert "65.02" not in body


def test_blank_code_row_renders_no_suggestion_not_a_blank_label():
    row = _suggestion(1, sub_cost_code_number="", sub_cost_code_name="")
    body = _body([_line(1, "Ana", 100)], rows=[row])
    assert "no suggestion" in body
    assert "<td>  </td>" not in body


def test_worker_with_two_lines_repeats_the_crew_suggestion_on_each_line():
    body = _body([_line(1, "Ana", 100), _line(1, "Ana", 101, hours="1.00", desc="Trim")], rows=[_suggestion(1)])
    assert body.count("65.02 Misc Labor") == 2


def test_escaping_covers_names_descriptions_labels_and_notes():
    row = _suggestion(1, may_split=True, model_split_code_ids=(3,))
    line = _line(1, "Ben <Ort> & Co", 100, desc="<b>x</b> & y")
    body = _body([line], rows=[row], labels={3: ("3.00", "A & B <c>")})
    assert "Ben &lt;Ort&gt; &amp; Co" in body
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in body
    assert "3.00 A &amp; B &lt;c&gt;" in body
    assert "<b>x</b>" not in body


def test_missing_description_renders_placeholder_and_hours_format():
    body = _body([_line(1, "Ana", 100, hours="2", desc=None)])
    assert "(no description)" in body
    assert "<td>2.00</td>" in body


# ---- flag ON: the ask --------------------------------------------------------------------------

def test_flag_on_ask_is_the_new_text_with_decline_guidance():
    ask = cl_ns.contract_labor_ask("OL", WORK_DATE, suggestions=True)
    assert (
        'Reply "Approved" to apply the suggested codes, or reply with a sub cost code to apply it '
        "to the whole crew."
    ) in ask
    assert 'To decline, reply "Not approved" with the reason.' in ask


def test_flag_on_ask_escapes_the_project_and_date():
    assert "A &amp; B" in cl_ns.contract_labor_ask("A & B", "2026-11-19", suggestions=True)


# ---- flag OFF: trunk's email -------------------------------------------------------------------

def test_flag_off_body_is_the_trunk_paragraph_layout_with_billable_and_overhead():
    svc = cl_ns.ContractLaborReviewNotificationService()
    body = svc._build_body(project_label="OL", work_date=WORK_DATE,
                           lines=[_line(1, "Ana", 100, billable=None, overhead=None)], pms=[])
    assert "<p><b>Ana</b><br>Hours: 2.00<br>Is Billable: Yes<br>Is Overhead: No<br>Description: Framing</p>" in body
    assert "<table" not in body
    assert "suggest" not in body


def test_flag_off_ask_is_trunk_text():
    ask = cl_ns.contract_labor_ask("OL", WORK_DATE)
    assert "or not approved." in ask
    assert ask.endswith("the full crew listed below.</p>")


def test_flag_off_ignores_any_suggestions_passed_in():
    svc = cl_ns.ContractLaborReviewNotificationService()
    displays = {1: css.suggestion_display(_suggestion(1), {})}
    body = svc._build_body(project_label="OL", work_date=WORK_DATE, lines=[_line(1, "Ana", 100)], pms=[],
                           suggestions=displays, suggestions_enabled=False)
    assert "65.02" not in body and "<table" not in body
