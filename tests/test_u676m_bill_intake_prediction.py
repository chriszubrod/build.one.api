"""U-676m: bill intake prediction persistence, bind, final, tick reuse, read route, accuracy."""
from __future__ import annotations

import asyncio
import inspect
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.business import read_attachment_service as ras
from entities.bill.business.model import Bill
from entities.bill.business.service import BillService
from entities.bill.api.read_attachment_router import (
    read_bill_attachment_router,
    router as read_attachment_router,
)
from entities.bill_intake_prediction.api.router import (
    read_bill_intake_prediction_router,
    router as intake_prediction_router,
)
from entities.bill_intake_prediction.business.accuracy import aggregate_prediction_accuracy
from entities.bill_intake_prediction.business.model import BillIntakePrediction
from entities.bill_intake_prediction.business.service import (
    BillIntakePredictionService,
    sum_cascade_cost_and_tokens,
    tick_can_reuse_prediction,
)
from intelligence.observability.cascade_telemetry import cascade_usage_totals
from entities.bill_line_item.business.model import BillLineItem
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from entities.coding_suggestion.business import bill_text_suggestion_service as bts
from entities.coding_suggestion.business.bill_text_suggestion_service import (
    MODEL_VERSION,
    _process_one_claim,
    predict_text_coding,
)
from entities.bill_intake_prediction.business.service import TEXT_MODEL_VERSION
from entities.bill_line_item_attachment.business.model import BillLineItemAttachment
from entities.coding_suggestion.persistence.bill_text_suggestion_repo import BillTextCodingClaim
from intelligence.cascade.core import Attempt, CascadeResult, Rung
from intelligence.transport.base import Usage
from shared.access import EntityNotAccessibleError

ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BILL_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"




def _bill(**kw) -> Bill:
    base = dict(
        id=10,
        public_id=BILL_UUID,
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        vendor_id=5,
        payment_term_id=None,
        bill_date="2026-05-01",
        due_date="2026-05-15",
        bill_number="INV-1",
        total_amount=Decimal("100.00"),
        memo=None,
        is_draft=False,
    )
    base.update(kw)
    return Bill(**base)


def _line(**kw) -> BillLineItem:
    base = dict(
        id=1,
        public_id="line-pid",
        row_version=None,
        created_datetime=None,
        modified_datetime=None,
        bill_id=10,
        sub_cost_code_id=501,
        project_id=100,
        description="x",
        quantity=None,
        rate=None,
        amount=None,
        is_billable=True,
        is_billed=False,
        markup=None,
        price=None,
        is_draft=False,
    )
    base.update(kw)
    return BillLineItem(**base)


def _prediction(**kw) -> BillIntakePrediction:
    base = dict(
        id=1,
        public_id="pred-pid",
        attachment_id=99,
        extracted_datetime=datetime(2026, 5, 1, 12, 0, 0),
        bill_id=10,
        intake_source="pdf_drop",
        model_version=MODEL_VERSION,
        status="ok",
        pred_vendor_id=5,
        pred_vendor_extracted_name=None,
        pred_bill_number="INV-1",
        pred_bill_date=date(2026, 5, 1),
        pred_total_amount=Decimal("100"),
        pred_description="d",
        pred_sub_cost_code_id=501,
        pred_project_id=100,
        coding_mode="A",
        coding_chosen_project_id=100,
        header_confidence=Decimal("0.9"),
        header_model="haiku",
        coding_confidence=Decimal("0.85"),
        coding_model="haiku",
        latency_ms_total=100,
        timing_json="{}",
        input_tokens=1,
        output_tokens=2,
        cost_usd=Decimal("0.001"),
    )
    base.update(kw)
    return BillIntakePrediction(**base)


def test_persist_called_with_expected_fields_on_read():
    intake = MagicMock(spec=BillIntakePredictionService)
    with patch.object(ras, "config") as cfg, patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(
        ras, "_extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "timeout"}),
    ), patch(
        "entities.bill.business.read_attachment_service.asyncio.to_thread",
        side_effect=lambda fn, *a, **k: fn(*a, **k),
    ):
        cfg.Settings.return_value.bill_drop_prefill_enabled = True
        cfg.Settings.return_value.bill_drop_prefill_timeout_seconds = 30
        att_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        asyncio.run(
            _drained(ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                intake_source="pdf_upload",
                intake_prediction_service=intake,
            ))
        )
    intake.record_read.assert_called_once()
    assert intake.record_read.call_args.kwargs["intake_source"] == "pdf_upload"


def _baseline_timeout_payload() -> dict:
    with patch.object(ras, "config") as cfg, patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(
        ras, "_extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "timeout"}),
    ), patch(
        "entities.bill.business.read_attachment_service.asyncio.to_thread",
        side_effect=lambda fn, *a, **k: fn(*a, **k),
    ):
        cfg.Settings.return_value.bill_drop_prefill_enabled = True
        cfg.Settings.return_value.bill_drop_prefill_timeout_seconds = 30
        att_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        return asyncio.run(
            _drained(ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                intake_prediction_service=MagicMock(spec=BillIntakePredictionService),
            ))
        )


def test_read_payload_unchanged_when_build_raises():
    baseline = _baseline_timeout_payload()
    svc = BillIntakePredictionService()
    svc.build_upsert_params_from_read = MagicMock(side_effect=RuntimeError("build"))
    with patch.object(ras, "config") as cfg, patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(
        ras, "_extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "timeout"}),
    ), patch(
        "entities.bill.business.read_attachment_service.asyncio.to_thread",
        side_effect=lambda fn, *a, **k: fn(*a, **k),
    ):
        cfg.Settings.return_value.bill_drop_prefill_enabled = True
        cfg.Settings.return_value.bill_drop_prefill_timeout_seconds = 30
        att_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        result = asyncio.run(
            _drained(ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                intake_prediction_service=svc,
            ))
        )
    assert json.dumps(result, sort_keys=True) == json.dumps(baseline, sort_keys=True)


