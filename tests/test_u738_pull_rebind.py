"""U-738: unambiguous leftover rebind on QBO Purchase line-id regeneration (recode)."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
    classify_purchase_line_leftover_rebind,
)
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import (
    PurchaseLineExpenseLineItemConnector,
)
from integrations.intuit.qbo.purchase.connector.recode_leftover import pull_is_billed

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


def _make_qbo_purchase_line(**overrides):
    defaults = dict(
        id=42,
        qbo_purchase_id=4,
        qbo_line_id="2",
        description="Coded materials",
        amount=Decimal("500"),
        qty=Decimal("1"),
        unit_price=Decimal("500"),
        markup_percent=None,
        billable_status="Billable",
        customer_ref_value="cust-1",
        item_ref_value="item-99",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _stamped_row(row_id, qbo_id, **extra):
    base = dict(
        id=row_id,
        public_id=f"pub-{row_id}",
        row_version=b"rv",
        qbo_id=qbo_id,
        realm_id="realm-1",
        quantity=Decimal("1"),
        rate=Decimal("500"),
        amount=Decimal("500"),
        description="58999 suspense",
        sub_cost_code_id=None,
        markup=None,
        billable_decided_at=None,
        is_billed=False,
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _build_line_connector():
    connector = PurchaseLineExpenseLineItemConnector()
    line_svc = Mock()
    line_svc.read_by_expense_id.return_value = []
    line_svc.read_by_qbo_identity.return_value = None
    connector.expense_line_item_service = line_svc
    connector.sub_cost_code_service = Mock()
    connector.sub_cost_code_service.is_holding_code.return_value = False
    connector.project_service = Mock()
    connector.project_service.read_by_qbo_identity.return_value = None
    connector.expense_service = Mock()
    connector.expense_service.read_by_id.return_value = SimpleNamespace(
        status="draft", qbo_id="P-1", public_id="exp-pub"
    )
    connector.reconciliation_repo = Mock()
    return connector, line_svc


def test_classify_unambiguous_single_leftover_and_unbound_live():
    leftover = SimpleNamespace(qbo_id="1", amount=Decimal("500"), sub_cost_code_id=None)
    live = frozenset({"2"})
    unambiguous, ambiguous, reason = classify_purchase_line_leftover_rebind(
        [leftover], live, unbound_live_line_amount=Decimal("500")
    )
    assert ambiguous is False
    assert reason is None
    assert unambiguous is leftover


def test_classify_amount_mismatch_is_ambiguous():
    leftover = SimpleNamespace(qbo_id="1", amount=Decimal("1"), sub_cost_code_id=None)
    unambiguous, ambiguous, reason = classify_purchase_line_leftover_rebind(
        [leftover], frozenset({"2"}), unbound_live_line_amount=Decimal("999")
    )
    assert unambiguous is None
    assert ambiguous is True
    assert reason == "amount_mismatch"


def test_recode_rebinds_stale_line_without_creating_sibling():
    """58999→item recode: new Line.Id + new content — fingerprint readopt misses, U-738 rebinds."""
    connector, line_svc = _build_line_connector()
    stale = _stamped_row(10, "1", description="58999 suspense", sub_cost_code_id=None)
    line_svc.read_by_expense_id.return_value = [stale]
    line_svc.update_by_public_id.return_value = SimpleNamespace(id=10, public_id="pub-10")
    line_svc.read_by_id.return_value = _stamped_row(10, "2", sub_cost_code_id=55)

    qbo_line = _make_qbo_purchase_line(qbo_line_id="2", description="Coded materials")

    with patch(
        "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service.resolve_dbo_sub_cost_code",
        return_value=SimpleNamespace(id=55),
    ):
        result = connector.sync_from_qbo_purchase_line(
            99,
            "exp-pub",
            qbo_line,
            frozenset({"2"}),
            realm_id="realm-1",
            u738_rebind_target_id=10,
        )

    line_svc.create.assert_not_called()
    line_svc.update_by_public_id.assert_called_once()
    line_svc.repo.set_qbo_identity.assert_called_once_with(id=10, qbo_id="2", realm_id="realm-1")
    assert result.id == 10


def test_fingerprint_mismatch_without_rebind_target_still_creates():
    connector, line_svc = _build_line_connector()
    stale = _stamped_row(10, "1", description="old", amount=Decimal("1"))
    line_svc.read_by_expense_id.return_value = [stale]
    line_svc.create.return_value = SimpleNamespace(id=77, public_id="pub-77")
    line_svc.read_by_id.return_value = _stamped_row(77, "2")

    qbo_line = _make_qbo_purchase_line(qbo_line_id="2", description="different", amount=Decimal("999"))

    connector.sync_from_qbo_purchase_line(
        99, "exp-pub", qbo_line, frozenset({"1", "2"}), realm_id="realm-1", u738_rebind_target_id=None
    )

    line_svc.create.assert_called_once()


def test_pull_is_billed_preserves_when_invoice_linked_and_qbo_billable():
    direct = SimpleNamespace(is_billed=True, billable_decided_at=None)
    assert pull_is_billed(direct, False, has_invoice_ref=True) is None
    assert pull_is_billed(direct, True, has_invoice_ref=True) is True


def test_sync_line_items_passes_rebind_target_for_unbound_line_only():
    header = PurchaseExpenseConnector(expense_service=Mock())
    line_connector = Mock()
    stale = _stamped_row(10, "1", amount=Decimal("500"))
    line_connector.expense_line_item_service.read_by_expense_id.return_value = [stale]
    seen_targets = []

    def _sync(expense_id, expense_public_id, line, live_ids, realm_id=None, u738_rebind_target_id=None):
        seen_targets.append(u738_rebind_target_id)

    line_connector.sync_from_qbo_purchase_line.side_effect = _sync
    header._line_connector = line_connector

    line_connector.expense_line_item_service.read_invoice_linked_line_ids_for_expense.return_value = (
        frozenset()
    )
    line_connector.expense_line_item_service.read_by_expense_id.return_value = [stale]
    lines = [_make_qbo_purchase_line(qbo_line_id="2", amount=Decimal("500"))]
    header._sync_line_items(10, "exp-pub", lines, "realm-1")

    assert seen_targets == [10]


def test_sync_line_items_logs_ambiguous_leftover_with_reason(caplog):
    header = PurchaseExpenseConnector(expense_service=Mock())
    line_connector = Mock()
    line_connector.expense_line_item_service.read_by_expense_id.return_value = [
        _stamped_row(10, "1", amount=Decimal("1")),
        _stamped_row(11, "3", amount=Decimal("1")),
    ]
    line_connector.sync_from_qbo_purchase_line.return_value = None
    header._line_connector = line_connector

    lines = [
        _make_qbo_purchase_line(qbo_line_id="2"),
        _make_qbo_purchase_line(qbo_line_id="4"),
    ]
    line_connector.expense_line_item_service.read_invoice_linked_line_ids_for_expense.return_value = (
        frozenset()
    )
    with caplog.at_level("WARNING"):
        header._sync_line_items(10, "exp-pub", lines, "realm-1")

    assert any("qbo.purchase.line_leftover_ambiguous" in r.message for r in caplog.records)
