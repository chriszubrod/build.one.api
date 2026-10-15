"""U-632 bite 1 — holding / suspense sub cost code gates (pure, no DB)."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.api.errors import ErrorCode
from shared.lifecycle.completion_gate import HoldingCodeRefused
from shared.lifecycle.terminal_lock import reraise_if_sproc_holding_code


def test_is_holding_code_none_is_false():
    from entities.sub_cost_code.business.service import SubCostCodeService

    svc = SubCostCodeService(repo=MagicMock())
    assert svc.is_holding_code(None) is False


def test_is_holding_code_uses_holding_id_set():
    from entities.sub_cost_code.business.service import SubCostCodeService

    repo = MagicMock()
    repo.read_holding_sub_cost_code_ids.return_value = [99, 100]
    svc = SubCostCodeService(repo=repo)
    assert svc.is_holding_code(99) is True
    assert svc.is_holding_code(100) is True
    assert svc.is_holding_code(1) is False
    repo.read_holding_sub_cost_code_ids.assert_called_once()


def test_holding_sub_cost_code_ids_memoized_per_service_instance():
    from entities.sub_cost_code.business.service import SubCostCodeService

    repo = MagicMock()
    repo.read_holding_sub_cost_code_ids.return_value = [42]
    svc = SubCostCodeService(repo=repo)
    assert svc.is_holding_code(42) is True
    assert svc.is_holding_code(42) is True
    repo.read_holding_sub_cost_code_ids.assert_called_once()


def test_reraise_if_sproc_holding_code_maps_token():
    with pytest.raises(HoldingCodeRefused) as exc:
        reraise_if_sproc_holding_code(
            Exception("[42000] HOLDING_CODE: blocked in sproc."),
            what="This bill cannot be completed while any line remains on the suspense sub cost code. Recode those lines first.",
        )
    assert exc.value.status_code == 422
    assert exc.value.error_code == ErrorCode.HOLDING_CODE


def test_reraise_if_sproc_holding_code_leaves_other_errors():
    reraise_if_sproc_holding_code(Exception("deadlock victim"), what="ignored")


def _make_bill_service(*, lines):
    from entities.bill.business.service import BillService

    repo = MagicMock()
    repo.finalize_by_id.return_value = SimpleNamespace(id=1, is_draft=False, public_id="pub-1")
    repo.read_by_bill_number_and_vendor_id.return_value = None
    svc = BillService(repo=repo)
    bill = SimpleNamespace(
        id=1,
        public_id="pub-1",
        vendor_id=2,
        bill_number="B-1",
        bill_date="2026-10-01",
        is_draft=True,
    )
    svc.read_by_public_id = MagicMock(return_value=bill)
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="v-pub")
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = lines
    svc._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))
    return svc, repo


def test_complete_bill_refuses_holding_before_finalize():
    lines = [SimpleNamespace(sub_cost_code_id=10)]
    svc, repo = _make_bill_service(lines=lines)
    with patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused) as exc:
            svc.complete_bill("pub-1")
    assert exc.value.error_code == ErrorCode.HOLDING_CODE
    repo.finalize_by_id.assert_not_called()


def test_assert_no_holding_allows_non_holding_lines():
    lines = [SimpleNamespace(sub_cost_code_id=10)]
    svc, _repo = _make_bill_service(lines=lines)
    with patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=False,
    ):
        svc._assert_no_holding_code_on_complete(1)


def test_complete_expense_refuses_holding():
    from entities.expense.business.service import ExpenseService

    repo = MagicMock()
    svc = ExpenseService(repo=repo)
    expense = SimpleNamespace(id=3, public_id="exp-1", vendor_id=1, is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=expense)
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="v")
    eli_svc = MagicMock()
    eli_svc.read_by_expense_id.return_value = [
        SimpleNamespace(sub_cost_code_id=5, is_draft=False)
    ]
    svc._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))
    svc._expense_line_item_service = eli_svc
    with patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused) as exc:
            svc.complete_expense("exp-1")
    assert exc.value.error_code == ErrorCode.HOLDING_CODE
    repo.finalize_by_id.assert_not_called()


def test_complete_bill_credit_refuses_holding():
    from entities.bill_credit.business.complete_service import BillCreditCompleteService

    svc = BillCreditCompleteService()
    bc = SimpleNamespace(id=8, public_id="bc-1", vendor_id=1, row_version="x", is_draft=True)
    svc.bill_credit_service = MagicMock()
    svc.bill_credit_service.read_by_public_id.return_value = bc
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="v")
    svc.bill_credit_line_item_service = MagicMock()
    svc.bill_credit_line_item_service.read_by_bill_credit_id.return_value = [
        SimpleNamespace(sub_cost_code_id=1)
    ]
    with patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused):
            svc.complete_bill_credit("bc-1")
    svc.bill_credit_service.update_by_public_id.assert_not_called()


def test_complete_invoice_refuses_holding_on_source_line():
    from entities.invoice.business.service import InvoiceService

    svc = InvoiceService()
    inv = SimpleNamespace(id=4, public_id="inv-1", project_id=1, payment_term_id=None, is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=inv)
    ili = SimpleNamespace(
        sub_cost_code_id=None,
        source_type="BillLineItem",
        bill_line_item_id=99,
        expense_line_item_id=None,
        bill_credit_line_item_id=None,
    )
    svc.invoice_line_item_service = MagicMock()
    svc.invoice_line_item_service.read_by_invoice_id.return_value = [ili]
    svc.update_by_public_id = MagicMock()
    with patch(
        "entities.bill_line_item.business.service.BillLineItemService.read_by_id",
        return_value=SimpleNamespace(sub_cost_code_id=7),
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused) as exc:
            svc.complete_invoice("inv-1")
    assert exc.value.error_code == ErrorCode.HOLDING_CODE
    svc.update_by_public_id.assert_not_called()


def test_resolve_qbo_item_ref_raises_for_holding_unchanged_for_normal():
    from integrations.intuit.qbo.base.cost_code_resolver import QboItemRef, resolve_qbo_item_ref

    holding = SimpleNamespace(id=1, qbo_id="9", realm_id="r1", name="Suspense", is_holding_code=True)
    normal = SimpleNamespace(id=2, qbo_id="10", realm_id="r1", name="Concrete", is_holding_code=False)
    scc = MagicMock()
    scc.read_by_id.side_effect = lambda id: holding if id == 1 else normal

    with pytest.raises(HoldingCodeRefused):
        resolve_qbo_item_ref(1, "r1", sub_cost_code_service=scc)

    assert resolve_qbo_item_ref(2, "r1", sub_cost_code_service=scc) == QboItemRef(
        value="10", name="Concrete"
    )


def test_bill_push_get_qbo_item_ref_propagates_holding():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector(sub_cost_code_service=MagicMock())
    connector.sub_cost_code_service.read_by_id.return_value = SimpleNamespace(
        id=7, qbo_id="9", realm_id="r1", name="00.02", is_holding_code=True
    )
    with pytest.raises(HoldingCodeRefused):
        connector._get_qbo_item_ref(7, "r1")


def test_invoice_build_skips_none_but_raises_on_holding():
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    connector = InvoiceInvoiceConnector(sub_cost_code_service=MagicMock())
    connector.sub_cost_code_service.read_by_id.return_value = SimpleNamespace(
        id=3, qbo_id="1", realm_id="r1", name="00.02", is_holding_code=True
    )
    line = SimpleNamespace(
        id=1,
        source_type="Manual",
        sub_cost_code_id=3,
        bill_line_item_id=None,
        expense_line_item_id=None,
        bill_credit_line_item_id=None,
        price=Decimal("10"),
        amount=Decimal("10"),
        quantity=None,
        rate=None,
    )

    with pytest.raises(HoldingCodeRefused):
        connector._get_qbo_item_ref_for_line(line, "r1")


def test_coding_confirm_refuses_holding_with_zero_writes(monkeypatch):
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(return_value=SimpleNamespace(realm_id="r1", qbo_purchase_id=1))
    record = MagicMock()
    svc.record_confirmation = record
    monkeypatch.setattr(
        "integrations.intuit.qbo.base.client.recode_write_gate_reason",
        lambda: None,
    )

    def _resolve(sub_cost_code_id, realm_id=None, **kwargs):
        if sub_cost_code_id == 99:
            raise HoldingCodeRefused("holding")
        return SimpleNamespace(value="ITEM-X", name="Item X")

    monkeypatch.setattr(
        "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref",
        _resolve,
    )
    with pytest.raises(HoldingCodeRefused):
        svc.confirm(
            public_id="x",
            project_id=1,
            sub_cost_code_id=99,
            description="d",
            was_overridden=False,
            user_id=1,
        )
    record.assert_not_called()


def test_suggestion_engine_treats_holding_as_no_suggestion():
    from entities.coding_suggestion.business.suggestion_service import CodingSuggestionEngine

    engine = CodingSuggestionEngine(
        sub_cost_code_service=MagicMock(),
        coding_item_service=MagicMock(),
        suggestion_repo=MagicMock(),
        project_service=MagicMock(),
    )
    engine.sub_cost_code_service.is_holding_code.return_value = True
    engine.suggestion_repo.read_vendor_dominant_sub_cost_code.return_value = {
        "sub_cost_code_id": 5,
        "number": "00.02",
        "top_count": 3,
        "total_count": 3,
    }
    item = SimpleNamespace(private_note="", vendor_id=1, memo_text=None)
    result = engine.suggest_for_item(item)
    assert result["sub_cost_code_id"] is None
    assert result["status"] == "flagged"


def test_bill_apply_reviewer_decision_refuses_holding_approval():
    from entities.bill.business.service import BillService
    from tests.conftest import actor_context

    svc = BillService(repo=MagicMock())
    bill = SimpleNamespace(id=1, public_id="b1", is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=bill)
    with actor_context(5, False), patch(
        "entities.review.business.recipient_service.ReviewRecipientService"
    ) as rr, patch(
        "shared.authz.provenance.bind_reviewer_reply_to_review_thread",
        return_value=SimpleNamespace(id=1),
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.read_by_public_id",
        return_value=SimpleNamespace(id=10, is_holding_code=True),
    ):
        rr.return_value.resolve_for_bill.return_value = {
            "to": [SimpleNamespace(email="pm@x.com", user_id=5)],
            "cc": [],
        }
        with pytest.raises(HoldingCodeRefused):
            svc.apply_reviewer_decision(
                bill_public_id="b1",
                decision="approved",
                reviewer_email="pm@x.com",
                sub_cost_code_public_id="scc-pub",
            )


def test_bill_apply_reviewer_decision_skips_holding_check_on_decline():
    """Declined decisions never load an SCC — holding gate applies only to approval."""
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    source = open(svc.apply_reviewer_decision.__code__.co_filename).read()
    idx = source.index("if decision == \"approved\":")
    block = source[idx : source.index("comments = ", idx)]
    assert "is_holding_code" in block
    assert "decision == \"rejected\"" not in block.split("if decision == \"approved\":")[0]


def test_expense_apply_reviewer_decision_refuses_holding_approval():
    from entities.expense.business.service import ExpenseService
    from tests.conftest import actor_context

    svc = ExpenseService(repo=MagicMock())
    expense = SimpleNamespace(id=2, public_id="e1", is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=expense)
    with actor_context(5, False), patch(
        "entities.review.business.recipient_service.ReviewRecipientService"
    ) as rr, patch(
        "shared.authz.provenance.bind_reviewer_reply_to_review_thread",
        return_value=SimpleNamespace(id=1),
    ), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService.read_by_expense_id",
        return_value=[SimpleNamespace(row_version="rv")],
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.read_by_public_id",
        return_value=SimpleNamespace(id=10, is_holding_code=True),
    ):
        rr.return_value.resolve_for_expense.return_value = {
            "to": [SimpleNamespace(email="pm@x.com", user_id=5)],
            "cc": [],
        }
        with pytest.raises(HoldingCodeRefused):
            svc.apply_reviewer_decision(
                expense_public_id="e1",
                decision="approved",
                reviewer_email="pm@x.com",
                sub_cost_code_public_id="scc-pub",
            )


def test_cl_mark_as_ready_treats_holding_like_missing():
    from entities.contract_labor.business.service import ContractLaborService

    svc = ContractLaborService()
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=1,
            vendor_id=1,
            sub_cost_code_id=8,
            hourly_rate=1,
            total_hours=1,
        )
    )
    with patch(
        "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository.read_by_contract_labor_id",
        return_value=[SimpleNamespace(sub_cost_code_id=8)],
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused, match="00.02"):
            svc.mark_as_ready("cl-1")


def test_cl_mark_ready_via_review_defers_when_line_is_holding():
    from entities.contract_labor.business.service import ContractLaborService

    svc = ContractLaborService()
    cl = SimpleNamespace(id=1, status="submitted")
    svc.repo = MagicMock()
    svc.repo.read_by_id.return_value = cl
    line = SimpleNamespace(project_id=2, sub_cost_code_id=9)
    with patch(
        "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository.read_by_contract_labor_id",
        return_value=[line],
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        result = svc.mark_as_ready_via_review_approval(contract_labor_id=1)
    assert result is cl
    svc.repo.update_by_id.assert_not_called()


def test_ili_create_refuses_holding_on_manual_line():
    from entities.invoice_line_item.business.service import InvoiceLineItemService

    svc = InvoiceLineItemService(repo=MagicMock())
    with patch(
        "entities.invoice.business.service.InvoiceService.read_by_public_id",
        return_value=SimpleNamespace(id=1),
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused):
            svc.create(
                invoice_public_id="inv-1",
                source_type="Manual",
                sub_cost_code_id=4,
                amount=Decimal("1"),
            )
    svc.repo.create.assert_not_called()


def test_qbo_bill_create_born_draft_when_holding_line():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
    from integrations.intuit.qbo.bill.business.model import QboBill, QboBillLine

    connector = BillBillConnector(
        bill_service=MagicMock(),
        sub_cost_code_service=MagicMock(),
    )
    connector.sub_cost_code_service.read_by_qbo_identity.return_value = SimpleNamespace(
        is_holding_code=True
    )
    qbo_bill = SimpleNamespace(id=1, realm_id="r1")
    lines = [SimpleNamespace(item_ref_value="item-1")]
    connector._create_bill(
        qbo_bill=qbo_bill,
        qbo_bill_lines=lines,
        vendor_public_id="v",
        bill_number="n",
        bill_date="2026-01-01",
        due_date="2026-01-02",
        memo=None,
        total_amount=1,
    )
    assert connector.bill_service.create.call_args.kwargs["is_draft"] is True


def test_qbo_bill_pull_update_path_detects_holding_on_completed_bill():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    connector = BillBillConnector(
        bill_service=MagicMock(),
        reconciliation_repo=MagicMock(),
        sub_cost_code_service=MagicMock(),
    )
    connector.sub_cost_code_service.read_by_qbo_identity.return_value = SimpleNamespace(
        is_holding_code=True
    )
    lines = [SimpleNamespace(item_ref_value="item-1")]
    assert connector._qbo_bill_lines_include_holding(lines, "r1") is True


def test_get_billable_ready_queries_exclude_holding_scc():
    from entities.invoice.business.service import (
        BILLABLE_READY_BILL_CREDIT_LINE_ITEMS_SQL,
        BILLABLE_READY_BILL_LINE_ITEMS_SQL,
        BILLABLE_READY_EXPENSE_LINE_ITEMS_SQL,
    )

    for sql in (
        BILLABLE_READY_BILL_LINE_ITEMS_SQL,
        BILLABLE_READY_EXPENSE_LINE_ITEMS_SQL,
        BILLABLE_READY_BILL_CREDIT_LINE_ITEMS_SQL,
    ):
        assert "IsHoldingCode" in sql


def test_purchase_initial_status_draft_for_holding_line():
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        PurchaseExpenseConnector,
    )

    connector = PurchaseExpenseConnector(sub_cost_code_service=MagicMock())
    connector.sub_cost_code_service.read_by_qbo_identity.return_value = SimpleNamespace(
        is_holding_code=True
    )
    line = SimpleNamespace(item_ref_value="x", account_ref_name="Materials")
    assert connector._initial_status_for_purchase_lines([line], "r1") == "draft"
