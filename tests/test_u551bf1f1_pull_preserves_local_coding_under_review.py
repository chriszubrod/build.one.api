"""U-551b-f1-f1: the QBO purchase-line pull keeps local coding while a human reviews.

While the parent Expense is submitted / in_review / approved, QBO still holds the
submitter's coding (the completion repoint has not landed), so a reviewer's changed
coding must survive the 15-minute pull. Draft and completed are unchanged.

Driven through the real dbo-only identity fast path (HIT branch) with every service
mocked; nothing here opens a DB/HTTP connection.
"""
import logging
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    PurchaseLineExpenseLineItemConnector,
)

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")

_MODULE = "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
_LOGGER = _MODULE

LOCAL_SCC = 11
QBO_SCC = 22
QBO_DESCRIPTION = "QBO submitter description"
LOCAL_DESCRIPTION = "Reviewer description"
QBO_PROJECT = "qbo-project-public-id"

UNDER_REVIEW = ["submitted", "in_review", "approved"]
NOT_UNDER_REVIEW = ["draft", "completed"]


def _qbo_line(**overrides):
    defaults = dict(
        id=42,
        qbo_purchase_id=4,
        qbo_line_id="2",
        description=QBO_DESCRIPTION,
        amount=Decimal("500"),
        qty=Decimal("2"),
        unit_price=Decimal("250"),
        markup_percent=None,
        billable_status="Billable",
        customer_ref_value="cust-1",
        item_ref_value="item-99",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _local_line(**overrides):
    base = dict(
        id=10,
        public_id="eli-pub-10",
        row_version=b"rv",
        qbo_id="2",
        realm_id="realm-1",
        quantity=Decimal("9"),
        rate=Decimal("9"),
        amount=Decimal("81"),
        description=LOCAL_DESCRIPTION,
        sub_cost_code_id=LOCAL_SCC,
        project_id=5,
        markup=None,
        is_billable=False,
        is_billed=False,
        billable_decided_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _drive(status, direct, *, holding=False, expense="default"):
    """Run one pull of a QBO line onto `direct`; return (update kwargs, connector)."""
    connector = PurchaseLineExpenseLineItemConnector()
    line_svc = Mock()
    line_svc.read_by_expense_id.return_value = [direct]
    line_svc.read_by_qbo_identity.return_value = direct
    line_svc.update_by_public_id.return_value = SimpleNamespace(
        id=direct.id, public_id=direct.public_id
    )
    connector.expense_line_item_service = line_svc
    connector.sub_cost_code_service = Mock()
    connector.sub_cost_code_service.is_holding_code.return_value = holding
    connector.project_service = Mock()
    connector.project_service.read_by_qbo_identity.return_value = None
    connector.expense_service = Mock()
    if expense == "default":
        expense = SimpleNamespace(status=status, qbo_id="P-1", public_id="exp-pub")
    connector.expense_service.read_by_id.return_value = expense
    connector.reconciliation_repo = Mock()
    connector.project_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=77, public_id=QBO_PROJECT, qbo_id="cust-1", realm_id="realm-1"
    )
    with patch(
        f"{_MODULE}.resolve_dbo_sub_cost_code", return_value=SimpleNamespace(id=QBO_SCC)
    ), patch(f"{_MODULE}.verify_identity_dbo_only", return_value="cust-1"), patch(
        "integrations.intuit.qbo.base.reconciliation_recorder."
        "record_holding_code_on_approved_deduped"
    ) as recorder:
        connector.sync_from_qbo_purchase_line(
            10, "exp-pub", _qbo_line(), frozenset({"2"}), realm_id="realm-1"
        )
    line_svc.update_by_public_id.assert_called_once()
    return line_svc.update_by_public_id.call_args.kwargs, recorder


@pytest.mark.parametrize("status", UNDER_REVIEW)
def test_under_review_keeps_local_coding(status):
    """Kills: rule dropped for a status / rule applied at one field only."""
    kwargs, _ = _drive(status, _local_line())
    assert kwargs["sub_cost_code_id"] == LOCAL_SCC
    # None = ExpenseLineItemService "leave unchanged": the local project survives.
    assert kwargs["project_public_id"] is None
    assert kwargs["project_public_id"] != QBO_PROJECT
    assert kwargs["description"] == LOCAL_DESCRIPTION


@pytest.mark.parametrize("status", UNDER_REVIEW)
def test_under_review_uncoded_local_line_takes_qbo_coding(status):
    """Kills: an over-broad rule that blocks a new line's first coding."""
    direct = _local_line(sub_cost_code_id=None, project_id=None, description="")
    kwargs, _ = _drive(status, direct)
    assert kwargs["sub_cost_code_id"] == QBO_SCC
    assert kwargs["project_public_id"] == QBO_PROJECT
    assert kwargs["description"] == QBO_DESCRIPTION


@pytest.mark.parametrize("status", UNDER_REVIEW)
def test_under_review_blank_local_description_takes_qbo_description(status):
    """Kills: a description preserved when the local one is empty/whitespace."""
    kwargs, _ = _drive(status, _local_line(description="   "))
    assert kwargs["description"] == QBO_DESCRIPTION
    assert kwargs["sub_cost_code_id"] == LOCAL_SCC


@pytest.mark.parametrize("status", NOT_UNDER_REVIEW)
def test_draft_and_completed_take_qbo_coding_as_today(status):
    """Kills: the rule applied to draft / completed."""
    kwargs, recorder = _drive(status, _local_line())
    assert kwargs["sub_cost_code_id"] == QBO_SCC
    assert kwargs["project_public_id"] == QBO_PROJECT
    assert kwargs["description"] == QBO_DESCRIPTION
    recorder.assert_not_called()


def test_approved_holding_keeps_local_and_still_records_issue():
    """Kills: a dropped ReconciliationIssue recorder on the approved+holding case."""
    kwargs, recorder = _drive("approved", _local_line(), holding=True)
    assert kwargs["sub_cost_code_id"] == LOCAL_SCC
    recorder.assert_called_once()
    assert recorder.call_args.kwargs["entity_public_id"] == "exp-pub"


@pytest.mark.parametrize("status", ["submitted", "in_review"])
def test_holding_below_approved_records_no_issue(status):
    """Kills: the recorder widened beyond approved (it is approved-only)."""
    kwargs, recorder = _drive(status, _local_line(), holding=True)
    assert kwargs["sub_cost_code_id"] == LOCAL_SCC
    recorder.assert_not_called()


@pytest.mark.parametrize("status", UNDER_REVIEW + NOT_UNDER_REVIEW)
def test_economics_always_come_from_qbo(status):
    """Kills: an over-broad preserve that also freezes quantity/rate/amount/billable."""
    kwargs, _ = _drive(status, _local_line())
    assert kwargs["quantity"] == Decimal("2")
    assert kwargs["rate"] == Decimal("250")
    assert kwargs["amount"] == Decimal("500")
    assert kwargs["is_billable"] is True


def test_preserved_line_logs_once_with_ids_only(caplog):
    """Kills: a missing/duplicated INFO log, or a log that leaks descriptions."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _drive("in_review", _local_line())
    records = [
        r for r in caplog.records
        if "qbo.pull.local_coding_preserved_under_review" in r.getMessage()
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    message = records[0].getMessage()
    assert "in_review" in message and str(LOCAL_SCC) in message and str(QBO_SCC) in message
    assert LOCAL_DESCRIPTION not in message and QBO_DESCRIPTION not in message


@pytest.mark.parametrize("status", NOT_UNDER_REVIEW)
def test_no_log_when_nothing_is_preserved(status, caplog):
    """Kills: the preserve log firing outside review."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _drive(status, _local_line())
    assert not any(
        "local_coding_preserved_under_review" in r.getMessage() for r in caplog.records
    )


def test_unreadable_parent_behaves_as_today():
    """Kills: a crash (or a preserve) when the parent read returns None."""
    kwargs, recorder = _drive(None, _local_line(), expense=None)
    assert kwargs["sub_cost_code_id"] == QBO_SCC
    assert kwargs["project_public_id"] == QBO_PROJECT
    assert kwargs["description"] == QBO_DESCRIPTION
    recorder.assert_not_called()


# ---- F2: the status that decides the triple is read AFTER the line it protects ----


class _SequencedExpenseService:
    """read_by_id returns each queued status in turn (the last one repeats)."""

    def __init__(self, *statuses):
        self._statuses = list(statuses)
        self.calls = 0

    def read_by_id(self, expense_id):
        status = self._statuses[min(self.calls, len(self._statuses) - 1)]
        self.calls += 1
        return SimpleNamespace(status=status, qbo_id="P-1", public_id="exp-pub")


def _drive_sequenced(*statuses, direct=None, seq=None):
    direct = direct or _local_line()
    seq = seq or _SequencedExpenseService(*statuses)
    connector = PurchaseLineExpenseLineItemConnector()
    line_svc = Mock()
    line_svc.read_by_expense_id.return_value = [direct]
    line_svc.read_by_qbo_identity.return_value = direct
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=direct.id, public_id=direct.public_id)
    connector.expense_line_item_service = line_svc
    connector.sub_cost_code_service = Mock()
    connector.sub_cost_code_service.is_holding_code.return_value = False
    connector.expense_service = seq
    connector.reconciliation_repo = Mock()
    connector.project_service = Mock()
    connector.project_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=77, public_id=QBO_PROJECT, qbo_id="cust-1", realm_id="realm-1"
    )
    with patch(
        f"{_MODULE}.resolve_dbo_sub_cost_code", return_value=SimpleNamespace(id=QBO_SCC)
    ), patch(f"{_MODULE}.verify_identity_dbo_only", return_value="cust-1"):
        connector.sync_from_qbo_purchase_line(
            10, "exp-pub", _qbo_line(), frozenset({"2"}), realm_id="realm-1"
        )
    line_svc.update_by_public_id.assert_called_once()
    return line_svc.update_by_public_id.call_args.kwargs, seq


