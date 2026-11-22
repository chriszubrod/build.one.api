"""U-797c4b item 3 — BillCreditService.apply_reviewer_decision with fakes only.

Every collaborator is patched; no DB, Graph or HTTP is touched. Covers the approve
and reject paths (one transaction, Review row with email_message_id, line coding
updated, no Status mirror) and each refusal.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill_credit.business.service import BillCreditService
from entities.review.business.decision_errors import ReviewDecisionApplyError
from shared.database import DatabaseConcurrencyError
from shared.lifecycle.completion_gate import HoldingCodeRefused

PM = SimpleNamespace(email="pm@example.test", user_id=11)
MOD = "entities.bill_credit.business.service"


def _statuses():
    approved = SimpleNamespace(id=3, name="Approved", is_final=True, is_declined=False)
    declined = SimpleNamespace(id=4, name="Declined", is_final=True, is_declined=True)
    return [approved, declined]


class Env:
    def __init__(self):
        self.credit = SimpleNamespace(id=7, public_id="bc-1", is_draft=True)
        self.scc = SimpleNamespace(id=55, public_id="scc-55", is_holding_code=False)
        self.line = SimpleNamespace(id=21, row_version="rv1", sub_cost_code_id=None, description=None)
        self.lines = [self.line]
        self.line_count_locked = 1
        self.binder_error = None
        self.line_svc = MagicMock(name="line_service")
        self.line_svc.read_by_bill_credit_id.side_effect = lambda **kw: list(self.lines)
        self.line_svc.count_by_bill_credit_id_for_update.side_effect = (
            lambda bill_credit_id, conn: self.line_count_locked
        )
        self.review_repo = MagicMock(name="review_repo")
        self.review_repo.create.side_effect = lambda **kw: SimpleNamespace(
            review_status_id=kw["review_status_id"], user_id=kw["user_id"], public_id="rev-1"
        )
        self.binder = MagicMock(name="binder")
        self.binder.side_effect = self._bind
        self.act_as = MagicMock(name="assert_may_act_as", return_value=False)


    def _bind(self, **kw):
        if self.binder_error is not None:
            raise self.binder_error
        return SimpleNamespace(id=99)


@pytest.fixture
def env():
    e = Env()
    with ExitStack() as stack:
        stack.enter_context(patch.object(BillCreditService, "read_by_public_id", return_value=e.credit))
        recipients = MagicMock()
        recipients.return_value.resolve_for_bill_credit.return_value = {"to": [PM], "cc": []}
        stack.enter_context(patch("entities.review.business.recipient_service.ReviewRecipientService", recipients))
        scc_svc = MagicMock()
        scc_svc.return_value.read_by_public_id.side_effect = lambda public_id: e.scc
        stack.enter_context(patch(f"{MOD}.SubCostCodeService", scc_svc))
        e.scc_svc = scc_svc
        stack.enter_context(patch(f"{MOD}.get_connection", MagicMock()))
        stack.enter_context(patch(f"{MOD}.assert_may_act_as", e.act_as))
        stack.enter_context(patch("shared.authz.provenance.bind_reviewer_reply_with_notification", e.binder))
        stack.enter_context(patch("entities.review.persistence.repo.ReviewRepository", MagicMock(return_value=e.review_repo)))
        rs = MagicMock()
        rs.return_value.read_all.return_value = _statuses()
        rs.return_value.read_by_id.side_effect = lambda id: next(s for s in _statuses() if s.id == id)
        stack.enter_context(patch("entities.review_status.business.service.ReviewStatusService", rs))
        stack.enter_context(
            patch("entities.bill_credit_line_item.business.service.BillCreditLineItemService",
                  MagicMock(return_value=e.line_svc))
        )
        yield e


def _approve(**kw):
    args = dict(
        bill_credit_public_id="bc-1",
        decision="approved",
        reviewer_email="pm@example.test",
        sub_cost_code_public_id="scc-55",
        description="Freight credit",
        raw_reply_text="Approved, code it to freight",
        reviewer_email_message_public_id="m-1",
    )
    args.update(kw)
    return BillCreditService(repo=MagicMock()).apply_reviewer_decision(**args)


def _reject(**kw):
    args = dict(
        bill_credit_public_id="bc-1",
        decision="rejected",
        reviewer_email="pm@example.test",
        raw_reply_text="Needs revision",
        reviewer_email_message_public_id="m-1",
    )
    args.update(kw)
    return BillCreditService(repo=MagicMock()).apply_reviewer_decision(**args)


# ---------------------------------------------------------------------------
# Approve / reject happy paths.
# ---------------------------------------------------------------------------


def test_approve_codes_the_line_and_inserts_review_with_email_message(env):
    result = _approve()
    assert result["decision_applied"] == "approved"
    assert result["review_status"] == "Approved"
    assert result["bill_credit_public_id"] == "bc-1"
    env.line_svc.update_coding_in_transaction.assert_called_once()
    kw = env.line_svc.update_coding_in_transaction.call_args.kwargs
    assert (kw["sub_cost_code_id"], kw["description"], kw["row_version"]) == (55, "Freight credit", "rv1")
    created = env.review_repo.create.call_args.kwargs
    assert created["bill_credit_id"] == 7
    assert created["bill_id"] is None and created["expense_id"] is None
    assert created["review_status_id"] == 3
    assert created["email_message_id"] == 99
    assert created["comments"] == "Approved, code it to freight"
    assert created["user_id"] == 11


def test_approve_locks_review_then_recounts_lines_before_writing(env):
    order = []
    env.review_repo.read_current_by_bill_credit_id_for_update.side_effect = lambda bill_credit_id, conn: order.append("lock")
    env.line_svc.count_by_bill_credit_id_for_update.side_effect = (
        lambda bill_credit_id, conn: order.append("count") or 1
    )
    env.line_svc.update_coding_in_transaction.side_effect = lambda **kw: order.append("update")
    env.review_repo.create.side_effect = lambda **kw: order.append("insert") or SimpleNamespace(
        review_status_id=3, user_id=11)
    _approve()
    assert order == ["lock", "count", "update", "insert"]


def test_reject_inserts_declined_review_without_touching_lines(env):
    result = _reject()
    assert result["decision_applied"] == "rejected"
    assert result["review_status"] == "Declined"
    env.line_svc.update_coding_in_transaction.assert_not_called()
    created = env.review_repo.create.call_args.kwargs
    assert created["review_status_id"] == 4
    assert created["bill_credit_id"] == 7
    assert created["email_message_id"] == 99


def test_no_status_mirror_is_written_onto_the_credit(env):
    # BillCredit has no Status column: the credit object must not gain one.
    _approve()
    assert not hasattr(env.credit, "status")


def test_binder_gets_bill_credit_target_and_caller_is_checked(env):
    _approve()
    assert env.binder.call_args.kwargs["target"] == ("bill_credit", 7)
    env.act_as.assert_called_once()
    assert env.act_as.call_args.kwargs["asserted_user_id"] == 11


# ---------------------------------------------------------------------------
# Refusals.
# ---------------------------------------------------------------------------


def test_unknown_decision_is_refused(env):
    with pytest.raises(ValueError, match="decision must be"):
        _approve(decision="maybe")


def test_not_draft_is_refused(env):
    env.credit.is_draft = False
    with pytest.raises(ValueError, match="no longer a draft"):
        _approve()
    env.review_repo.create.assert_not_called()


def test_unknown_sender_is_refused(env):
    with pytest.raises(ValueError, match="not an authorized reviewer"):
        _approve(reviewer_email="stranger@example.test")
    env.binder.assert_not_called()


def test_binder_refusal_propagates_and_writes_nothing(env):
    env.binder_error = ValueError("reply not bound to the review thread")
    with pytest.raises(ValueError, match="not bound"):
        _approve()
    env.line_svc.update_coding_in_transaction.assert_not_called()
    env.review_repo.create.assert_not_called()


def test_multi_line_credit_is_refused_before_the_scc_lookup(env):
    env.lines = [env.line, SimpleNamespace(id=22, row_version="rv2")]
    with pytest.raises(ValueError, match="2 line items"):
        _approve()
    env.scc_svc.return_value.read_by_public_id.assert_not_called()


def test_zero_line_credit_is_refused(env):
    env.lines = []
    with pytest.raises(ValueError, match="has no line items"):
        _approve()


def test_missing_sub_cost_code_is_refused_on_approval(env):
    with pytest.raises(ValueError, match="sub_cost_code_public_id is required"):
        _approve(sub_cost_code_public_id=None)


def test_unknown_sub_cost_code_is_refused(env):
    env.scc = None
    with pytest.raises(ValueError, match="not found"):
        _approve()


def test_holding_sub_cost_code_is_refused(env):
    env.scc.is_holding_code = True
    with pytest.raises(HoldingCodeRefused):
        _approve()
    env.review_repo.create.assert_not_called()


def test_line_added_since_fast_path_is_refused_under_lock(env):
    env.line_count_locked = 2
    with pytest.raises(ValueError, match="2 line items"):
        _approve()
    env.line_svc.update_coding_in_transaction.assert_not_called()
    env.review_repo.create.assert_not_called()


def test_stale_line_rowversion_maps_to_409(env):
    env.line_svc.update_coding_in_transaction.side_effect = DatabaseConcurrencyError("stale")
    with pytest.raises(ReviewDecisionApplyError) as err:
        _approve()
    assert err.value.status_code == 409
    assert err.value.error_code == "line_row_version_stale"
    assert "bill credit" in err.value.detail
    env.review_repo.create.assert_not_called()
