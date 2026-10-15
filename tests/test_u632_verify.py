"""U-632 verify round — regression tests that each go RED on a revert of the
product hunk they pin (pure: everything below the route/service is mocked)."""
from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from shared.api.errors import ErrorCode
from shared.lifecycle.completion_gate import HoldingCodeRefused

HOLDING_SCC = SimpleNamespace(id=99, is_holding_code=True, qbo_id="q99", realm_id="r1")
HOLDING_ID_PATCH = patch(
    "entities.sub_cost_code.persistence.repo.SubCostCodeRepository.read_holding_sub_cost_code_ids",
    return_value=[99],
)


# --------------------------------------------------------------------------
# G3 — enqueue_coding_recode_on_submit reads the lazy PROPERTY, not the field
# --------------------------------------------------------------------------


def test_g3_fresh_service_skips_holding_line_via_lazy_property():
    from entities.expense.business.service import ExpenseService

    svc = ExpenseService(repo=MagicMock())  # private _expense_line_item_service left None
    eli_instance = MagicMock()
    eli_instance.read_by_expense_id.return_value = [SimpleNamespace(sub_cost_code_id=99)]
    coding_svc = MagicMock()
    with patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService",
        return_value=eli_instance,
    ) as eli_cls, patch(
        "entities.coding_suggestion.persistence.repo.CodingSuggestionRepository.read_state_by_expense_ids",
        return_value={3: [{"public_id": "cs-1", "status": "pending"}]},
    ), patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService",
        return_value=coding_svc,
    ), HOLDING_ID_PATCH, patch.object(
        ExpenseService,
        "_resolve_dominant_expense_coding_for_submit",
        return_value={"project_id": 1, "sub_cost_code_id": 2, "description": "d"},
    ):
        summary = svc.enqueue_coding_recode_on_submit(expense_id=3, user_id=5)
    assert summary["items"] == [{"reason": "holding_code_on_line"}]
    assert summary["enqueued"] == 0
    assert summary["skipped"] == 1
    coding_svc.confirm.assert_not_called()
    eli_cls.assert_called_once()


# --------------------------------------------------------------------------
# G8 + F4 — real route paths (TestClient + dependency overrides)
# --------------------------------------------------------------------------


@pytest.fixture
def app_client():
    from app import app

    yield app, TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def _override_current_user(app, endpoint, param="current_user"):
    dep = inspect.signature(endpoint).parameters[param].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "test-sub", "tenant_id": 1}


def _assert_holding_422(response):
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error_code"] == ErrorCode.HOLDING_CODE
    assert isinstance(body["detail"], str)
    assert not body["detail"].startswith("422:")
    assert "suspense" in body["detail"]


def test_g8_bill_complete_route_returns_422_holding_code_before_enqueue(app_client, monkeypatch):
    from entities.bill.api.router import complete_bill_router

    app, client = app_client
    monkeypatch.delenv("LIFECYCLE_COMPLETION_GATE_BILL", raising=False)
    _override_current_user(app, complete_bill_router)
    job_svc = MagicMock()
    with patch(
        "entities.bill.business.service.BillService.read_by_public_id",
        return_value=SimpleNamespace(id=1, public_id="b-1", is_draft=True),
    ), patch(
        "entities.bill_line_item.business.service.BillLineItemService.read_by_bill_id",
        return_value=[SimpleNamespace(sub_cost_code_id=7), SimpleNamespace(sub_cost_code_id=99)],
    ), HOLDING_ID_PATCH, patch(
        "entities.completion_job.business.service.CompletionJobService",
        return_value=job_svc,
    ):
        response = client.post("/api/v1/complete/bill/b-1")
    _assert_holding_422(response)
    job_svc.enqueue.assert_not_called()