class _LineGatedExpenseService:
    """read_by_id reports `draft` until the LINE has been read, then `in_review`: the parent enters
    review exactly at the line-read boundary, so only a status read taken AFTER the line read sees it."""

    def __init__(self):
        self.line_read = False
        self.calls = 0

    def read_by_id(self, expense_id):
        self.calls += 1
        return SimpleNamespace(
            status="in_review" if self.line_read else "draft", qbo_id="P-1", public_id="exp-pub"
        )


def test_parent_entering_review_at_the_line_read_boundary_keeps_local_coding():
    """Kills (r2 Codex P2 / twin P3): a deciding status read hoisted BEFORE the line read - the fake flips
    to in_review only when `read_by_qbo_identity` runs, so an earlier read sees draft and writes QBO's
    coding. The same `_apply_line_fields` serves the HIT and readopt paths, so one boundary pin covers both."""
    direct = _local_line()
    gated = _LineGatedExpenseService()
    connector = PurchaseLineExpenseLineItemConnector()
    line_svc = Mock()
    line_svc.read_by_expense_id.return_value = [direct]

    def _read_line(*args, **kwargs):
        gated.line_read = True
        return direct

    line_svc.read_by_qbo_identity.side_effect = _read_line
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=direct.id, public_id=direct.public_id)
    connector.expense_line_item_service = line_svc
    connector.sub_cost_code_service = Mock()
    connector.sub_cost_code_service.is_holding_code.return_value = False
    connector.expense_service = gated
    connector.reconciliation_repo = Mock()
    connector.project_service = Mock()
    connector.project_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=77, public_id=QBO_PROJECT, qbo_id="cust-1", realm_id="realm-1"
    )
    with patch(
        f"{_MODULE}.resolve_dbo_sub_cost_code", return_value=SimpleNamespace(id=QBO_SCC)
    ), patch(f"{_MODULE}.verify_identity_dbo_only", return_value="cust-1"):
        connector.sync_from_qbo_purchase_line(
            10, "exp-pub", _qbo_line(), frozenset({"2"}), realm_id="realm-1"
        )
    kwargs = line_svc.update_by_public_id.call_args.kwargs
    assert gated.calls >= 2 and gated.line_read
    assert kwargs["sub_cost_code_id"] == LOCAL_SCC
    assert kwargs["project_public_id"] is None
    assert kwargs["description"] == LOCAL_DESCRIPTION


