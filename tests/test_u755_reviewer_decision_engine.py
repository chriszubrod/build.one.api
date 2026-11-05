"""U-755 — shape pins for the shared reviewer-decision engine.

Behaviour is frozen by tests/test_u755_reviewer_decision_characterization.py; this
file pins the wiring: each entity's spec carries its own drift switches, the spec's
collaborators are read from the entity service module at call time (so the
long-standing `entities.{bill,expense}.business.service.*` patch points still bite),
and both routers delegate review-replies/sync to the one shared helper.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from entities.bill.business.service import BillService
from entities.expense.business.service import ExpenseService
from entities.review.business import reviewer_decision

SWITCHES = {
    BillService: (False, True),
    ExpenseService: (True, False),
}


@pytest.mark.parametrize("svc_cls", [BillService, ExpenseService], ids=["bill", "expense"])
def test_each_entity_keeps_its_own_drift_switches(svc_cls):
    spec = svc_cls(repo=MagicMock())._reviewer_decision_spec()
    assert (
        spec.emailed_recount_under_lock,
        spec.emailed_fresh_status_service_for_readback,
    ) == SWITCHES[svc_cls]


def test_emailed_line_policy_is_no_longer_a_switch():
    # U-760: both entities code a single-line parent only; the Bill-only
    # "first line + other-line holding check" policy and its hook are gone.
    fields = set(reviewer_decision.ReviewerDecisionSpec.__dataclass_fields__)
    assert "emailed_line_policy" not in fields
    assert "assert_no_holding_sub_cost_codes" not in fields


@pytest.mark.parametrize(
    "svc_cls,mod",
    [(BillService, "entities.bill.business.service"), (ExpenseService, "entities.expense.business.service")],
    ids=["bill", "expense"],
)
def test_spec_reads_collaborators_from_the_service_module_at_call_time(svc_cls, mod):
    gc, scc, act, lg = MagicMock(), MagicMock(), MagicMock(), MagicMock()
    with patch(f"{mod}.get_connection", gc), patch(f"{mod}.SubCostCodeService", scc), patch(
        f"{mod}.assert_may_act_as", act
    ), patch(f"{mod}.logger", lg):
        spec = svc_cls(repo=MagicMock())._reviewer_decision_spec()
    assert (spec.get_connection, spec.sub_cost_code_service, spec.assert_may_act_as, spec.logger) == (gc, scc, act, lg)


@pytest.mark.parametrize("svc_cls", [BillService, ExpenseService], ids=["bill", "expense"])
@pytest.mark.parametrize(
    "method,engine_fn",
    [("apply_reviewer_decision", "apply_emailed"), ("apply_transactional_reviewer_decision", "apply_transactional")],
)
def test_public_methods_delegate_to_the_engine(svc_cls, method, engine_fn):
    src = inspect.getsource(getattr(svc_cls, method))
    assert f"return {engine_fn}(" in src
    assert "self._reviewer_decision_spec()" in src


@pytest.mark.parametrize("router_mod", ["entities.bill.api.router", "entities.expense.api.router"])
def test_both_sync_routes_use_the_shared_helper(router_mod):
    import importlib

    src = inspect.getsource(importlib.import_module(router_mod).sync_review_replies_router)
    assert "return sync_review_replies(" in src


def test_engine_is_entity_agnostic():
    src = inspect.getsource(reviewer_decision)
    body = src.split('"""', 2)[2]
    for word in ("BillService", "ExpenseService", "bill_line_item", "expense_line_item"):
        assert word not in body


# ---------------------------------------------------------------------------
# expected_review_public_id is compared case-insensitively on BOTH sides
# (mutation survivor: every fixture's current review id was already uppercase).
# ---------------------------------------------------------------------------

from types import SimpleNamespace as _NS  # noqa: E402

from tests.test_u755_reviewer_decision_characterization import _run  # noqa: E402

_CASE_PAIRS = [
    ("abc-def-1", "ABC-DEF-1"),  # current lower, expected UPPER
    ("ABC-DEF-1", "abc-def-1"),  # current UPPER, expected lower
    ("AbC-dEf-1", "aBc-DeF-1"),  # mixed both sides
]


@pytest.mark.parametrize("entity", ["bill", "expense"])
@pytest.mark.parametrize("current_pid,expected_pid", _CASE_PAIRS)
def test_expected_review_match_ignores_case_on_both_sides(entity, current_pid, expected_pid):
    result = _run(
        entity,
        "B",
        current=_NS(_tag="current", public_id=current_pid),
        kwargs={"expected_review_public_id": expected_pid},
    )
    out = result["outcome"]
    assert "returned" in out, out
    assert out["returned"]["replayed"] is False
    assert any(c[0] == "reviews.create" for c in result["calls"])


@pytest.mark.parametrize("entity", ["bill", "expense"])
@pytest.mark.parametrize("current_pid,expected_pid", [("abc-def-1", "ABC-DEF-2"), ("ABC-DEF-1", "abc-def-2")])
def test_expected_review_genuine_mismatch_is_409_review_state_stale(entity, current_pid, expected_pid):
    result = _run(
        entity,
        "B",
        current=_NS(_tag="current", public_id=current_pid),
        kwargs={"expected_review_public_id": expected_pid},
    )
    out = result["outcome"]
    assert (out.get("raised"), out.get("error_code"), out.get("status_code")) == (
        "ReviewDecisionApplyError",
        "review_state_stale",
        409,
    )
    assert not any(c[0] in ("reviews.create", "lines.update_coding_in_transaction") for c in result["calls"])