def test_read_payload_unchanged_when_upsert_raises():
    baseline = _baseline_timeout_payload()
    svc = BillIntakePredictionService()
    svc.repo = MagicMock()
    svc.repo.upsert.side_effect = RuntimeError("db down")
    with patch.object(ras, "config") as cfg, patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(
        ras, "_extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "timeout"}),
    ), patch(
        "entities.bill.business.read_attachment_service.asyncio.to_thread",
        side_effect=lambda fn, *a, **k: fn(*a, **k),
    ):
        cfg.Settings.return_value.bill_drop_prefill_enabled = True
        cfg.Settings.return_value.bill_drop_prefill_timeout_seconds = 30
        att_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        result = asyncio.run(
            _drained(ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                intake_prediction_service=svc,
            ))
        )
    assert json.dumps(result, sort_keys=True) == json.dumps(baseline, sort_keys=True)


def test_record_read_runs_in_dedicated_pool_with_request_context():
    import threading

    from shared.authz.context import current_user_id

    seen = {}

    def _record(**_kwargs):
        seen["thread"] = threading.current_thread().name
        seen["user"] = current_user_id.get()

    intake = MagicMock(spec=BillIntakePredictionService)
    intake.record_read.side_effect = _record
    with patch.object(ras, "config") as cfg, patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(
        ras, "_extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "timeout"}),
    ):
        cfg.Settings.return_value.bill_drop_prefill_enabled = True
        cfg.Settings.return_value.bill_drop_prefill_timeout_seconds = 30
        att_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)

        async def _run():
            token = current_user_id.set(4242)
            try:
                await ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    intake_prediction_service=intake,
                )
            finally:
                current_user_id.reset(token)
            await _await_record_read_tasks()

        asyncio.run(_run())
    intake.record_read.assert_called_once()
    assert seen["thread"].startswith("intake-record")
    assert seen["user"] == 4242


def test_record_read_skipped_when_backlog_full():
    intake = MagicMock(spec=BillIntakePredictionService)
    ras._record_read_tasks.clear()
    sentinels = {MagicMock() for _ in range(ras._RECORD_READ_MAX_PENDING)}
    ras._record_read_tasks.update(sentinels)
    try:
        async def _run():
            ras._schedule_record_read(intake, attachment_id=7)

        asyncio.run(_run())
        assert len(ras._record_read_tasks) == ras._RECORD_READ_MAX_PENDING
        intake.record_read.assert_not_called()
    finally:
        ras._record_read_tasks.clear()


def test_read_path_does_not_refresh_attachment_after_extract():
    with patch.object(ras, "config") as cfg, patch.object(
        ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
    ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(
        ras, "_extract_with_inflight_wait",
        new=AsyncMock(return_value={"status": "timeout"}),
    ), patch(
        "entities.bill.business.read_attachment_service.asyncio.to_thread",
        side_effect=lambda fn, *a, **k: fn(*a, **k),
    ):
        cfg.Settings.return_value.bill_drop_prefill_enabled = True
        cfg.Settings.return_value.bill_drop_prefill_timeout_seconds = 30
        att_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
        att_cls.return_value.read_by_id = MagicMock()
        asyncio.run(
            _drained(ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                intake_prediction_service=MagicMock(spec=BillIntakePredictionService),
            ))
        )
    att_cls.return_value.read_by_id.assert_not_called()


def test_intake_source_validation_422():
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(read_attachment_router)
    dep = inspect.signature(read_bill_attachment_router).parameters["_"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "u1"}
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/bill/read-attachment",
        json={"attachment_public_id": ATT_UUID, "intake_source": "invalid"},
    )
    assert resp.status_code == 422


def test_bind_on_create_and_create_succeeds_when_bind_raises():
    intake = BillIntakePredictionService()
    intake.repo = MagicMock()
    intake.repo.bind_to_bill.side_effect = RuntimeError("bind")
    svc = BillService.__new__(BillService)
    bill = _bill(id=10, is_draft=True, public_id=BILL_UUID)
    svc.repo = MagicMock()
    svc.repo.create.return_value = bill
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_public_id.return_value = SimpleNamespace(id=5, public_id="v")
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.create.return_value = _line()
    svc.bill_line_item_attachment_service = MagicMock()
    svc.attachment_service = MagicMock()
    svc.attachment_service.read_by_public_id.return_value = SimpleNamespace(
        id=99, filename="a.pdf", content_type="application/pdf"
    )
    svc.assert_create_submit_reviewed = MagicMock()
    with patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ), patch(
        "entities.bill.business.service.VendorService"
    ) as vendor_cls:
        vendor_cls.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=5, public_id="v-pid"
        )
        out = BillService.create(
            svc,
            vendor_public_id="v-pid",
            bill_date="2026-05-01",
            due_date="2026-05-15",
            attachment_public_id=ATT_UUID,
        )
    assert out.public_id == BILL_UUID
    intake.repo.bind_to_bill.assert_called_once_with(attachment_id=99, bill_id=10)


def test_create_succeeds_when_bind_import_raises():
    import builtins

    real_import = builtins.__import__

    def _import_block(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "entities.bill_intake_prediction.business.service":
            raise ImportError("no module")
        return real_import(name, globals, locals, fromlist, level)

    svc = BillService.__new__(BillService)
    bill = _bill(id=10, is_draft=True, public_id=BILL_UUID)
    svc.repo = MagicMock()
    svc.repo.create.return_value = bill
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_public_id.return_value = SimpleNamespace(id=5, public_id="v")
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.create.return_value = _line()
    svc.bill_line_item_attachment_service = MagicMock()
    svc.attachment_service = MagicMock()
    svc.attachment_service.read_by_public_id.return_value = SimpleNamespace(
        id=99, filename="a.pdf", content_type="application/pdf"
    )
    svc.assert_create_submit_reviewed = MagicMock()
    with patch.object(builtins, "__import__", _import_block), patch(
        "entities.bill.business.service.VendorService"
    ) as vendor_cls:
        vendor_cls.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=5, public_id="v-pid"
        )
        out = BillService.create(
            svc,
            vendor_public_id="v-pid",
            bill_date="2026-05-01",
            due_date="2026-05-15",
            attachment_public_id=ATT_UUID,
        )
    assert out.public_id == BILL_UUID
    svc.repo.delete_by_id.assert_not_called()