def test_late_read_decides_when_review_ended_after_the_early_read():
    """Pins that the LATE read decides: in_review early, draft late -> QBO coding written."""
    kwargs, _ = _drive_sequenced("in_review", "draft")
    assert kwargs["sub_cost_code_id"] == QBO_SCC
    assert kwargs["project_public_id"] == QBO_PROJECT
    assert kwargs["description"] == QBO_DESCRIPTION


def test_late_read_unreadable_parent_behaves_as_today():
    """Kills: a crash when the late parent read returns None."""
    class _Gone(_SequencedExpenseService):
        def read_by_id(self, expense_id):
            self.calls += 1
            return SimpleNamespace(status="in_review", qbo_id="P-1", public_id="exp-pub") if self.calls == 1 else None

    kwargs, _ = _drive_sequenced(seq=_Gone())
    assert kwargs["sub_cost_code_id"] == QBO_SCC


# ---- P3: the preserve log only fires when a non-None value was actually kept ----


def test_approved_holding_with_uncoded_local_line_logs_no_preserve(caplog):
    """Kills: `preserved` set on the approved+holding branch for a None local code."""
    direct = _local_line(sub_cost_code_id=None, project_id=None, description="")
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        kwargs, recorder = _drive("approved", direct, holding=True)
    assert kwargs["sub_cost_code_id"] is None
    recorder.assert_called_once()  # the holding issue is still recorded
    assert not any(
        "local_coding_preserved_under_review" in r.getMessage() for r in caplog.records
    )


def test_approved_holding_with_coded_local_line_still_logs_preserve(caplog):
    """Kills: the approved+holding branch's `preserved` flag set to False (r2 Codex P3: the line carries
    NO project and an EMPTY description, so only the kept sub cost code can make the log fire)."""
    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _drive("approved", _local_line(project_id=None, description=""), holding=True)
    assert any(
        "local_coding_preserved_under_review" in r.getMessage() for r in caplog.records
    )
