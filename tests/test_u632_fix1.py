"""U-632 fix round 1 — regression tests (pure, no DB)."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.api.errors import ErrorCode
from shared.lifecycle.completion_gate import HoldingCodeRefused


def test_transactional_reviewer_decision_refuses_holding_scc_before_write():
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    bill = SimpleNamespace(id=1, public_id="b1", is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=bill)
    line = SimpleNamespace(id=10, row_version="rv", sub_cost_code_id=2)
    bli = MagicMock()
    bli.read_by_bill_id.return_value = [line]
    with patch(
        "entities.bill_line_item.business.service.BillLineItemService",
        return_value=bli,
    ), patch(
        "entities.review.business.recipient_service.ReviewRecipientService"
    ) as rr, patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.read_by_public_id",
        return_value=SimpleNamespace(id=99, is_holding_code=True),
    ):
        rr.return_value.resolve_for_bill.return_value = {
            "to": [SimpleNamespace(email="pm@x.com", user_id=5)],
            "cc": [],
        }
        with pytest.raises(HoldingCodeRefused):
            svc.apply_transactional_reviewer_decision(
                bill_public_id="b1",
                decision="approved",
                sub_cost_code_public_id="scc-pub",
                description=None,
                idempotency_key="k1",
                line_row_version="rv",
                expected_review_public_id=None,
                caller_user_id=5,
            )
    bli.update_coding_in_transaction.assert_not_called()


def test_apply_reviewer_decision_refuses_when_other_line_stays_on_holding():
    from entities.bill.business.service import BillService
    from tests.conftest import actor_context

    svc = BillService(repo=MagicMock())
    bill = SimpleNamespace(id=1, public_id="b1", is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=bill)
    line_a = SimpleNamespace(id=1, row_version="rv1", sub_cost_code_id=5)
    line_b = SimpleNamespace(id=2, row_version="rv2", sub_cost_code_id=8)
    with actor_context(5, False), patch(
        "entities.review.business.recipient_service.ReviewRecipientService"
    ) as rr, patch(
        "shared.authz.provenance.bind_reviewer_reply_to_review_thread",
        return_value=SimpleNamespace(id=1),
    ), patch(
        "entities.bill_line_item.business.service.BillLineItemService"
    ) as bli_cls, patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.read_by_public_id",
        return_value=SimpleNamespace(id=10, is_holding_code=False),
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        side_effect=lambda sid: sid == 8,
    ):
        bli_cls.return_value.read_by_bill_id.return_value = [line_a, line_b]
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


def test_review_fast_path_refuses_holding_lines_on_bill():
    from entities.review.business.model import ParentType
    from entities.review.business.service import ReviewService

    svc = ReviewService()
    bill = SimpleNamespace(id=3, public_id="b3", is_draft=True, status="submitted")
    with patch.object(svc, "_resolve_parent", return_value=bill), patch.object(
        svc, "_get_current_by_id",
        return_value=SimpleNamespace(review_kind="in_review"),
    ), patch.object(
        svc.review_status_service,
        "get_approved_status",
        return_value=SimpleNamespace(id=1, name="approved"),
    ), patch(
        "entities.sub_cost_code.business.holding_lines.parent_has_holding_line",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused):
            svc.build_fast_path_approval_payload(
                parent_type=ParentType.BILL,
                parent_public_id="b3",
                user_id=1,
            )


def test_complete_bill_router_refuses_holding_before_enqueue():
    from entities.bill.api.router import complete_bill_router
    from fastapi import BackgroundTasks
    from shared.api.errors import ApiError

    bill = SimpleNamespace(id=1, public_id="b1", is_draft=True)
    svc = MagicMock()
    svc.read_by_public_id.return_value = bill
    with patch("entities.bill.api.router.BillService", return_value=svc), patch(
        "entities.bill.api.router.gate_completion",
    ), patch(
        "entities.completion_job.business.service.CompletionJobService"
    ) as job_cls:
        svc._assert_no_holding_code_on_complete.side_effect = HoldingCodeRefused("x")
        with pytest.raises(ApiError) as exc:
            complete_bill_router(
                public_id="b1",
                background_tasks=BackgroundTasks(),
                current_user={"sub": "u"},
            )
        job_cls.return_value.enqueue.assert_not_called()
        assert exc.value.error_code == ErrorCode.HOLDING_CODE


def test_cl_crew_apply_reviewer_decision_surfaces_holding_as_api_error():
    from entities.contract_labor.api.router import apply_reviewer_decision_router
    from shared.api.errors import ApiError

    with patch(
        "entities.contract_labor.api.router.ContractLaborService"
    ) as cl_svc:
        cl_svc.return_value.apply_reviewer_decision.side_effect = HoldingCodeRefused(
            "holding"
        )
        with pytest.raises(ApiError) as exc:
            apply_reviewer_decision_router(body={}, current_user={"sub": "u"})
        assert exc.value.error_code == ErrorCode.HOLDING_CODE


def test_qbo_worker_dead_letters_holding_code_refused_immediately():
    from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker

    worker = QboOutboxWorker(repo=MagicMock())
    row = SimpleNamespace(
        id=1,
        public_id="p",
        row_version=b"rv",
        kind="push_bill",
        entity_type="Bill",
        entity_public_id="b",
        realm_id="r",
        correlation_id=None,
        request_id="req",
        attempts=0,
    )
    worker._dispatch_table = {"push_bill": MagicMock(side_effect=HoldingCodeRefused("nope"))}
    with patch.object(worker, "_dead_letter") as dl:
        worker._process_inner(row)
        dl.assert_called_once()
        assert "HoldingCodeRefused" in dl.call_args[0][1]


def test_invoice_complete_refuses_when_only_source_line_is_holding():
    from entities.invoice.business.service import InvoiceService

    svc = InvoiceService()
    inv = SimpleNamespace(id=4, public_id="inv-1", project_id=1, payment_term_id=None, is_draft=True)
    svc.read_by_public_id = MagicMock(return_value=inv)
    ili = SimpleNamespace(
        sub_cost_code_id=20,
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
        side_effect=lambda sid: sid == 7,
    ):
        with pytest.raises(HoldingCodeRefused):
            svc.complete_invoice("inv-1")
    svc.update_by_public_id.assert_not_called()


def test_coding_confirm_multi_line_refuses_when_any_expense_line_is_holding():
    from entities.coding_suggestion.business.service import CodingSuggestionService

    svc = CodingSuggestionService()
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            realm_id="r1",
            qbo_purchase_id=1,
            qbo_purchase_qbo_id="p1",
            expense_id=9,
        )
    )
    svc._count_uncategorized_purchase_lines = MagicMock(return_value=2)
    svc._uncategorized_staging_qbo_line_ids = MagicMock(return_value=["L1", "L2"])
    svc.record_confirmation = MagicMock()
    eli_svc = MagicMock()
    eli_svc.read_by_expense_id.return_value = [
        SimpleNamespace(id=100, sub_cost_code_id=1),
        SimpleNamespace(id=101, sub_cost_code_id=1),
        SimpleNamespace(id=102, sub_cost_code_id=2),
    ]
    eli_svc.read_by_qbo_identity.side_effect = [
        SimpleNamespace(id=100, sub_cost_code_id=1),
        SimpleNamespace(id=101, sub_cost_code_id=2),
    ]
    with patch(
        "integrations.intuit.qbo.base.client.recode_write_gate_reason",
        return_value=None,
    ), patch(
        "integrations.intuit.qbo.base.cost_code_resolver.resolve_qbo_item_ref",
        return_value=SimpleNamespace(value="x", name="y"),
    ), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        return_value=eli_svc,
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        side_effect=lambda sid: sid == 2,
    ):
        with pytest.raises(HoldingCodeRefused):
            svc.confirm(
                public_id="x",
                project_id=1,
                sub_cost_code_id=10,
                description="d",
                was_overridden=False,
                user_id=1,
                allow_multi_line=True,
            )
    svc.record_confirmation.assert_not_called()