def _complete_bill_fixture(*, intake: MagicMock) -> BillService:
    svc = BillService.__new__(BillService)
    draft = _bill(is_draft=True)
    finalized = _bill(is_draft=False)
    svc.read_by_public_id = MagicMock(return_value=draft)
    svc.repo = MagicMock()
    svc.repo.finalize_by_id.return_value = finalized
    svc.vendor_service = MagicMock()
    svc.vendor_service.read_by_id.return_value = SimpleNamespace(public_id="v-pid")
    svc._assert_no_duplicate_on_complete = MagicMock()
    svc._assert_completable_lines = MagicMock()
    svc.bill_line_item_service = MagicMock()
    svc.bill_line_item_service.read_by_bill_id.return_value = [_line(is_draft=False)]
    svc.bill_line_item_attachment_service = MagicMock()
    svc.bill_line_item_attachment_service.read_by_bill_line_item_ids.return_value = []
    svc.project_service = MagicMock()
    svc._qbo_auth_service = MagicMock(read_all=MagicMock(return_value=[]))
    svc._rename_invoice_blob_on_complete = MagicMock()
    return svc


def test_final_recorded_once_on_draft_to_completed_not_on_recomplete():
    intake = MagicMock()
    intake.read_by_bill_id.return_value = _prediction()
    svc = _complete_bill_fixture(intake=intake)
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ):
        first = svc.complete_bill(BILL_UUID)
    assert first["bill_finalized"] is True
    assert intake.record_final_on_complete_failure_isolated.call_count == 1

    done = _bill(is_draft=False)
    svc.read_by_public_id = MagicMock(return_value=done)
    intake.reset_mock()
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
    ) as rec, patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ):
        second = svc.complete_bill(BILL_UUID)
    assert second["bill_finalized"] is True
    rec.assert_not_called()
    intake.record_final_on_complete_failure_isolated.assert_not_called()


def test_completion_succeeds_when_final_record_raises():
    intake = BillIntakePredictionService()
    intake.repo = MagicMock()
    intake.repo.record_final.side_effect = RuntimeError("db")
    intake.record_final_on_complete_failure_isolated(bill=_bill(), line=_line())


def test_final_line_is_attachment_line_not_first():
    lines = [
        _line(id=1, public_id="line-1", sub_cost_code_id=111),
        _line(id=2, public_id="line-2", sub_cost_code_id=222),
    ]

    def _read_links(public_ids: list[str]) -> list[BillLineItemAttachment]:
        return [
            BillLineItemAttachment(
                id=1,
                public_id="bla-1",
                row_version=None,
                created_datetime=None,
                modified_datetime=None,
                bill_line_item_id=2,
                attachment_id=99,
                bill_line_item_public_id="line-2",
            )
        ]

    from entities.bill_intake_prediction.business.service import resolve_intake_final_line

    picked = resolve_intake_final_line(
        lines,
        target_attachment_id=99,
        read_attachment_links_for_line_items=_read_links,
    )
    assert picked is not None and picked.sub_cost_code_id == 222


def test_final_line_falls_back_to_first_when_no_target_attachment():
    lines = [_line(id=1, sub_cost_code_id=111), _line(id=2, sub_cost_code_id=222)]
    from entities.bill_intake_prediction.business.service import resolve_intake_final_line

    picked = resolve_intake_final_line(
        lines,
        target_attachment_id=None,
        read_attachment_links_for_line_items=lambda _p: [],
    )
    assert picked is not None and picked.sub_cost_code_id == 111


def test_tick_reuses_matching_prediction_without_model_call():
    claim = BillTextCodingClaim(
        bill_id=10,
        attachment_id=99,
        extracted_text_blob_url="https://x",
        extracted_datetime=datetime(2026, 5, 1, 12, 0, 0),
        vendor_id=5,
        chosen_project_id=100,
        bill_date=None,
        created_datetime=datetime(2026, 5, 1),
    )
    repo = MagicMock()
    repo.complete.return_value = SimpleNamespace()
    repo.read_vendor_scc_counts.return_value = []
    with patch(
        "entities.coding_suggestion.business.bill_text_suggestion_service.history_eligible_for_text",
        return_value=(True, None),
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService"
    ) as intake_cls, patch(
        "entities.coding_suggestion.business.bill_text_suggestion_service.run_bill_coding_suggestion",
        new=AsyncMock(),
    ) as run_model:
        intake_cls.return_value.read_by_attachment_id.return_value = _prediction()
        out = asyncio.run(
            _process_one_claim(
                claim,
                repository=repo,
                storage=MagicMock(),
                suggester=MagicMock(),
                catalog_loader=lambda _asof: MagicMock(),
                transport_for=None,
            )
        )
    run_model.assert_not_called()
    repo.complete.assert_called_once()
    assert repo.complete.call_args.kwargs.get("record_call") is False
    assert out["status"] == "done"


def test_tick_does_not_reuse_on_extracted_datetime_mismatch():
    claim = BillTextCodingClaim(
        bill_id=10,
        attachment_id=99,
        extracted_text_blob_url="https://x",
        extracted_datetime=datetime(2026, 6, 1, 12, 0, 0),
        vendor_id=5,
        chosen_project_id=100,
        bill_date=None,
        created_datetime=datetime(2026, 5, 1),
    )
    repo = MagicMock()
    with patch(
        "entities.coding_suggestion.business.bill_text_suggestion_service.history_eligible_for_text",
        return_value=(True, None),
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService"
    ) as intake_cls, patch(
        "entities.coding_suggestion.business.bill_text_suggestion_service.read_text_blob_with_retry",
        return_value="masked",
    ), patch(
        "entities.coding_suggestion.business.bill_text_suggestion_service.blob_has_current_redaction",
        return_value=True,
    ), patch(
        "entities.coding_suggestion.business.bill_text_suggestion_service.run_bill_coding_suggestion",
        new=AsyncMock(
            return_value=CascadeResult(
                accepted=False,
                needs_human=True,
                result=None,
                confidence=None,
                winning_rung=None,
                attempts=[],
            )
        ),
    ):
        intake_cls.return_value.read_by_attachment_id.return_value = _prediction()
        asyncio.run(
            _process_one_claim(
                claim,
                repository=repo,
                storage=MagicMock(),
                suggester=MagicMock(),
                catalog_loader=lambda _asof: MagicMock(
                    vendor_name="v",
                    scc_number_to_id={},
                    proj_abbrev_to_id={},
                ),
                transport_for=None,
            )
        )
    repo.complete.assert_called()