def test_g8_coding_confirm_route_returns_422_holding_code_and_releases_claim(app_client):
    from entities.coding_suggestion.api.router import confirm_expense_coding_item_router

    app, client = app_client
    _override_current_user(app, confirm_expense_coding_item_router, param="_")
    svc = MagicMock()
    svc.read_by_public_id.return_value = SimpleNamespace(public_id="cs-1", claimed_by_user_id=None)
    svc.is_multi_line_purchase.return_value = False
    svc.claim.return_value = SimpleNamespace(public_id="cs-1", claimed_by_user_id=5)
    svc.confirm.side_effect = HoldingCodeRefused(
        "This expense cannot be approved while coded to the suspense sub cost code."
    )
    with patch(
        "entities.coding_suggestion.api.router.current_user_id",
        SimpleNamespace(get=lambda: 5),
    ), patch(
        "entities.coding_suggestion.api.router.CodingSuggestionService",
        return_value=svc,
    ), patch(
        "entities.coding_suggestion.api.router._assert_can_access_coding_suggestion",
    ), patch(
        "entities.coding_suggestion.api.router.assert_can_access_project",
    ), patch(
        "entities.project.business.service.ProjectService.read_by_public_id",
        return_value=SimpleNamespace(id=1),
    ), patch(
        "entities.sub_cost_code.business.service.SubCostCodeService.read_by_public_id",
        return_value=SimpleNamespace(id=2),
    ):
        response = client.post(
            "/api/v1/expense/coding/cs-1/confirm",
            json={
                "project_public_id": "p-1",
                "sub_cost_code_public_id": "s-1",
                "description": "d",
                "was_overridden": False,
            },
        )
    _assert_holding_422(response)
    svc.release.assert_called_once_with(public_id="cs-1", user_id=5)


def test_f4_expense_complete_route_returns_422_holding_code_before_job(app_client, monkeypatch):
    from entities.expense.api.router import complete_expense_router

    app, client = app_client
    monkeypatch.delenv("LIFECYCLE_COMPLETION_GATE_EXPENSE", raising=False)
    _override_current_user(app, complete_expense_router)
    job_cls = MagicMock()
    with patch(
        "entities.expense.business.service.ExpenseService.read_by_public_id",
        return_value=SimpleNamespace(id=4, public_id="e-1", is_draft=True),
    ), patch(
        "entities.expense_line_item.business.service.ExpenseLineItemService.read_by_expense_id",
        return_value=[SimpleNamespace(sub_cost_code_id=99)],
    ), HOLDING_ID_PATCH, patch(
        "entities.completion_job.business.service.CompletionJobService",
        job_cls,
    ):
        response = client.post("/api/v1/complete/expense/e-1")
    _assert_holding_422(response)
    job_cls.assert_not_called()
    job_cls.return_value.enqueue.assert_not_called()


def test_f4_bill_background_runner_marks_holding_refusal_terminal():
    from entities.bill.api import router as bill_router

    job_svc = MagicMock()
    repo = MagicMock()
    with patch(
        "entities.completion_job.business.service.CompletionJobService",
        return_value=job_svc,
    ), patch.object(bill_router, "BillService") as svc_cls, patch.object(
        bill_router, "BillRepository", return_value=repo
    ):
        svc_cls.return_value.read_by_public_id.return_value = SimpleNamespace(is_draft=True)
        svc_cls.return_value.complete_bill.side_effect = HoldingCodeRefused("suspense line")
        bill_router._run_complete_bill("b-1", "job-1")
    job_svc.mark_failure.assert_called_once()
    assert job_svc.mark_failure.call_args.kwargs.get("max_attempts") == 1
    job_svc.mark_success.assert_not_called()
    saved = repo.set_completion_result.call_args.args[1]
    assert saved["status_code"] == 422
    assert saved["errors"][0]["error_code"] == ErrorCode.HOLDING_CODE


def test_f4_expense_background_runner_caches_422_and_marks_terminal():
    from entities.expense.api import router as expense_router

    job_svc = MagicMock()
    expense_router._EXPENSE_COMPLETION_RESULT_CACHE.pop("e-1", None)
    try:
        with patch(
            "entities.completion_job.business.service.CompletionJobService",
            return_value=job_svc,
        ), patch.object(expense_router, "ExpenseService") as svc_cls:
            svc_cls.return_value.read_by_public_id.return_value = SimpleNamespace(is_draft=True)
            svc_cls.return_value.complete_expense.side_effect = HoldingCodeRefused("suspense line")
            expense_router._run_complete_expense("e-1", "job-1")
        job_svc.mark_failure.assert_called_once()
        assert job_svc.mark_failure.call_args.kwargs.get("max_attempts") == 1
        cached = expense_router._EXPENSE_COMPLETION_RESULT_CACHE["e-1"]["result"]
        assert cached["status_code"] == 422
        assert cached["errors"][0]["error_code"] == ErrorCode.HOLDING_CODE
    finally:
        expense_router._EXPENSE_COMPLETION_RESULT_CACHE.pop("e-1", None)


