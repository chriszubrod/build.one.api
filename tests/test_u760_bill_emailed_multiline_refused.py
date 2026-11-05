"""U-760 — the emailed reviewer decision codes a single-line Bill only.

Before U-760 the Bill emailed path (Path A) resolved the sub cost code, coded
`line_items[0]` and only holding-checked the OTHER lines, so an emailed approval of a
manually split bill stamped one cost code over the split. It now follows Expense: the
line count is checked BEFORE the sub cost code lookup and anything other than exactly
one line is refused with no recode, no lock and no Review row. A decline never codes a
line, so it applies to a bill of any line count — exactly as for Expense.

Driven through the U-755 recording harness (every collaborator faked; no DB/network).
"""

from __future__ import annotations

import pytest

from tests.test_u755_reviewer_decision_characterization import _line, _run

TWO_LINES = [_line("L1", 1, 3), _line("L2", 2, 4)]
MULTI_MSG = (
    "{E} PID-1 has 2 line items; a reviewer decision cannot be auto-applied to a "
    "multi-line {e} (would stamp one cost code over a manual split)."
)
ZERO_MSG = "{E} PID-1 has no line items; a reviewer decision cannot be auto-applied."

# Anything at or past the sub cost code lookup must not run on a refused approval.
_FORBIDDEN_AFTER_REFUSAL = (
    "scc.read_by_public_id",
    "statuses.read_all",
    "get_connection.enter",
    "lines.update_coding_in_transaction",
    "reviews.create",
)


def _names(result):
    return [c[0] for c in result["calls"]]


def _assert_refused(result, msg):
    assert result["outcome"]["raised"] == "ValueError"
    assert result["outcome"]["str"] == msg
    names = _names(result)
    for forbidden in _FORBIDDEN_AFTER_REFUSAL:
        assert forbidden not in names, forbidden
    assert not any(n.endswith("_for_update") for n in names)
    # The line read is the last thing that happens: the count check precedes the SCC.
    assert names[-1].startswith("lines.read_by_")


@pytest.mark.parametrize("entity", ["bill", "expense"])
def test_two_line_emailed_approve_is_refused_before_the_scc_lookup(entity):
    result = _run(entity, "A", lines=TWO_LINES)
    _assert_refused(result, MULTI_MSG.format(E=entity.capitalize(), e=entity))


def test_two_line_bill_with_a_holding_code_on_the_other_line_is_refused_as_multi_line():
    result = _run("bill", "A", lines=[_line("L1", 1, 3), _line("L2", 2, 99)], holding_ids={99})
    _assert_refused(result, MULTI_MSG.format(E="Bill", e="bill"))


@pytest.mark.parametrize("entity", ["bill", "expense"])
def test_zero_line_emailed_approve_is_refused(entity):
    result = _run(entity, "A", lines=[])
    _assert_refused(result, ZERO_MSG.format(E=entity.capitalize()))


def test_two_line_bill_without_a_scc_is_refused_on_line_count_first():
    result = _run("bill", "A", lines=TWO_LINES, kwargs={"sub_cost_code_public_id": None})
    _assert_refused(result, MULTI_MSG.format(E="Bill", e="bill"))


def test_one_line_bill_emailed_approve_codes_the_line_and_creates_the_review():
    result = _run("bill", "A")
    assert result["outcome"] == {
        "returned": {
            "bill_public_id": "PID-1",
            "decision_applied": "approved",
            "is_draft": True,
            "review_status": "Approved",
            "reviewer_user_id": 20,
        }
    }
    names = _names(result)
    assert names.index("lines.read_by_bill_id") < names.index("scc.read_by_public_id")
    update = next(c for c in result["calls"] if c[0] == "lines.update_coding_in_transaction")
    assert "L1" in repr(update)
    assert names.count("lines.update_coding_in_transaction") == 1
    assert names.count("reviews.create") == 1
    # The bill path still takes no under-lock recount (U-755 drift kept).
    assert "lines.count_by_bill_id_for_update" not in names


@pytest.mark.parametrize("entity", ["bill", "expense"])
@pytest.mark.parametrize("lines", [[], TWO_LINES], ids=["zero", "two"])
def test_decline_is_not_line_gated_for_either_entity(entity, lines):
    result = _run(
        entity,
        "A",
        lines=lines,
        kwargs={"decision": "rejected", "sub_cost_code_public_id": None},
    )
    assert result["outcome"]["returned"]["decision_applied"] == "rejected"
    assert result["outcome"]["returned"]["review_status"] == "Declined"
    names = _names(result)
    assert not any(n.startswith("lines.read_by_") for n in names)
    assert "lines.update_coding_in_transaction" not in names
    assert names.count("reviews.create") == 1