def test_read_route_404_inaccessible_and_absent():
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(intake_prediction_router)
    dep = inspect.signature(read_bill_intake_prediction_router).parameters["_"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "u1"}
    client = TestClient(app, raise_server_exceptions=False)
    with patch("entities.bill_intake_prediction.api.router.BillService") as bill_cls:
        bill_cls.return_value.read_by_public_id.side_effect = EntityNotAccessibleError(
            "Bill", 10
        )
        assert client.get(f"/api/v1/bill/{BILL_UUID}/intake-prediction").status_code == 404
        bill_cls.return_value.read_by_public_id.side_effect = None
        bill_cls.return_value.read_by_public_id.return_value = _bill()
        with patch(
            "entities.bill_intake_prediction.api.router.BillIntakePredictionService"
        ) as intake_cls:
            intake_cls.return_value.read_by_bill_id.return_value = None
            assert client.get(f"/api/v1/bill/{BILL_UUID}/intake-prediction").status_code == 404


def test_read_route_never_exposes_cost_or_final_fields():
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(intake_prediction_router)
    dep = inspect.signature(read_bill_intake_prediction_router).parameters["_"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "u1"}
    client = TestClient(app, raise_server_exceptions=False)
    rich = _prediction(
        pred_sub_cost_code_id=None,
        pred_project_id=None,
        cost_usd=Decimal("9.99"),
        input_tokens=999,
        output_tokens=888,
        latency_ms_total=1234,
        timing_json='{"total":1}',
        final_vendor_id=1,
        final_bill_number="X",
        finalized_datetime=datetime(2026, 5, 2),
    )
    with patch("entities.bill_intake_prediction.api.router.BillService") as bill_cls, patch(
        "entities.bill_intake_prediction.api.router.BillIntakePredictionService"
    ) as intake_cls, patch(
        "entities.bill_intake_prediction.business.service.VendorService"
    ) as vendor_cls:
        vendor_cls.return_value.read_by_id.return_value = SimpleNamespace(
            public_id="v-pid", name="V"
        )
        bill_cls.return_value.read_by_public_id.return_value = _bill()
        intake_svc = BillIntakePredictionService()
        intake_cls.return_value = intake_svc
        with patch.object(intake_svc, "read_by_bill_id", return_value=rich):
            body = client.get(f"/api/v1/bill/{BILL_UUID}/intake-prediction").json()
    item = body["data"]
    blob = json.dumps(item).lower()
    for forbidden in (
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "latency",
        "timing",
        "final_",
        "finalized",
    ):
        assert forbidden not in blob


def test_accuracy_aggregation_math():
    rows = [
        {
            "pred_vendor_id": 1,
            "final_vendor_id": 1,
            "pred_bill_number": " inv-1 ",
            "final_bill_number": "INV1",
            "pred_bill_date": date(2026, 5, 1),
            "final_bill_date": date(2026, 5, 1),
            "pred_total_amount": Decimal("100.00"),
            "final_total_amount": Decimal("100.00"),
            "pred_sub_cost_code_id": 501,
            "final_sub_cost_code_id": 501,
            "pred_project_id": 100,
            "final_project_id": 100,
            "cost_usd": Decimal("0.01"),
            "latency_ms_total": 50,
            "vendor_name": "Acme",
        },
        {
            "pred_vendor_id": 2,
            "final_vendor_id": 1,
            "pred_bill_number": "X",
            "final_bill_number": "Y",
            "pred_bill_date": date(2026, 5, 1),
            "final_bill_date": date(2026, 5, 2),
            "pred_total_amount": Decimal("1.00"),
            "final_total_amount": Decimal("2.00"),
            "pred_sub_cost_code_id": 1,
            "final_sub_cost_code_id": 2,
            "pred_project_id": 1,
            "final_project_id": 2,
            "cost_usd": Decimal("0.03"),
            "latency_ms_total": 150,
            "vendor_name": "Other",
        },
    ]
    report = aggregate_prediction_accuracy(rows)
    vendor_field = next(f for f in report.fields if f.label == "vendor_id")
    assert vendor_field.n_with_final == 2 and vendor_field.n_match == 1
    assert report.overall_match_rate == 0.5
    assert report.mean_cost_usd == Decimal("0.02")
    assert report.mean_latency_ms == 100.0


def test_tick_reuse_gate_rejects_mode_project_and_missing_scc():
    extracted = datetime(2026, 5, 1, 12, 0, 0)
    assert tick_can_reuse_prediction(
        _prediction(), extracted_datetime=extracted, chosen_project_id=100, vendor_id=5
    )
    assert not tick_can_reuse_prediction(
        _prediction(coding_chosen_project_id=999),
        extracted_datetime=extracted,
        chosen_project_id=100,
        vendor_id=5,
    )
    assert not tick_can_reuse_prediction(
        _prediction(extracted_datetime=datetime(2026, 6, 1)),
        extracted_datetime=extracted,
        chosen_project_id=100,
        vendor_id=5,
    )
    assert not tick_can_reuse_prediction(
        _prediction(pred_sub_cost_code_id=None),
        extracted_datetime=extracted,
        chosen_project_id=100,
        vendor_id=5,
    )
    assert not tick_can_reuse_prediction(
        _prediction(pred_vendor_id=999),
        extracted_datetime=extracted,
        chosen_project_id=100,
        vendor_id=5,
    )
    assert not tick_can_reuse_prediction(
        _prediction(coding_mode="B", coding_chosen_project_id=None, pred_project_id=None),
        extracted_datetime=extracted,
        chosen_project_id=None,
        vendor_id=5,
    )