# --------------------------------------------------------------------------
# F6 — outbox recode: HoldingCodeRefused -> mark_error, no retry
# --------------------------------------------------------------------------


def _outbox_row():
    from integrations.intuit.qbo.outbox.business.model import QboOutbox

    return QboOutbox(
        id=1, public_id="outbox-1", row_version="abc", kind="recode_purchase_line",
        entity_type="CodingSuggestion", entity_public_id="cs-1", realm_id="r1",
        request_id="req-1", status="processing", attempts=0,
    )


def test_f6_recode_handler_marks_suggestion_error_on_holding_refusal():
    from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker

    svc = MagicMock()
    svc.read_by_public_id.return_value = SimpleNamespace(
        public_id="cs-1", status="enqueued", confirmation_seq=3,
        qbo_purchase_qbo_id="p1", sync_token_at_suggest="5",
    )
    worker = QboOutboxWorker(repo=MagicMock(), api_budget=MagicMock())
    with patch(
        "entities.coding_suggestion.business.service.CodingSuggestionService",
        return_value=svc,
    ), patch(
        "integrations.intuit.qbo.purchase.connector.expense.business.service.PurchaseExpenseConnector"
    ) as conn_cls, patch.object(
        QboOutboxWorker,
        "_resolve_recode_purchase_line_targets",
        return_value=([{"target_qbo_line_id": "1", "sub_cost_code_id": 99}], None),
    ):
        conn_cls.return_value.recode_purchase_lines.side_effect = HoldingCodeRefused(
            "The suspense sub cost code cannot be pushed to QuickBooks."
        )
        worker._handle_recode_purchase_line(_outbox_row())  # must not raise
    svc.mark_error.assert_called_once()
    assert svc.mark_error.call_args.kwargs["write_error"].startswith("HoldingCodeRefused:")
    assert svc.mark_error.call_args.kwargs["expected_confirmation_seq"] == 3
    svc.mark_written.assert_not_called()


def test_f6_unexpected_holding_refusal_dead_letters_without_retry():
    from integrations.intuit.qbo.outbox.business.worker import QboOutboxWorker

    worker = QboOutboxWorker(repo=MagicMock(), api_budget=MagicMock())
    with patch.object(worker, "_dead_letter") as dead, patch.object(
        worker, "_schedule_retry_or_dead_letter"
    ) as retry:
        worker._handle_unexpected_error(_outbox_row(), HoldingCodeRefused("suspense"))
    dead.assert_called_once()
    assert dead.call_args.args[1].startswith("HoldingCodeRefused:")
    retry.assert_not_called()


# --------------------------------------------------------------------------
# F7 — Purchase pull UPDATE on a completed Expense with a holding line
# --------------------------------------------------------------------------


