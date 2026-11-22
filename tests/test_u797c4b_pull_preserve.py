"""U-797c4b — the VendorCredit pull keeps local coding while the parent BillCredit is
under human review; amounts always come from QBO. Pure rule (field_ownership) and the
lifecycle derivation. Fakes only."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.field_ownership import UNDER_REVIEW_STATUSES, under_review_line_coding
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)

LOCAL = SimpleNamespace(sub_cost_code_id=10, project_id=5, description="Local memo")
BARE = SimpleNamespace(sub_cost_code_id=None, project_id=None, description=None)


def test_under_review_statuses_are_the_expense_rule():
    assert UNDER_REVIEW_STATUSES == frozenset({"submitted", "in_review", "approved"})


@pytest.mark.parametrize("status", ["submitted", "in_review", "approved"])
def test_under_review_keeps_local_scc_project_and_description(status):
    scc, project, desc, preserved = under_review_line_coding(
        local=LOCAL, status=status,
        qbo_sub_cost_code_id=99, qbo_project_public_id="proj-qbo", qbo_description="QBO memo",
    )
    assert (scc, project, desc, preserved) == (10, None, "Local memo", True)


@pytest.mark.parametrize("status", ["draft", "completed", "declined", None])
def test_not_under_review_takes_qbo_values(status):
    assert under_review_line_coding(
        local=LOCAL, status=status,
        qbo_sub_cost_code_id=99, qbo_project_public_id="proj-qbo", qbo_description="QBO memo",
    ) == (99, "proj-qbo", "QBO memo", False)


def test_under_review_with_no_local_scc_takes_qbo_scc():
    scc, _, _, preserved = under_review_line_coding(
        local=BARE, status="in_review",
        qbo_sub_cost_code_id=99, qbo_project_public_id=None, qbo_description="QBO memo",
    )
    assert (scc, preserved) == (99, False)


def test_under_review_with_blank_local_description_takes_qbo_description():
    local = SimpleNamespace(sub_cost_code_id=10, project_id=None, description="   ")
    _, _, desc, _ = under_review_line_coding(
        local=local, status="approved",
        qbo_sub_cost_code_id=10, qbo_project_public_id=None, qbo_description="QBO memo",
    )
    assert desc == "QBO memo"


def test_under_review_with_no_local_project_takes_qbo_project():
    local = SimpleNamespace(sub_cost_code_id=10, project_id=None, description="x")
    _, project, _, _ = under_review_line_coding(
        local=local, status="submitted",
        qbo_sub_cost_code_id=10, qbo_project_public_id="proj-qbo", qbo_description=None,
    )
    assert project == "proj-qbo"


def test_quantity_and_amount_are_never_part_of_the_pull_decision():
    src = inspect.getsource(VendorCreditLineItemConnector.sync_from_qbo_line)
    assert "quantity=qbo_line.qty" in src
    assert "amount=qbo_line.amount" in src
    assert "under_review_line_coding(" in src
    assert "sub_cost_code_id=write_scc" in src


def _lifecycle(credit, review_kind):
    read_by_id = MagicMock(return_value=credit)
    review = SimpleNamespace(review_kind=review_kind) if review_kind else None
    repo = MagicMock()
    repo.read_current_by_bill_credit_id.return_value = review
    with patch("entities.bill_credit.business.service.BillCreditService",
               MagicMock(return_value=MagicMock(read_by_id=read_by_id))), \
            patch("entities.review.persistence.repo.ReviewRepository", MagicMock(return_value=repo)):
        return VendorCreditLineItemConnector._parent_lifecycle(7)


@pytest.mark.parametrize(
    "is_draft,review_kind,expected",
    [
        (True, None, "draft"),
        (True, "submitted", "submitted"),
        (True, "in_review", "in_review"),
        (True, "approved", "approved"),
        (True, "declined", "declined"),
        (False, "approved", "completed"),
    ],
)
def test_lifecycle_status_is_derived_like_the_api(is_draft, review_kind, expected):
    status, credit = _lifecycle(SimpleNamespace(id=7, is_draft=is_draft), review_kind)
    assert status == expected
    assert credit.id == 7


def test_lifecycle_is_none_for_a_missing_credit():
    assert _lifecycle(None, None) == (None, None)