def test_tick_reuse_requires_extracted_datetime_millisecond_match():
    extracted = datetime(2026, 5, 1, 12, 0, 0, 500000)
    stored = datetime(2026, 5, 1, 12, 0, 0)
    assert not tick_can_reuse_prediction(
        _prediction(extracted_datetime=stored),
        extracted_datetime=extracted,
        chosen_project_id=100,
        vendor_id=5,
    )
    assert tick_can_reuse_prediction(
        _prediction(extracted_datetime=extracted),
        extracted_datetime=extracted,
        chosen_project_id=100,
        vendor_id=5,
    )


def test_text_model_version_matches_coding_service():
    assert TEXT_MODEL_VERSION == MODEL_VERSION


def test_cost_summation_uses_decimal():
    usage = Usage(input_tokens=1000, output_tokens=500)
    cascade = CascadeResult(
        accepted=True,
        needs_human=False,
        result={},
        confidence=0.9,
        winning_rung=Rung("anthropic", "claude-haiku-5-5"),
        attempts=[
            Attempt(
                rung=Rung("anthropic", "claude-haiku-5-5"),
                accepted=True,
                usage=usage,
            )
        ],
    )
    inp, out, cost = sum_cascade_cost_and_tokens(cascade)
    assert inp == 1000 and out == 500
    assert isinstance(cost, Decimal)
    assert cost is not None and cost > 0


def test_cost_none_when_any_attempt_price_unknown():
    usage = Usage(input_tokens=10, output_tokens=5)
    cascade = CascadeResult(
        accepted=True,
        needs_human=False,
        result={},
        confidence=0.9,
        winning_rung=Rung("anthropic", "unknown-model-xyz"),
        attempts=[
            Attempt(
                rung=Rung("anthropic", "unknown-model-xyz"),
                accepted=True,
                usage=usage,
            )
        ],
    )
    _, _, cost = sum_cascade_cost_and_tokens(cascade)
    assert cost is None


def test_predict_text_coding_returns_cascade_on_rejected():
    usage = Usage(input_tokens=42, output_tokens=7)
    cascade = CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=Rung("anthropic", "claude-haiku-5-5"),
        attempts=[
            Attempt(
                rung=Rung("anthropic", "claude-haiku-5-5"),
                accepted=False,
                usage=usage,
            )
        ],
    )
    with patch.object(bts, "run_bill_coding_suggestion", new=AsyncMock(return_value=cascade)), patch.object(
        bts, "BillTextCodingSuggestionRepository"
    ) as repo_cls, patch.object(bts, "load_catalog_from_db", return_value=MagicMock(vendor_name="v")):
        repo_cls.return_value.count_calls_today.return_value = 0
        repo_cls.return_value.read_vendor_scc_counts.return_value = []
        suggestion, stopped, out_cascade = asyncio.run(
            predict_text_coding(
                bill_text="t",
                vendor_id=1,
                chosen_project_id=None,
                as_of=datetime(2026, 5, 1),
            )
        )
    assert suggestion is None and stopped is None
    assert out_cascade is cascade
    inp, out, _ = sum_cascade_cost_and_tokens(out_cascade)
    assert inp == 42 and out == 7


def test_accuracy_excludes_rows_with_all_null_finals():
    rows = [
        {
            "pred_vendor_id": 1,
            "final_vendor_id": None,
            "final_bill_number": "  ",
            "pred_bill_number": "A",
            "pred_bill_date": None,
            "final_bill_date": None,
            "pred_total_amount": None,
            "final_total_amount": None,
            "pred_sub_cost_code_id": None,
            "final_sub_cost_code_id": None,
            "pred_project_id": None,
            "final_project_id": None,
        },
        {
            "pred_vendor_id": 1,
            "final_vendor_id": 1,
            "pred_bill_number": "A",
            "final_bill_number": "A",
            "pred_bill_date": date(2026, 5, 1),
            "final_bill_date": date(2026, 5, 1),
            "pred_total_amount": Decimal("1"),
            "final_total_amount": Decimal("1"),
            "pred_sub_cost_code_id": 1,
            "final_sub_cost_code_id": 1,
            "pred_project_id": 1,
            "final_project_id": 1,
        },
    ]
    report = aggregate_prediction_accuracy(rows)
    assert report.n_rows == 1
    assert report.overall_match_rate == 1.0


async def _await_record_read_tasks() -> None:
    pending = list(ras._record_read_tasks)
    if pending:
        await asyncio.gather(*(asyncio.wrap_future(f) for f in pending), return_exceptions=True)


async def _drained(coro):
    """Run a read and drain its background recording in the SAME loop (a pending
    executor future is abandoned when asyncio.run closes the loop)."""
    out = await coro
    await _await_record_read_tasks()
    return out


def _ok_header_cascade(**fields) -> CascadeResult:
    base = {
        "vendor_name": "Acme Supply",
        "bill_number": "INV-1",
        "bill_date": "2026-05-01",
        "total_amount": "100.00",
        "description": "Materials",
        "confidence": 0.9,
    }
    base.update(fields)
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result=base,
        confidence=0.9,
        winning_rung=Rung(provider="anthropic", model="claude-haiku-5-5"),
        attempts=[],
    )


