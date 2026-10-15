"""U-632 fix round 2 — regression tests (pure unless noted)."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.api.errors import ApiError, ErrorCode
from shared.lifecycle.completion_gate import HoldingCodeRefused


def test_g1_cl_bill_put_refuses_holding_before_any_repo_write():
    from entities.contract_labor.api.router import update_contract_labor_bill

    entry = SimpleNamespace(id=1, public_id="cl-1", status="pending_review", sub_cost_code_id=None)
    repo = MagicMock()
    line_repo = MagicMock()
    with patch(
        "entities.contract_labor.api.router.ContractLaborService"
    ) as cl_cls, patch(
        "entities.contract_labor.api.router.ContractLaborLineItemRepository",
        return_value=line_repo,
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        cl_cls.return_value.read_by_public_id.return_value = entry
        cl_cls.return_value.repo = repo
        body = SimpleNamespace(
            row_version="AAAA",
            bill_vendor_id=1,
            bill_date="2026-01-01",
            due_date="2026-01-02",
            bill_number="n",
            status="ready",
            line_items=[SimpleNamespace(id=1, row_version="AAAA", sub_cost_code_id=9)],
        )
        with pytest.raises(HoldingCodeRefused):
            update_contract_labor_bill(
                public_id="cl-1",
                bill_update=body,
                current_user={"sub": "u"},
            )
    repo.update_bill_info.assert_not_called()
    line_repo.update_by_id.assert_not_called()
    line_repo.create.assert_not_called()
    line_repo.delete_by_id.assert_not_called()


def test_g2_bill_line_pull_keeps_local_scc_on_approved_parent():
    from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import (
        BillLineItemConnector,
    )
    from integrations.intuit.qbo.bill.business.model import QboBillLine

    bill = SimpleNamespace(
        id=5,
        public_id="b-pub",
        status="approved",
        qbo_id="qb-5",
    )
    bli_svc = MagicMock()
    direct = SimpleNamespace(
        id=10,
        public_id="li-pub",
        row_version="rv",
        sub_cost_code_id=42,
        realm_id="r1",
    )
    bli_svc.update_by_public_id.return_value = direct
    holding_scc = SimpleNamespace(id=99, is_holding_code=True)
    connector = BillLineItemConnector(
        bill_line_item_service=bli_svc,
        bill_service=MagicMock(read_by_id=MagicMock(return_value=bill)),
        sub_cost_code_service=MagicMock(),
        reconciliation_repo=MagicMock(),
    )
    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="L1",
        item_ref_value="item-1",
        customer_ref_value=None,
        description="d",
        amount=Decimal("1"),
        qty=None,
        unit_price=None,
        markup_percent=None,
        billable_status=None,
    )
    with patch.object(
        connector,
        "_get_project_public_id",
        return_value=None,
    ), patch(
        "integrations.intuit.qbo.bill.connector.bill_line_item.business.service.resolve_dbo_sub_cost_code",
        return_value=holding_scc,
    ), patch(
        "integrations.intuit.qbo.bill.connector.bill_line_item.business.service.run_line_identity_fastpath_dbo_only",
        side_effect=lambda **kw: SimpleNamespace(entity=kw["apply_fields"](direct)),
    ):
        connector.sync_from_qbo_bill_line(
            bill_id=5,
            qbo_bill_line=qbo_line,
            live_qbo_line_ids=frozenset({"L1"}),
            realm_id="r1",
        )
    assert bli_svc.update_by_public_id.call_args.kwargs["sub_cost_code_id"] == 42
    connector.reconciliation_repo.read_unresolved_issue_keys_by_drift_type.assert_called()


def test_g4_coding_confirm_ignores_holding_on_line_being_recoded():
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
    svc._count_uncategorized_purchase_lines = MagicMock(return_value=1)
    svc._uncategorized_staging_qbo_line_ids = MagicMock(return_value=["QL1"])
    svc.record_confirmation = MagicMock()
    recoded = SimpleNamespace(id=100, sub_cost_code_id=1)
    other = SimpleNamespace(id=101, sub_cost_code_id=2)
    eli_svc = MagicMock()
    eli_svc.read_by_expense_id.return_value = [recoded, other]
    eli_svc.read_by_qbo_identity.return_value = recoded
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
    ), patch(
        "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
    ) as outbox:
        outbox.return_value.enqueue = MagicMock()
        svc.mark_enqueued = MagicMock()
        with pytest.raises(HoldingCodeRefused):
            svc.confirm(
                public_id="x",
                project_id=1,
                sub_cost_code_id=10,
                description="d",
                was_overridden=False,
                user_id=1,
            )
    svc.record_confirmation.assert_not_called()


def test_g4_coding_confirm_allows_recode_when_only_target_is_holding():
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
    svc._count_uncategorized_purchase_lines = MagicMock(return_value=1)
    svc._uncategorized_staging_qbo_line_ids = MagicMock(return_value=["QL1"])
    recoded = SimpleNamespace(id=100, sub_cost_code_id=1)
    eli_svc = MagicMock()
    eli_svc.read_by_expense_id.return_value = [recoded]
    eli_svc.read_by_qbo_identity.return_value = recoded
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
        side_effect=lambda sid: sid == 1,
    ), patch(
        "integrations.intuit.qbo.outbox.business.service.QboOutboxService"
    ) as outbox, patch.object(svc, "record_confirmation"), patch.object(
        svc, "mark_enqueued"
    ):
        outbox.return_value.enqueue = MagicMock()
        result = svc.confirm(
            public_id="x",
            project_id=1,
            sub_cost_code_id=10,
            description="d",
            was_overridden=False,
            user_id=1,
        )
    assert result["status"] == "enqueued"


def test_g5_preview_pdf_skips_holding_entry_like_generate_bills():
    from entities.contract_labor.business.bill_service import ContractLaborBillService

    svc = ContractLaborBillService()
    svc.cl_service = MagicMock()
    svc.line_item_repo = MagicMock()
    svc.vendor_service = MagicMock()
    svc.project_service = MagicMock()
    svc.scc_service = MagicMock()
    svc.vendor_service.read_all.return_value = [SimpleNamespace(id=1, name="V")]
    entry = SimpleNamespace(id=7, vendor_id=1)
    svc.cl_service.read_by_status.return_value = [entry]
    svc.line_item_repo.read_by_contract_labor_id.return_value = [
        SimpleNamespace(project_id=2, is_overhead=False, sub_cost_code_id=9),
    ]
    svc.scc_service.is_holding_code.return_value = True
    svc.scc_service.read_all.return_value = []
    svc.project_service.read_all.return_value = []
    result = svc.preview_pdf_for_vendor(vendor_id=1)
    assert "error" in result


def test_g6_mark_as_ready_holding_raises_holding_code_refused():
    from entities.contract_labor.business.service import ContractLaborService

    svc = ContractLaborService()
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=1,
            vendor_id=1,
            sub_cost_code_id=2,
            hourly_rate=1,
            total_hours=1,
        )
    )
    with patch(
        "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository.read_by_contract_labor_id",
        return_value=[SimpleNamespace(sub_cost_code_id=9)],
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused) as exc:
            svc.mark_as_ready("cl-1")
    assert exc.value.error_code == ErrorCode.HOLDING_CODE
    assert "00.02" in exc.value.detail


def test_g6_mark_as_ready_missing_scc_still_value_error():
    from entities.contract_labor.business.service import ContractLaborService

    svc = ContractLaborService()
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(
            id=1,
            vendor_id=1,
            sub_cost_code_id=None,
            hourly_rate=1,
            total_hours=1,
        )
    )
    with patch(
        "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository.read_by_contract_labor_id",
        return_value=[],
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=False,
    ):
        with pytest.raises(ValueError, match="SubCostCode is required"):
            svc.mark_as_ready("cl-1")


def test_g7_parent_has_holding_line_contract_labor_header():
    from entities.sub_cost_code.business.holding_lines import parent_has_holding_line

    with patch(
        "entities.contract_labor.persistence.repo.ContractLaborRepository.read_by_id",
        return_value=SimpleNamespace(sub_cost_code_id=5),
    ), patch(
        "entities.contract_labor.persistence.line_item_repo.ContractLaborLineItemRepository.read_by_contract_labor_id",
        return_value=[],
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        return_value=True,
    ):
        assert parent_has_holding_line("contract_labor", 1) is True


def _invoice_service_with_lines(lines):
    from entities.invoice.business.service import InvoiceService

    inv_svc = InvoiceService.__new__(InvoiceService)
    inv_svc.invoice_line_item_service = MagicMock()
    inv_svc.invoice_line_item_service.read_by_invoice_id.return_value = lines
    return inv_svc


@pytest.mark.parametrize(
    "own_scc, source_scc, expected",
    [
        (None, 8, True),   # uncoded invoice line, source on the holding code
        (5, 8, True),      # R3-4: a real code on the invoice line does not clear the source
        (8, 5, True),      # the invoice line itself on the holding code
        (5, 6, False),     # both real
    ],
)
def test_g7_parent_has_holding_line_invoice_checks_own_and_source_scc(own_scc, source_scc, expected):
    from entities.sub_cost_code.business.holding_lines import parent_has_holding_line

    ili = SimpleNamespace(
        sub_cost_code_id=own_scc,
        source_type="BillLineItem",
        bill_line_item_id=3,
        expense_line_item_id=None,
        bill_credit_line_item_id=None,
    )
    inv_svc = _invoice_service_with_lines([ili])
    with patch(
        "entities.invoice.business.service.InvoiceService",
        return_value=inv_svc,
    ), patch(
        "entities.bill_line_item.business.service.BillLineItemService.read_by_id",
        return_value=SimpleNamespace(sub_cost_code_id=source_scc),
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        side_effect=lambda sid: sid == 8,
    ):
        assert parent_has_holding_line("invoice", 4) is expected
        # Approval (above) and completion must agree on every case.
        assert inv_svc.invoice_has_holding_line(4) is expected


def test_f3_bill_line_item_repo_maps_sproc_holding_code():
    from entities.bill_line_item.persistence.repo import BillLineItemRepository

    repo = BillLineItemRepository()
    with patch("entities.bill_line_item.persistence.repo.get_connection") as gc:
        conn = MagicMock()
        gc.return_value.__enter__.return_value = conn
        conn.cursor.return_value.fetchone.return_value = None
        with patch("entities.bill_line_item.persistence.repo.call_procedure") as cp:
            cp.side_effect = Exception("[42000] HOLDING_CODE: blocked")
            with pytest.raises(HoldingCodeRefused):
                repo.create(
                    bill_id=1,
                    sub_cost_code_id=1,
                    created_by_user_id=17,
                )


def test_g9_review_repo_maps_create_review_holding_code():
    from entities.review.persistence.repo import ReviewRepository

    repo = ReviewRepository()
    with patch("entities.review.persistence.repo.get_connection") as gc:
        conn = MagicMock()
        gc.return_value.__enter__.return_value = conn
        conn.cursor.return_value.fetchone.return_value = None
        with patch("entities.review.persistence.repo.call_procedure") as cp:
            cp.side_effect = Exception("[42000] HOLDING_CODE: blocked")
            with pytest.raises(HoldingCodeRefused):
                repo.create(
                    review_status_id=1,
                    user_id=17,
                    bill_id=1,
                )


def test_f2_build_advance_payload_refuses_holding_on_final_status():
    from entities.review.business.model import ParentType
    from entities.review.business.service import ReviewService

    svc = ReviewService()
    bill = SimpleNamespace(id=3, public_id="b3", is_draft=True, status="in_review")
    final_status = SimpleNamespace(
        id=2, name="approved", is_final=True, is_declined=False, sort_order=3
    )
    with patch.object(svc, "_resolve_parent", return_value=bill), patch.object(
        svc, "_get_current_by_id",
        return_value=SimpleNamespace(
            review_kind="in_review",
            status_sort_order=2,
            status_is_final=False,
            status_is_declined=False,
        ),
    ), patch.object(
        svc.review_status_service,
        "get_next_status",
        return_value=final_status,
    ), patch(
        "entities.sub_cost_code.business.holding_lines.parent_has_holding_line",
        return_value=True,
    ):
        with pytest.raises(HoldingCodeRefused):
            svc.build_advance_payload(
                parent_type=ParentType.BILL,
                parent_public_id="b3",
                user_id=1,
            )


def test_f5_generate_bills_skips_holding_entry():
    from entities.contract_labor.business.bill_service import ContractLaborBillService

    svc = ContractLaborBillService()
    svc.cl_service = MagicMock()
    svc.line_item_repo = MagicMock()
    svc.vendor_service = MagicMock()
    svc.project_service = MagicMock()
    svc.scc_service = MagicMock()
    svc.bill_service = MagicMock()
    svc.vendor_service.read_all.return_value = [SimpleNamespace(id=1, name="V")]
    entry = SimpleNamespace(id=7, vendor_id=1, status="ready")
    svc.cl_service.read_by_status.return_value = [entry]
    svc.line_item_repo.read_by_contract_labor_id.return_value = [
        SimpleNamespace(
            project_id=2,
            is_overhead=False,
            sub_cost_code_id=9,
            line_date="2026-01-01",
            price=Decimal("1"),
            is_billable=True,
        ),
    ]
    svc.scc_service.is_holding_code.return_value = True
    svc.project_service.read_all.return_value = [SimpleNamespace(id=2, name="P")]
    svc.scc_service.read_all.return_value = []
    result = svc.generate_bills_for_vendor(vendor_id=1)
    assert result["skipped_entries"][0]["reason"] == "holding_code_on_line"
    svc.bill_service.create.assert_not_called()


def test_f10_coding_router_releases_own_claim_on_holding_refusal():
    from entities.coding_suggestion.api.router import confirm_expense_coding_item_router
    from tests.conftest import actor_context

    body = SimpleNamespace(
        project_public_id="p",
        sub_cost_code_public_id="s",
        description="d",
        was_overridden=False,
    )
    svc = MagicMock()
    svc.read_by_public_id.return_value = SimpleNamespace(
        public_id="item-1", claimed_by_user_id=None
    )
    svc.is_multi_line_purchase.return_value = False
    svc.claim.return_value = SimpleNamespace(public_id="item-1", claimed_by_user_id=5)
    svc.confirm.side_effect = HoldingCodeRefused("holding")
    with actor_context(5, False), patch(
        "entities.coding_suggestion.api.router.CodingSuggestionService",
        return_value=svc,
    ), patch(
        "entities.coding_suggestion.api.router.assert_can_access_project",
    ), patch(
        "entities.coding_suggestion.api.router._assert_can_access_coding_suggestion",
    ), patch(
        "entities.project.business.service.ProjectService"
    ) as proj, patch(
        "entities.sub_cost_code.business.service.SubCostCodeService"
    ) as scc:
        proj.return_value.read_by_public_id.return_value = SimpleNamespace(id=1)
        scc.return_value.read_by_public_id.return_value = SimpleNamespace(id=2)
        with pytest.raises(ApiError) as exc:
            confirm_expense_coding_item_router(
                public_id="item-1",
                body=body,
                _={"sub": "u"},
            )
        svc.release.assert_called_once_with(public_id="item-1", user_id=5)
        assert not str(exc.value.detail).startswith("422:")


def _cl_bill_put(entry, *, status, line_scc_ids, holding_ids):
    """Drive PUT /contract-labor/{id}/bill with mocked repos; return (repo, line_repo, error)."""
    from entities.contract_labor.api.router import update_contract_labor_bill

    repo = MagicMock()
    line_repo = MagicMock()
    error = None
    with patch(
        "entities.contract_labor.api.router.ContractLaborService"
    ) as cl_cls, patch(
        "entities.contract_labor.api.router.ContractLaborLineItemRepository",
        return_value=line_repo,
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.is_holding_code",
        side_effect=lambda sid: sid in holding_ids,
    ):
        cl_cls.return_value.read_by_public_id.return_value = entry
        cl_cls.return_value.repo = repo
        body = SimpleNamespace(
            row_version="AAAA",
            bill_vendor_id=1,
            bill_date="2026-01-01",
            due_date="2026-01-02",
            bill_number="n",
            status=status,
            line_items=[
                SimpleNamespace(id=i + 1, row_version="AAAA", sub_cost_code_id=scc)
                for i, scc in enumerate(line_scc_ids)
            ],
        )
        try:
            update_contract_labor_bill(public_id="cl-1", bill_update=body, current_user={"sub": "u"})
        except Exception as exc:  # the allowed path runs on into mocked writes
            error = exc
    return repo, line_repo, error


def test_r3_cl_bill_put_status_omitted_on_ready_entry_refuses_holding_line():
    """R3-1: status omitted keeps the entry 'ready' (sproc keeps the current status)."""
    entry = SimpleNamespace(id=1, public_id="cl-1", status="ready", sub_cost_code_id=5)
    repo, line_repo, error = _cl_bill_put(entry, status=None, line_scc_ids=[9], holding_ids={9})
    assert isinstance(error, HoldingCodeRefused)
    repo.update_bill_info.assert_not_called()
    line_repo.update_by_id.assert_not_called()
    line_repo.create.assert_not_called()


def test_r3_cl_bill_put_refuses_holding_header_scc():
    """R3-2: the header SCC counts, as it does for /mark-ready."""
    entry = SimpleNamespace(id=1, public_id="cl-1", status="pending_review", sub_cost_code_id=9)
    repo, _line_repo, error = _cl_bill_put(entry, status="ready", line_scc_ids=[5], holding_ids={9})
    assert isinstance(error, HoldingCodeRefused)
    repo.update_bill_info.assert_not_called()


def test_r3_cl_bill_put_pending_entry_may_keep_holding_line():
    """Control: an entry staying pending_review may carry the holding code."""
    entry = SimpleNamespace(id=1, public_id="cl-1", status="pending_review", sub_cost_code_id=None)
    repo, _line_repo, error = _cl_bill_put(entry, status=None, line_scc_ids=[9], holding_ids={9})
    assert not isinstance(error, HoldingCodeRefused)
    repo.update_bill_info.assert_called_once()


def test_r3_bill_line_pull_create_on_approved_parent_lands_uncoded_and_records_issue():
    """R3-3: the CREATE (miss) path flags the holding code instead of dropping it silently."""
    from integrations.intuit.qbo.bill.connector.bill_line_item.business import service as mod
    from integrations.intuit.qbo.base.drift_types import DRIFT_HOLDING_CODE_ON_APPROVED

    bill = SimpleNamespace(id=5, public_id="b-pub", status="approved", qbo_id="qb-5")
    bli_svc = MagicMock()
    recon = MagicMock()
    recon.read_unresolved_issue_keys_by_drift_type.return_value = []
    connector = mod.BillLineItemConnector(
        bill_line_item_service=bli_svc,
        bill_service=MagicMock(read_by_id=MagicMock(return_value=bill)),
        sub_cost_code_service=MagicMock(),
        reconciliation_repo=recon,
    )
    qbo_line = SimpleNamespace(
        id=1, qbo_line_id="L1", item_ref_value="item-1", customer_ref_value=None,
        description="d", amount=Decimal("1"), qty=None, unit_price=None,
        markup_percent=None, billable_status=None,
    )
    with patch.object(connector, "_get_project_public_id", return_value=None), patch.object(
        mod, "resolve_dbo_sub_cost_code", return_value=SimpleNamespace(id=99, is_holding_code=True),
    ), patch.object(
        mod,
        "run_line_identity_fastpath_dbo_only",
        side_effect=lambda **kw: SimpleNamespace(entity=kw["resolve_candidate"]()),
    ):
        connector.sync_from_qbo_bill_line(
            bill_id=5, qbo_bill_line=qbo_line, live_qbo_line_ids=frozenset({"L1"}), realm_id="r1",
        )
    assert bli_svc.create.call_args.kwargs["sub_cost_code_id"] is None
    created = [c.kwargs for c in recon.create.call_args_list]
    assert [c["drift_type"] for c in created] == [DRIFT_HOLDING_CODE_ON_APPROVED]
    assert created[0]["entity_type"] == "Bill"


def test_r3_expense_line_pull_create_on_approved_parent_lands_uncoded_and_records_issue():
    """R3-3 twin for the Purchase -> ExpenseLineItem connector."""
    from integrations.intuit.qbo.purchase.connector.expense_line_item.business import (
        service as mod,
    )
    from integrations.intuit.qbo.base.drift_types import DRIFT_HOLDING_CODE_ON_APPROVED

    expense = SimpleNamespace(id=5, public_id="e-pub", status="approved", qbo_id="P-5")
    eli_svc = MagicMock()
    scc_svc = MagicMock()
    scc_svc.read_by_id.return_value = SimpleNamespace(id=99, is_holding_code=True)
    recon = MagicMock()
    recon.read_unresolved_issue_keys_by_drift_type.return_value = []
    connector = mod.PurchaseLineExpenseLineItemConnector(
        expense_line_item_service=eli_svc,
        sub_cost_code_service=scc_svc,
        qbo_customer_repo=MagicMock(),
        project_service=MagicMock(),
        reconciliation_repo=recon,
        expense_service=MagicMock(read_by_id=MagicMock(return_value=expense)),
    )
    qbo_line = SimpleNamespace(
        id=1, qbo_line_id="L1", item_ref_value="I-99", customer_ref_value=None,
        description="d", amount=Decimal("1"), qty=None, unit_price=None,
        markup_percent=None, billable_status=None,
    )
    with patch.object(connector, "_get_sub_cost_code_id", return_value=99), patch.object(
        mod,
        "run_line_identity_fastpath_dbo_only",
        side_effect=lambda **kw: SimpleNamespace(entity=kw["resolve_candidate"]()),
    ):
        connector.sync_from_qbo_purchase_line(
            expense_id=5, expense_public_id="e-pub", qbo_line=qbo_line,
            live_qbo_line_ids=frozenset({"L1"}), realm_id="r1",
        )
    assert eli_svc.create.call_args.kwargs["sub_cost_code_id"] is None
    created = [c.kwargs for c in recon.create.call_args_list]
    assert [c["drift_type"] for c in created] == [DRIFT_HOLDING_CODE_ON_APPROVED]
    assert created[0]["entity_type"] == "Expense"