def test_f7_purchase_pull_update_on_completed_expense_records_issue_status_unchanged():
    from integrations.intuit.qbo.purchase.connector.expense.business import service as mod
    from integrations.intuit.qbo.base.drift_types import DRIFT_HOLDING_CODE_ON_COMPLETED

    expense_svc = MagicMock()
    direct = SimpleNamespace(
        id=11, public_id="e-11", row_version="rv", reference_number="R1",
        is_draft=False, status="completed",
    )
    expense_svc.update_by_public_id.return_value = SimpleNamespace(id=11, public_id="e-11")
    recon = MagicMock()
    recon.read_unresolved_issue_keys_by_drift_type.return_value = []
    scc_svc = MagicMock()
    with patch(
        "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service.PurchaseLineExpenseLineItemConnector"
    ):
        connector = mod.PurchaseExpenseConnector(
            expense_service=expense_svc,
            vendor_service=MagicMock(),
            qbo_vendor_repo=MagicMock(),
            reconciliation_repo=recon,
            sub_cost_code_service=scc_svc,
        )
    qbo_purchase = SimpleNamespace(
        id=1, qbo_id="P-1", realm_id="r1", doc_number="R1", txn_date="2026-01-01",
        private_note=None, total_amt=Decimal("10"), credit=False, sync_token="3",
        entity_ref_value="v1",
    )
    lines = [SimpleNamespace(item_ref_value="I-99", amount=Decimal("10"))]
    with patch.object(connector, "_get_vendor_public_id", return_value="v-pub"), patch.object(
        connector, "_sync_line_items"
    ), patch.object(mod, "guard_lines_present"), patch.object(
        mod,
        "run_identity_fastpath_dbo_only",
        side_effect=lambda **kw: SimpleNamespace(entity=kw["apply_fields"](direct)),
    ), patch(
        "integrations.intuit.qbo.base.cost_code_resolver.resolve_dbo_sub_cost_code",
        return_value=HOLDING_SCC,
    ):
        connector.sync_from_qbo_purchase(qbo_purchase, lines)
    update_kwargs = expense_svc.update_by_public_id.call_args.kwargs
    assert "status" not in update_kwargs and "is_draft" not in update_kwargs
    created = [c.kwargs for c in recon.create.call_args_list]
    assert [c["drift_type"] for c in created] == [DRIFT_HOLDING_CODE_ON_COMPLETED]
    assert created[0]["entity_type"] == "Expense"
    assert created[0]["qbo_id"] == "P-1"
    for name in ("finalize_by_id", "transition_status", "set_status"):
        getattr(expense_svc.repo, name).assert_not_called()


# --------------------------------------------------------------------------
# F8 — holding-code issue recorders dedupe on (realm, entity_type, qbo_id)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fn_name", ["record_holding_code_on_approved_deduped", "record_holding_code_on_completed_deduped"]
)
def test_f8_recorder_dedupes_same_key_but_inserts_for_other_entity(fn_name):
    from integrations.intuit.qbo.base import reconciliation_recorder as rr

    fn = getattr(rr, fn_name)
    repo = MagicMock()
    repo.read_unresolved_issue_keys_by_drift_type.return_value = [("r1", "Bill", "Q-1")]
    common = dict(entity_public_id="pub", realm_id="r1", details="d")

    fn(repo, entity_type="Bill", qbo_id="Q-1", **common)
    repo.create.assert_not_called()

    fn(repo, entity_type="Expense", qbo_id="Q-1", **common)
    fn(repo, entity_type="Bill", qbo_id="Q-2", **common)
    assert [(c.kwargs["entity_type"], c.kwargs["qbo_id"]) for c in repo.create.call_args_list] == [
        ("Expense", "Q-1"),
        ("Bill", "Q-2"),
    ]


# --------------------------------------------------------------------------
# G2 twin — expense-line pull keeps local SCC on an approved parent
# --------------------------------------------------------------------------


def test_g2_expense_line_pull_keeps_local_scc_on_approved_parent():
    from integrations.intuit.qbo.purchase.connector.expense_line_item.business import (
        service as mod,
    )
    from integrations.intuit.qbo.base.drift_types import DRIFT_HOLDING_CODE_ON_APPROVED

    expense = SimpleNamespace(id=5, public_id="e-pub", status="approved", qbo_id="P-5")
    eli_svc = MagicMock()
    direct = SimpleNamespace(
        id=10, public_id="li-pub", row_version="rv", sub_cost_code_id=42,
        quantity=None, rate=None, markup=None, is_billable=True, realm_id="r1",
    )
    eli_svc.update_by_public_id.return_value = direct
    scc_svc = MagicMock()
    scc_svc.is_holding_code.return_value = True
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
        side_effect=lambda **kw: SimpleNamespace(entity=kw["apply_fields"](direct)),
    ):
        connector.sync_from_qbo_purchase_line(
            expense_id=5,
            expense_public_id="e-pub",
            qbo_line=qbo_line,
            live_qbo_line_ids=frozenset({"L1"}),
            realm_id="r1",
        )
    assert eli_svc.update_by_public_id.call_args.kwargs["sub_cost_code_id"] == 42
    created = [c.kwargs for c in recon.create.call_args_list]
    assert [c["drift_type"] for c in created] == [DRIFT_HOLDING_CODE_ON_APPROVED]
    assert created[0]["entity_type"] == "Expense"