def _baseline_ok_read_payload(
    intake_prediction_service=None,
    *,
    await_record: bool = True,
) -> dict:
    att = SimpleNamespace(id=7, public_id=ATT_UUID)
    vendor = SimpleNamespace(id=10, public_id="cccccccc-cccc-cccc-cccc-cccccccccccc", name="Acme")
    history = BillSuggestion(
        project_id=100,
        sub_cost_code_id=501,
        sub_cost_code_public_id="11111111-1111-1111-1111-000000000501",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="vendor_project_history",
        reason="history",
        confidence=Decimal("0.85"),
        raw_score=Decimal("0.85"),
        top_count=3,
        second_count=1,
        total_count=4,
        text_mode="A",
        text_model="haiku-coding",
    )
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.project_service.read_by_id.return_value = SimpleNamespace(
        id=100, public_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", name="P"
    )
    suggester.sub_cost_code_service = MagicMock()
    proj_uuid = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    intake = intake_prediction_service or MagicMock(spec=BillIntakePredictionService)

    async def _run():
        with patch.object(ras, "config") as cfg, patch.object(
            ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
        ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
            ras, "AttachmentExtractionService"
        ) as ES, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch(
            "entities.bill.business.read_attachment_service.run_bill_header_extraction",
            new=AsyncMock(return_value=_ok_header_cascade()),
        ), patch(
            "entities.bill.business.read_attachment_service.history_eligible_for_text",
            return_value=(False, "history_strong"),
        ), patch.object(
            ras, "resolve_accessible_text_project_id", return_value=100
        ), patch.object(
            ras, "load_catalog_from_db", return_value=MagicMock(vendor_name="Acme")
        ), patch.object(
            ras, "VendorService"
        ) as VS, patch.object(ras, "ProjectService") as PS, patch.object(
            ras, "assert_can_access_project"
        ), patch(
            "entities.coding_suggestion.business.presenters.ProjectService"
        ) as PSapi:
            cfg.Settings.return_value.bill_drop_prefill_enabled = True
            cfg.Settings.return_value.bill_drop_prefill_timeout_seconds = 30
            att_cls.return_value.read_by_public_id.return_value = att
            ES.return_value.extract_for_bill_drop_prefill.return_value = {
                "status": "completed",
                "masked_text": "invoice body text",
            }
            from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

            rvm.return_value = VendorResolveMatch(
                public_id=str(vendor.public_id), name="Acme", confidence=0.8, vendor_id=10
            )
            VS.return_value.read_by_public_id.return_value = vendor
            PS.return_value.read_by_public_id.return_value = SimpleNamespace(
                id=100, public_id=proj_uuid, name="P"
            )
            PSapi.return_value.read_by_id.return_value = SimpleNamespace(
                id=100, public_id=str(proj_uuid), name="P"
            )
            out = await ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                project_public_id=proj_uuid,
                suggester=suggester,
                intake_prediction_service=intake,
            )
            if await_record:
                # Same loop: a task left pending is cancelled when asyncio.run closes.
                await _await_record_read_tasks()
            return out

    return asyncio.run(_run())


def _json_payload(d: dict) -> str:
    return json.dumps(d, sort_keys=True, default=str)


def test_read_ok_payload_unchanged_when_build_raises():
    baseline = _baseline_ok_read_payload()
    svc = BillIntakePredictionService()
    svc.build_upsert_params_from_read = MagicMock(side_effect=RuntimeError("build"))
    result = _baseline_ok_read_payload(intake_prediction_service=svc)
    assert _json_payload(result) == _json_payload(baseline)


def test_read_ok_payload_unchanged_when_upsert_raises():
    baseline = _baseline_ok_read_payload()
    svc = BillIntakePredictionService()
    svc.repo = MagicMock()
    svc.repo.upsert.side_effect = RuntimeError("db")
    result = _baseline_ok_read_payload(intake_prediction_service=svc)
    assert _json_payload(result) == _json_payload(baseline)


def test_slow_record_read_does_not_delay_or_change_ok_payload():
    """Slow persistence must not extend the wait_for envelope (N1)."""
    import time

    baseline = _baseline_ok_read_payload()
    svc = BillIntakePredictionService()

    def _slow_upsert(**_kwargs):
        time.sleep(2.0)

    svc.repo = MagicMock()
    svc.repo.upsert.side_effect = _slow_upsert

    att = SimpleNamespace(id=7, public_id=ATT_UUID)
    vendor = SimpleNamespace(id=10, public_id="cccccccc-cccc-cccc-cccc-cccccccccccc", name="Acme")
    history = BillSuggestion(
        project_id=100,
        sub_cost_code_id=501,
        sub_cost_code_public_id="11111111-1111-1111-1111-000000000501",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="vendor_project_history",
        reason="history",
        confidence=Decimal("0.85"),
        raw_score=Decimal("0.85"),
        top_count=3,
        second_count=1,
        total_count=4,
        text_mode="A",
        text_model="haiku-coding",
    )
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.project_service.read_by_id.return_value = SimpleNamespace(
        id=100, public_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", name="P"
    )
    suggester.sub_cost_code_service = MagicMock()
    proj_uuid = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    cfg = MagicMock(
        bill_drop_prefill_enabled=True,
        bill_drop_prefill_timeout_seconds=1,
    )

    async def _run():
        with patch.object(ras, "config") as cfg_mod, patch.object(
            ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
        ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
            ras, "AttachmentExtractionService"
        ) as ES, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch(
            "entities.bill.business.read_attachment_service.run_bill_header_extraction",
            new=AsyncMock(return_value=_ok_header_cascade()),
        ), patch(
            "entities.bill.business.read_attachment_service.history_eligible_for_text",
            return_value=(False, "history_strong"),
        ), patch.object(ras, "resolve_accessible_text_project_id", return_value=100), patch.object(
            ras, "load_catalog_from_db", return_value=MagicMock(vendor_name="Acme")
        ), patch.object(ras, "VendorService") as VS, patch.object(ras, "ProjectService") as PS, patch.object(
            ras, "assert_can_access_project"
        ), patch(
            "entities.coding_suggestion.business.presenters.ProjectService"
        ) as PSapi:
            cfg_mod.Settings.return_value = cfg
            att_cls.return_value.read_by_public_id.return_value = att
            ES.return_value.extract_for_bill_drop_prefill.return_value = {
                "status": "completed",
                "masked_text": "invoice body text",
            }
            from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

            rvm.return_value = VendorResolveMatch(
                public_id=str(vendor.public_id), name="Acme", confidence=0.8, vendor_id=10
            )
            VS.return_value.read_by_public_id.return_value = vendor
            PS.return_value.read_by_public_id.return_value = SimpleNamespace(
                id=100, public_id=proj_uuid, name="P"
            )
            PSapi.return_value.read_by_id.return_value = SimpleNamespace(
                id=100, public_id=str(proj_uuid), name="P"
            )
            start = time.monotonic()
            out = await asyncio.wait_for(
                ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    project_public_id=proj_uuid,
                    settings=cfg,
                    suggester=suggester,
                    intake_prediction_service=svc,
                ),
                timeout=max(1, int(cfg.bill_drop_prefill_timeout_seconds)),
            )
            elapsed = time.monotonic() - start
            assert elapsed < 0.75
            await _await_record_read_tasks()
            return out

    result = asyncio.run(_run())
    assert result["status"] == "ok"
    assert _json_payload(result) == _json_payload(baseline)
    svc.repo.upsert.assert_called_once()


