"""U-797c4b item 1 — ReviewerDecisionSpec carries an explicit `kind` (the binder
target / ReviewNotification Kind) and a `noun` (error-text noun), so a BillCredit
spec can say "bill credit" in prose while binding on "bill_credit".

Bill and Expense keep their behaviour byte-identical: they pass kind="bill" /
"expense" explicitly and their noun defaults to their parent.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from entities.bill.business.service import BillService
from entities.bill_credit.business.service import BillCreditService
from entities.expense.business.service import ExpenseService
from entities.review.business import reviewer_decision as rd


class _Stop(Exception):
    """Raised by the binder fake so apply_emailed stops right after the binder."""


def _spec(**overrides) -> rd.ReviewerDecisionSpec:
    base = dict(
        parent="bill_credit",
        kind="bill_credit",
        entity_type="BillCredit",
        provenance_what="a bill credit reviewer decision",
        read_parent=lambda public_id: SimpleNamespace(id=7, is_draft=True),
        resolve_recipients=lambda svc, parent_id: {
            "to": [SimpleNamespace(email="pm@example.test", user_id=11)],
            "cc": [],
        },
        line_service_factory=lambda: MagicMock(),
        read_lines=lambda svc, parent_id: [],
        count_lines_for_update=lambda svc, parent_id, conn: 0,
        read_lines_in_transaction=lambda svc, parent_id, conn: [],
        lock_current_review=lambda repo, parent_id, conn: None,
        apply_error=lambda code, **kw: RuntimeError(code),
        get_connection=MagicMock(),
        sub_cost_code_service=MagicMock(),
        assert_may_act_as=MagicMock(return_value=False),
        logger=MagicMock(),
        emailed_fresh_status_service_for_readback=False,
    )
    base.update(overrides)
    return rd.ReviewerDecisionSpec(**base)


@pytest.fixture
def binder_stop():
    """Run apply_emailed up to the binder call, record the binder's target, stop."""
    seen = {}

    def _bind(**kwargs):
        seen.update(kwargs)
        raise _Stop()

    recipients = MagicMock()
    recipients.return_value.resolve_for_bill_credit.return_value = {"to": [], "cc": []}
    with patch("shared.authz.provenance.bind_reviewer_reply_with_notification", side_effect=_bind), \
            patch("entities.review.business.recipient_service.ReviewRecipientService", recipients):
        yield seen


# ---------------------------------------------------------------------------
# The three entities declare their kind explicitly.
# ---------------------------------------------------------------------------


def test_bill_spec_declares_kind_bill():
    assert BillService(repo=MagicMock())._reviewer_decision_spec().kind == "bill"


def test_expense_spec_declares_kind_expense():
    assert ExpenseService(repo=MagicMock())._reviewer_decision_spec().kind == "expense"


def test_bill_credit_spec_declares_kind_bill_credit_and_prose_noun():
    spec = BillCreditService(repo=MagicMock())._reviewer_decision_spec()
    assert spec.kind == "bill_credit"
    assert spec.parent == "bill_credit"
    assert spec.noun == "bill credit"
    assert spec.entity_type == "BillCredit"


def test_bill_and_expense_noun_defaults_to_parent():
    # Bill/Expense error text is byte-identical: the noun is their parent word.
    assert BillService(repo=MagicMock())._reviewer_decision_spec().error_noun == "bill"
    assert ExpenseService(repo=MagicMock())._reviewer_decision_spec().error_noun == "expense"


# ---------------------------------------------------------------------------
# The binder keys on spec.kind, never on spec.parent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["bill", "expense", "bill_credit"])
def test_binder_target_uses_kind(binder_stop, kind):
    spec = _spec(parent="bill_credit", kind=kind)
    with pytest.raises(_Stop):
        rd.apply_emailed(
            spec,
            public_id="bc-1",
            decision="approved",
            reviewer_email="pm@example.test",
            reviewer_email_message_public_id="m-1",
        )
    assert binder_stop["target"] == (kind, 7)


def test_binder_ignores_parent_when_kind_differs(binder_stop):
    # The point of the explicit field: parent="bill" must not leak into the target
    # when the spec's kind says bill_credit.
    spec = _spec(parent="bill", kind="bill_credit")
    with pytest.raises(_Stop):
        rd.apply_emailed(
            spec,
            public_id="bc-1",
            decision="rejected",
            reviewer_email="pm@example.test",
            reviewer_email_message_public_id="m-1",
        )
    assert binder_stop["target"] == ("bill_credit", 7)


# ---------------------------------------------------------------------------
# Error text uses the noun.
# ---------------------------------------------------------------------------


def test_multi_line_refusal_prose_uses_noun():
    # Binder succeeds (no stop) so the line-count refusal is reached.
    spec = _spec(read_lines=lambda svc, parent_id: [object(), object()], noun="bill credit")
    with patch("shared.authz.provenance.bind_reviewer_reply_with_notification",
               return_value=SimpleNamespace(id=99)), \
            patch("entities.review.business.recipient_service.ReviewRecipientService"):
        with pytest.raises(ValueError) as err:
            rd.apply_emailed(
                spec,
                public_id="bc-1",
                decision="approved",
                reviewer_email="pm@example.test",
                sub_cost_code_public_id="scc-1",
                reviewer_email_message_public_id="m-1",
            )
    message = str(err.value)
    assert "multi-line bill credit" in message
    assert "bill_credit" not in message


def test_multi_line_refusal_prose_unchanged_for_bill():
    spec = _spec(
        parent="bill", kind="bill", entity_type="Bill",
        read_lines=lambda svc, parent_id: [object(), object()],
    )
    with patch("shared.authz.provenance.bind_reviewer_reply_with_notification",
               return_value=SimpleNamespace(id=99)), \
            patch("entities.review.business.recipient_service.ReviewRecipientService"):
        with pytest.raises(ValueError) as err:
            rd.apply_emailed(
                spec,
                public_id="b-1",
                decision="approved",
                reviewer_email="pm@example.test",
                sub_cost_code_public_id="scc-1",
                reviewer_email_message_public_id="m-1",
            )
    assert "multi-line bill" in str(err.value)