def test_record_read_task_retained_until_done():
    import threading

    intake = MagicMock(spec=BillIntakePredictionService)
    gate = threading.Event()

    def _block(**_kwargs):
        gate.wait(timeout=5)

    intake.record_read.side_effect = _block
    ras._record_read_tasks.clear()

    async def _run():
        with patch.object(ras, "config") as cfg, patch.object(
            ras.claude_budget, "is_claude_day_stopped_async", new=AsyncMock(return_value=False)
        ), patch.object(ras, "AttachmentService") as att_cls, patch.object(
            ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
        ), patch.object(
            ras, "_extract_with_inflight_wait",
            new=AsyncMock(return_value={"status": "timeout"}),
        ):
            cfg.Settings.return_value.bill_drop_prefill_enabled = True
            cfg.Settings.return_value.bill_drop_prefill_timeout_seconds = 30
            att_cls.return_value.read_by_public_id.return_value = SimpleNamespace(id=7)
            await ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                intake_prediction_service=intake,
            )
            assert len(ras._record_read_tasks) == 1
            gate.set()
            await _await_record_read_tasks()
            assert len(ras._record_read_tasks) == 0

    asyncio.run(_run())


def test_record_read_ok_path_field_mapping():
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)

    intake = MagicMock(spec=BillIntakePredictionService)
    intake.record_read.side_effect = _capture
    _baseline_ok_read_payload(intake_prediction_service=intake)
    assert captured["vendor_id"] == 10
    assert captured["vendor_extracted_name"] == "Acme Supply"
    assert captured["fields"]["bill_number"]["value"] == "INV-1"
    assert captured["suggestion"].sub_cost_code_id == 501
    assert captured["suggestion"].project_id == 100
    assert captured["suggestion"].text_mode == "A"
    assert captured["header_model"] == "claude-haiku-5-5"


def test_cascade_usage_empty_attempts_is_zero_cost():
    empty = CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[],
    )
    _, _, cost = cascade_usage_totals(empty)
    assert cost == Decimal("0")


def test_cost_header_only_empty_coding_cascade():
    usage = Usage(input_tokens=100, output_tokens=50)
    header = CascadeResult(
        accepted=True,
        needs_human=False,
        result={},
        confidence=0.9,
        winning_rung=Rung("anthropic", "claude-haiku-5-5"),
        attempts=[
            Attempt(
                rung=Rung("anthropic", "claude-haiku-5-5"),
                accepted=True,
                usage=usage,
            )
        ],
    )
    coding_empty = CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[],
    )
    _, _, cost = sum_cascade_cost_and_tokens(header, coding_empty)
    assert cost is not None and cost > Decimal("0")


def _prefill_settings_patch():
    return patch.object(ras.config, "Settings", return_value=MagicMock(
        bill_drop_prefill_enabled=True,
        bill_drop_prefill_timeout_seconds=30,
        bill_coding_text_daily_call_cap=999,
    ))


def _priced_coding_cascade() -> CascadeResult:
    rung = Rung("anthropic", "claude-haiku-5-5")
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result={"scc_number": "05.100", "project_abbreviation": "P1"},
        confidence=0.9,
        winning_rung=rung,
        attempts=[Attempt(rung=rung, accepted=True, usage=Usage(input_tokens=1000, output_tokens=20))],
    )


def _predict_text_coding_raising(*, mode: str, retain: bool, patches) -> tuple[Exception, CascadeResult]:
    """Run the REAL predict_text_coding with a cascade that ran, then fail afterwards."""
    cascade = _priced_coding_cascade()
    repo = MagicMock()
    repo.count_calls_today.return_value = 0
    repo.read_vendor_scc_counts.return_value = []
    catalog = SimpleNamespace(vendor_name="Acme", scc_number_to_id={"05.100": 501}, proj_abbrev_to_id={"P1": 100})
    with _prefill_settings_patch(), patch.object(
        bts, "build_runtime_payload", return_value=({"bill_text": "t"}, mode)
    ), patch.object(bts, "run_bill_coding_suggestion", new=AsyncMock(return_value=cascade)), patches:
        with pytest.raises(RuntimeError) as info:
            asyncio.run(
                bts.predict_text_coding(
                    bill_text="t",
                    vendor_id=10,
                    chosen_project_id=100 if mode == "A" else None,
                    as_of=datetime(2026, 5, 1, tzinfo=timezone.utc),
                    text_repo=repo,
                    suggester=MagicMock(),
                    catalog=catalog,
                    retain_accessible_project=retain,
                )
            )
    return info.value, cascade


def test_predict_text_coding_reraises_mapping_failure_with_cascade():
    err, cascade = _predict_text_coding_raising(
        mode="A",
        retain=False,
        patches=patch(
            "entities.coding_suggestion.business.bill_suggestion_service.bill_suggestion_from_text_cascade",
            side_effect=RuntimeError("map fail"),
        ),
    )
    assert str(err) == "map fail"
    assert err.coding_cascade is cascade


def test_predict_text_coding_reraises_retain_branch_failure_with_cascade():
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch(
        "entities.coding_suggestion.business.bill_suggestion_service.bill_suggestion_from_text_cascade",
        return_value=None,
    ))
    stack.enter_context(patch.object(bts, "resolve_text_prediction", return_value=(501, 100)))
    stack.enter_context(patch(
        "entities.coding_suggestion.business.bill_suggestion_service.resolve_accessible_text_project_id",
        side_effect=RuntimeError("access fail"),
    ))
    err, cascade = _predict_text_coding_raising(mode="B", retain=True, patches=stack)
    assert str(err) == "access fail"
    assert err.coding_cascade is cascade


def _read_with_coding_failure(*, mode_a: bool) -> tuple[dict, dict]:
    """Read path with predict_text_coding raising a tagged error; returns (payload, record kwargs)."""
    cascade = _priced_coding_cascade()
    err = RuntimeError("coding blew up")
    err.coding_cascade = cascade
    att = SimpleNamespace(id=1, public_id=ATT_UUID)
    vendor = SimpleNamespace(id=10, public_id="v-pid", name="Acme")
    weak = BillSuggestion(
        project_id=100 if mode_a else None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
    )
    suggester = MagicMock()
    suggester.suggest.return_value = weak
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()
    captured: dict = {}
    intake = MagicMock(spec=BillIntakePredictionService)
    intake.record_read.side_effect = lambda **kw: captured.update(kw)
    proj_uuid = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    with _prefill_settings_patch(), patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ), patch.object(ras, "predict_text_coding", new=AsyncMock(side_effect=err)) as ptc, patch.object(
        ras, "load_catalog_from_db", return_value=MagicMock(vendor_name="Acme")
    ), patch(
        "entities.bill.business.read_attachment_service.history_eligible_for_text",
        return_value=(True, None),
    ), patch.object(ras, "VendorService") as VS, patch.object(ras, "ProjectService") as PS, patch.object(
        ras, "assert_can_access_project"
    ):
        from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "text",
        }
        rvm.return_value = VendorResolveMatch(public_id="v-pid", name="Acme", confidence=0.8, vendor_id=10)
        VS.return_value.read_by_public_id.return_value = vendor
        PS.return_value.read_by_public_id.return_value = SimpleNamespace(id=100, public_id=proj_uuid)
        out = asyncio.run(
            _drained(ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                project_public_id=proj_uuid if mode_a else None,
                suggester=suggester,
                intake_prediction_service=intake,
            ))
        )
    assert ptc.await_count == 1
    return out, captured


def test_read_mode_a_coding_failure_is_partial_and_records_cascade_cost():
    out, captured = _read_with_coding_failure(mode_a=True)
    assert out["status"] == "partial"
    assert captured["coding_cascade"].attempts[0].usage.input_tokens == 1000


def test_read_mode_b_coding_failure_is_partial_and_records_cascade_cost():
    out, captured = _read_with_coding_failure(mode_a=False)
    assert out["status"] == "partial"
    assert captured["coding_cascade"].attempts[0].usage.input_tokens == 1000


def test_complete_bill_same_result_when_intake_read_raises():
    intake = MagicMock()
    intake.read_by_bill_id.side_effect = RuntimeError("read fail")
    svc = _complete_bill_fixture(intake=intake)
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ):
        baseline = svc.complete_bill(BILL_UUID)
    intake.read_by_bill_id.side_effect = None
    intake.read_by_bill_id.return_value = _prediction()
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ):
        ok = svc.complete_bill(BILL_UUID)
    assert ok == baseline


def test_complete_bill_same_result_when_line_read_raises():
    intake = MagicMock()
    intake.read_by_bill_id.return_value = _prediction()
    svc = _complete_bill_fixture(intake=intake)
    svc.bill_line_item_service.read_by_bill_id.side_effect = [
        RuntimeError("lines"),
        [_line(is_draft=False)],
    ]
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ):
        err = svc.complete_bill(BILL_UUID)
    svc.bill_line_item_service.read_by_bill_id.side_effect = None
    svc.bill_line_item_service.read_by_bill_id.return_value = [_line(is_draft=False)]
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ):
        ok = svc.complete_bill(BILL_UUID)
    assert ok == err


def test_complete_bill_same_result_when_attachment_links_raise():
    intake = MagicMock()
    intake.read_by_bill_id.return_value = _prediction()
    svc = _complete_bill_fixture(intake=intake)
    svc.bill_line_item_attachment_service.read_by_bill_line_item_ids.side_effect = RuntimeError(
        "links"
    )
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ):
        err = svc.complete_bill(BILL_UUID)
    svc.bill_line_item_attachment_service.read_by_bill_line_item_ids.side_effect = None
    svc.bill_line_item_attachment_service.read_by_bill_line_item_ids.return_value = []
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.record_bill_coding_suggestion_on_save"
    ), patch(
        "entities.bill_intake_prediction.business.service.BillIntakePredictionService",
        return_value=intake,
    ):
        ok = svc.complete_bill(BILL_UUID)
    assert ok == err


def test_accuracy_empty_input_has_no_fake_overall_field():
    report = aggregate_prediction_accuracy([])
    assert report.fields == ()
    assert report.n_rows == 0
    assert report.overall_match_rate is None


def test_no_model_calls_store_zero_tokens_and_zero_cost():
    params = BillIntakePredictionService().build_upsert_params_from_read(
        attachment_id=7,
        intake_source="pdf_drop",
        status="unreadable",
        timing_ms={},
        fields={},
        suggestion=None,
        chosen_project_id=None,
        header_cascade=None,
        coding_cascade=None,
        header_model=None,
        vendor_id=None,
        vendor_extracted_name=None,
    )
    assert params["InputTokens"] == 0
    assert params["OutputTokens"] == 0
    assert params["CostUsd"] == Decimal("0")


def test_record_read_entry_cleared_even_if_loop_closes_first():
    """A recording still running when its event loop closes must still leave the set."""
    import threading
    import time as _time

    gate = threading.Event()
    intake = MagicMock(spec=BillIntakePredictionService)
    intake.record_read.side_effect = lambda **_k: gate.wait(timeout=5)
    ras._record_read_tasks.clear()

    async def _run():
        ras._schedule_record_read(intake, attachment_id=7)

    asyncio.run(_run())  # loop closes while the write is still blocked
    assert len(ras._record_read_tasks) == 1
    gate.set()
    deadline = _time.monotonic() + 5
    while ras._record_read_tasks and _time.monotonic() < deadline:
        _time.sleep(0.01)
    assert len(ras._record_read_tasks) == 0
