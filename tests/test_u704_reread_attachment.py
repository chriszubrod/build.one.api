"""U-704: POST /bill/{public_id}/reread-attachment (fakes).

UpsertBillIntakePrediction is keyed on AttachmentId and only updates when BillId and
FinalizedDatetime are both null; reread records intake_source ``reread`` only for attachments
without a bound row yet (see reread_attachment_service module docstring).
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.api.reread_attachment_router import (
    reread_bill_attachment_router,
    router as reread_router,
)
from entities.bill.business import read_attachment_service as ras
from entities.bill.business.reread_attachment_service import reread_draft_bill_attachment
from entities.bill_intake_prediction.business.service import BillIntakePredictionService
from shared.access import EntityNotAccessibleError

BILL_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
OTHER_BILL_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_ATT_UUID = "11111111-bbbb-cccc-dddd-eeeeeeeeeeee"
PROJ_UUID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
LINE1_UUID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
LINE2_UUID = "ffffffff-ffff-ffff-ffff-ffffffffffff"


def _bill(*, status="draft", bill_id=100, is_draft=True):
    return SimpleNamespace(
        id=bill_id,
        public_id=BILL_UUID,
        is_draft=is_draft,
        status=status,
    )


def _line(*, line_id, public_id, project_id=None):
    return SimpleNamespace(id=line_id, public_id=public_id, project_id=project_id)


def _attachment(*, att_id=1, public_id=ATT_UUID):
    return SimpleNamespace(
        id=att_id,
        public_id=public_id,
        blob_url="https://blob/in.pdf",
        file_hash="abc",
        content_type="application/pdf",
    )


def _read_ok():
    return {
        "status": "ok",
        "message": None,
        "fields": {
            "vendor": {"value": None, "extracted_name": None, "confidence": None},
            "bill_number": {"value": "INV-1", "confidence": "0.9000"},
            "bill_date": {"value": None, "confidence": None},
            "total_amount": {"value": "100.00", "confidence": "0.9000"},
            "description": {"value": None, "confidence": None},
            "long_description": {"value": None, "confidence": None},
            "reference": {"value": None, "confidence": None},
        },
        "coding": None,
        "model": "claude-haiku-5-5",
    }


@pytest.fixture
def client_can_update() -> TestClient:
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(reread_router)
    dep = inspect.signature(reread_bill_attachment_router).parameters["_"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "u1"}
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_no_update() -> TestClient:
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(reread_router)
    dep = inspect.signature(reread_bill_attachment_router).parameters["_"].default.dependency

    def _deny():
        raise HTTPException(status_code=403, detail="Forbidden")

    app.dependency_overrides[dep] = _deny
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _enable_flag():
    with patch.object(ras.config, "Settings") as mock_settings:
        s = MagicMock()
        s.bill_drop_prefill_enabled = True
        s.bill_drop_prefill_timeout_seconds = 25
        mock_settings.return_value = s
        yield s


async def _await_record_read_tasks() -> None:
    pending = list(ras._record_read_tasks)
    for fut in pending:
        await asyncio.wrap_future(fut)


def _stacked_services(
    *,
    bill=None,
    lines=None,
    links_by_line_public_id=None,
    attachments_by_id=None,
    project=None,
):
    bill_svc = MagicMock()
    if bill is None:
        bill_svc.read_by_public_id.return_value = None
    else:
        bill_svc.read_by_public_id.side_effect = lambda **_: bill
        bill_svc.update_by_public_id.side_effect = AssertionError("bill must not be updated")
        bill_svc.create.side_effect = AssertionError("bill must not be created")

    bli_svc = MagicMock()
    bli_svc.read_by_bill_id.return_value = lines or []
    bli_svc.update_by_public_id.side_effect = AssertionError("line item must not be updated")

    blia_svc = MagicMock()
    links = links_by_line_public_id or {}

    def _links_batch(public_ids: list[str]):
        out = []
        for pid in public_ids:
            link = links.get(pid)
            if link is None:
                continue
            out.append(
                SimpleNamespace(
                    bill_line_item_public_id=pid,
                    bill_line_item_id=getattr(link, "bill_line_item_id", None),
                    attachment_id=link.attachment_id,
                )
            )
        return out

    blia_svc.read_by_bill_line_item_ids.side_effect = _links_batch

    att_svc = MagicMock()
    att_by_id = attachments_by_id or {}

    def _read_att(*, id: int):
        return att_by_id.get(id)

    att_svc.read_by_id.side_effect = _read_att

    proj_svc = MagicMock()
    proj_svc.read_by_id.return_value = project

    return bill_svc, bli_svc, blia_svc, att_svc, proj_svc


def test_rbac_without_can_update_403(client_no_update):
    resp = client_no_update.post(f"/api/v1/bill/{BILL_UUID}/reread-attachment")
    assert resp.status_code == 403


def test_inaccessible_bill_404(client_can_update):
    bill_svc = MagicMock()
    bill_svc.read_by_public_id.side_effect = EntityNotAccessibleError("Bill", 100)

    with patch(
        "entities.bill.business.reread_attachment_service.BillService",
        return_value=bill_svc,
    ):
        resp = client_can_update.post(f"/api/v1/bill/{BILL_UUID}/reread-attachment")
    assert resp.status_code == 404


def test_non_draft_409(client_can_update):
    """CK_Bill_Status_IsDraft keeps is_draft=1 for in_review; gate is status != draft."""
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(status="in_review", is_draft=True),
        lines=[_line(line_id=10, public_id=LINE1_UUID)],
        links_by_line_public_id={
            LINE1_UUID: SimpleNamespace(attachment_id=1),
        },
        attachments_by_id={1: _attachment()},
    )
    with patch(
        "entities.bill.business.reread_attachment_service.BillService",
        return_value=bill_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemService",
        return_value=bli_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemAttachmentService",
        return_value=blia_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.AttachmentService",
        return_value=att_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.ProjectService",
        return_value=proj_svc,
    ):
        resp = client_can_update.post(f"/api/v1/bill/{BILL_UUID}/reread-attachment")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "only draft bills can be re-read"


def test_non_draft_409_before_attachment_lookup(client_can_update):
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(status="submitted", is_draft=True),
        lines=[_line(line_id=10, public_id=LINE1_UUID)],
        links_by_line_public_id={},
    )
    with patch(
        "entities.bill.business.reread_attachment_service.BillService",
        return_value=bill_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemService",
        return_value=bli_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemAttachmentService",
        return_value=blia_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.AttachmentService",
        return_value=att_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.ProjectService",
        return_value=proj_svc,
    ):
        resp = client_can_update.post(f"/api/v1/bill/{BILL_UUID}/reread-attachment")
    assert resp.status_code == 409
    blia_svc.read_by_bill_line_item_ids.assert_not_called()


def test_no_attachment_404(client_can_update):
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(),
        lines=[_line(line_id=10, public_id=LINE1_UUID)],
        links_by_line_public_id={},
    )
    with patch(
        "entities.bill.business.reread_attachment_service.BillService",
        return_value=bill_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemService",
        return_value=bli_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemAttachmentService",
        return_value=blia_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.AttachmentService",
        return_value=att_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.ProjectService",
        return_value=proj_svc,
    ):
        resp = client_can_update.post(f"/api/v1/bill/{BILL_UUID}/reread-attachment")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no attachment"


def test_ai_unavailable_passthrough(client_can_update):
    read_fn = AsyncMock(
        return_value={
            "status": "ai_unavailable",
            "message": ras.MSG_AI_PAUSED,
            "fields": ras._empty_fields(),
            "coding": None,
            "model": None,
        }
    )
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(),
        lines=[_line(line_id=10, public_id=LINE1_UUID)],
        links_by_line_public_id={
            LINE1_UUID: SimpleNamespace(attachment_id=1),
        },
        attachments_by_id={1: _attachment()},
    )
    with patch(
        "entities.bill.business.reread_attachment_service.BillService",
        return_value=bill_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemService",
        return_value=bli_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemAttachmentService",
        return_value=blia_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.AttachmentService",
        return_value=att_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.ProjectService",
        return_value=proj_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=False, is_owned=True),
    ):
        out = asyncio.run(
            reread_draft_bill_attachment(
                bill_public_id=BILL_UUID,
                bill_service=bill_svc,
                line_item_service=bli_svc,
                blia_service=blia_svc,
                attachment_service=att_svc,
                project_service=proj_svc,
                read_fn=read_fn,
            )
        )
    assert out["status"] == "ai_unavailable"
    read_fn.assert_awaited_once()
    assert read_fn.await_args.kwargs["intake_source"] == "reread"
    assert read_fn.await_args.kwargs["pre_gated"] is not None


def test_success_records_reread_and_never_writes_bill():
    intake = MagicMock(spec=BillIntakePredictionService)
    captured: dict = {}

    def _record(**kwargs):
        captured.update(kwargs)

    intake.record_read.side_effect = _record

    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(bill_id=100),
        lines=[
            _line(line_id=5, public_id=LINE1_UUID),
            _line(line_id=20, public_id=LINE2_UUID, project_id=50),
        ],
        links_by_line_public_id={
            LINE2_UUID: SimpleNamespace(attachment_id=2),
        },
        attachments_by_id={2: _attachment(att_id=2, public_id=ATT_UUID)},
        project=SimpleNamespace(public_id=PROJ_UUID),
    )

    read_fn = AsyncMock(return_value=_read_ok())

    with patch(
        "entities.bill.business.reread_attachment_service.read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=False, is_owned=True),
    ):
        out = asyncio.run(
            reread_draft_bill_attachment(
                bill_public_id=BILL_UUID,
                bill_service=bill_svc,
                line_item_service=bli_svc,
                blia_service=blia_svc,
                attachment_service=att_svc,
                project_service=proj_svc,
                read_fn=read_fn,
            )
        )

    assert out["status"] == "ok"
    assert "fields" in out
    read_fn.assert_awaited_once()
    assert read_fn.await_args.kwargs["attachment_public_id"] == ATT_UUID
    assert read_fn.await_args.kwargs["intake_source"] == "reread"
    assert read_fn.await_args.kwargs["project_public_id"] == UUID(PROJ_UUID)
    pre_gated = read_fn.await_args.kwargs["pre_gated"]
    assert pre_gated[0].public_id == ATT_UUID
    assert pre_gated[1] is False
    blia_svc.read_by_bill_line_item_ids.assert_called_once()
    batch_ids = blia_svc.read_by_bill_line_item_ids.call_args[0][0]
    assert batch_ids == [LINE1_UUID, LINE2_UUID]


def test_uses_lowest_line_with_attachment_not_other_bills_attachment():
    """Skip a lower line item without a link; never read another bill's attachment."""
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(bill_id=200),
        lines=[_line(line_id=8, public_id=LINE1_UUID)],
        links_by_line_public_id={
            LINE1_UUID: SimpleNamespace(attachment_id=9),
        },
        attachments_by_id={
            9: _attachment(att_id=9, public_id=ATT_UUID),
            99: _attachment(att_id=99, public_id=OTHER_ATT_UUID),
        },
    )
    read_fn = AsyncMock(return_value=_read_ok())

    with patch(
        "entities.bill.business.reread_attachment_service.read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=False, is_owned=True),
    ):
        asyncio.run(
            reread_draft_bill_attachment(
                bill_public_id=BILL_UUID,
                bill_service=bill_svc,
                line_item_service=bli_svc,
                blia_service=blia_svc,
                attachment_service=att_svc,
                project_service=proj_svc,
                read_fn=read_fn,
            )
        )

    assert read_fn.await_args.kwargs["attachment_public_id"] == ATT_UUID
    att_svc.read_by_id.assert_called_once_with(id=9)


def test_linked_refusal_bypassed_only_via_pre_gated(client_can_update):
    """Linked attachment would 404 on plain read-attachment; reread passes pre_gated."""
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(),
        lines=[_line(line_id=10, public_id=LINE1_UUID)],
        links_by_line_public_id={
            LINE1_UUID: SimpleNamespace(attachment_id=1),
        },
        attachments_by_id={1: _attachment()},
    )

    async def _real_read(**kwargs):
        if kwargs.get("pre_gated") is None:
            raise ras.AttachmentNotReadable("linked")
        return _read_ok()

    with patch(
        "entities.bill.business.reread_attachment_service.BillService",
        return_value=bill_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemService",
        return_value=bli_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemAttachmentService",
        return_value=blia_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.AttachmentService",
        return_value=att_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.ProjectService",
        return_value=proj_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=False, is_owned=True),
    ), patch(
        "entities.bill.business.reread_attachment_service.run_bill_read_attachment_with_timeout",
        side_effect=_real_read,
    ):
        resp = client_can_update.post(f"/api/v1/bill/{BILL_UUID}/reread-attachment")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["status"] == "ok"


def test_intake_source_reread_in_allowed_set():
    from entities.bill_intake_prediction.business.service import INTAKE_SOURCES

    assert "reread" in INTAKE_SOURCES


def test_end_to_end_intake_record_read_reread_source():
    intake = MagicMock(spec=BillIntakePredictionService)
    captured: dict = {}
    intake.record_read.side_effect = lambda **kw: captured.update(kw)

    att = _attachment()
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(bill_id=100),
        lines=[_line(line_id=10, public_id=LINE1_UUID)],
        links_by_line_public_id={
            LINE1_UUID: SimpleNamespace(attachment_id=1),
        },
        attachments_by_id={1: att},
    )

    async def _short_read(**kwargs):
        return await ras.run_bill_read_attachment(
            attachment_public_id=kwargs["attachment_public_id"],
            project_public_id=kwargs.get("project_public_id"),
            intake_source=kwargs["intake_source"],
            pre_gated=kwargs["pre_gated"],
            intake_prediction_service=intake,
            extraction_service=MagicMock(
                extract_for_bill_read=AsyncMock(
                    return_value={
                        "status": "completed",
                        "masked_text": "invoice total 100",
                    }
                )
            ),
            attachment_service=att_svc,
            transport_for=MagicMock(),
            settings=MagicMock(bill_drop_prefill_enabled=True, bill_drop_prefill_timeout_seconds=25),
        )

    with patch.object(
        ras.claude_budget,
        "is_claude_day_stopped_async",
        new=AsyncMock(return_value=False),
    ), patch.object(
        ras,
        "run_bill_header_extraction",
        new=AsyncMock(
            return_value=SimpleNamespace(
                stopped_reason=None,
                confidence=0.9,
                result={
                    "vendor_name": "Acme",
                    "bill_number": "1",
                    "total_amount": "100.00",
                    "confidence": 0.9,
                },
                winning_rung=SimpleNamespace(model="claude-haiku-5-5"),
                attempts=[],
                accepted=True,
            )
        ),
    ), patch.object(
        ras,
        "_header_from_cascade",
        return_value=(ras._empty_fields(), True, "claude-haiku-5-5", None),
    ), patch(
        "entities.bill.business.reread_attachment_service.read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=False, is_owned=True),
    ):
        asyncio.run(
            reread_draft_bill_attachment(
                bill_public_id=BILL_UUID,
                bill_service=bill_svc,
                line_item_service=bli_svc,
                blia_service=blia_svc,
                attachment_service=att_svc,
                project_service=proj_svc,
                read_fn=_short_read,
            )
        )
        asyncio.run(_await_record_read_tasks())

    assert captured.get("intake_source") == "reread"
    assert captured.get("attachment_id") == 1


def test_upsert_params_use_intake_source_reread():
    intake = BillIntakePredictionService()
    params = intake.build_upsert_params_from_read(
        attachment_id=1,
        intake_source="reread",
        status="ok",
        timing_ms={"total": 1},
        fields=ras._empty_fields(),
        suggestion=None,
        chosen_project_id=None,
        header_cascade=None,
        coding_cascade=None,
        header_model="claude-haiku-5-5",
        vendor_id=None,
        vendor_extracted_name=None,
    )
    assert params["IntakeSource"] == "reread"


def test_taxpayer_link_state_reaches_pre_gated_and_extraction():
    """is_taxpayer from link state must flow into pre_gated and extract_for_bill_drop_prefill."""
    intake = MagicMock(spec=BillIntakePredictionService)
    extract_mock = MagicMock(
        extract_for_bill_drop_prefill=MagicMock(
            return_value={"status": "completed", "masked_text": "w9 form"}
        )
    )
    att = _attachment()
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(bill_id=100),
        lines=[_line(line_id=10, public_id=LINE1_UUID)],
        links_by_line_public_id={
            LINE1_UUID: SimpleNamespace(attachment_id=1),
        },
        attachments_by_id={1: att},
    )

    async def _short_read(**kwargs):
        pre = kwargs.get("pre_gated")
        assert pre is not None
        assert pre[1] is True, "pre_gated must carry is_taxpayer=True from link state"
        return await ras.run_bill_read_attachment(
            attachment_public_id=kwargs["attachment_public_id"],
            project_public_id=kwargs.get("project_public_id"),
            intake_source=kwargs["intake_source"],
            pre_gated=kwargs["pre_gated"],
            intake_prediction_service=intake,
            extraction_service=extract_mock,
            attachment_service=att_svc,
            transport_for=MagicMock(),
            settings=MagicMock(bill_drop_prefill_enabled=True, bill_drop_prefill_timeout_seconds=25),
        )

    with patch.object(
        ras.claude_budget,
        "is_claude_day_stopped_async",
        new=AsyncMock(return_value=False),
    ), patch.object(
        ras,
        "run_bill_header_extraction",
        new=AsyncMock(
            return_value=SimpleNamespace(
                stopped_reason=None,
                confidence=0.9,
                result={
                    "vendor_name": "Acme",
                    "bill_number": "1",
                    "total_amount": "100.00",
                    "confidence": 0.9,
                },
                winning_rung=SimpleNamespace(model="claude-haiku-5-5"),
                attempts=[],
                accepted=True,
            )
        ),
    ), patch.object(
        ras,
        "_header_from_cascade",
        return_value=(ras._empty_fields(), True, "claude-haiku-5-5", None),
    ), patch(
        "entities.bill.business.reread_attachment_service.read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=True, is_owned=True),
    ):
        asyncio.run(
            reread_draft_bill_attachment(
                bill_public_id=BILL_UUID,
                bill_service=bill_svc,
                line_item_service=bli_svc,
                blia_service=blia_svc,
                attachment_service=att_svc,
                project_service=proj_svc,
                read_fn=_short_read,
            )
        )

    extract_mock.extract_for_bill_drop_prefill.assert_called_once()
    assert extract_mock.extract_for_bill_drop_prefill.call_args.kwargs["is_taxpayer_document"] is True


def _post_with_services(client, services):
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = services
    with patch(
        "entities.bill.business.reread_attachment_service.BillService",
        return_value=bill_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemService",
        return_value=bli_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.BillLineItemAttachmentService",
        return_value=blia_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.AttachmentService",
        return_value=att_svc,
    ), patch(
        "entities.bill.business.reread_attachment_service.ProjectService",
        return_value=proj_svc,
    ):
        return client.post(f"/api/v1/bill/{BILL_UUID}/reread-attachment")


@pytest.mark.parametrize(
    "status,is_draft",
    [
        ("submitted", True),
        ("in_review", True),
        ("approved", True),
        ("declined", True),
        ("completed", False),
    ],
)
def test_every_non_draft_status_409(client_can_update, status, is_draft):
    """Only Status 'draft' may be re-read; IsDraft is not the gate."""
    services = _stacked_services(
        bill=_bill(status=status, is_draft=is_draft),
        lines=[_line(line_id=10, public_id=LINE1_UUID)],
        links_by_line_public_id={LINE1_UUID: SimpleNamespace(attachment_id=1)},
        attachments_by_id={1: _attachment()},
    )
    resp = _post_with_services(client_can_update, services)
    assert resp.status_code == 409
    assert resp.json()["detail"] == "only draft bills can be re-read"


def test_missing_bill_404_bill_not_found(client_can_update):
    """A bill that does not exist is the bill-routes' 404, not 'no attachment'."""
    services = _stacked_services(bill=None)
    resp = _post_with_services(client_can_update, services)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Bill not found"
    services[2].read_by_bill_line_item_ids.assert_not_called()


def test_two_linked_lines_lowest_id_wins():
    """With both lines linked, the lower-id line's attachment is read (lines arrive unsorted)."""
    bill_svc, bli_svc, blia_svc, att_svc, proj_svc = _stacked_services(
        bill=_bill(),
        lines=[
            _line(line_id=20, public_id=LINE2_UUID),
            _line(line_id=8, public_id=LINE1_UUID),
        ],
        links_by_line_public_id={
            LINE1_UUID: SimpleNamespace(attachment_id=9),
            LINE2_UUID: SimpleNamespace(attachment_id=99),
        },
        attachments_by_id={
            9: _attachment(att_id=9, public_id=ATT_UUID),
            99: _attachment(att_id=99, public_id=OTHER_ATT_UUID),
        },
    )
    read_fn = AsyncMock(return_value=_read_ok())
    with patch(
        "entities.bill.business.reread_attachment_service.read_attachment_link_state",
        return_value=AttachmentLinkState(is_linked=True, is_taxpayer=False, is_owned=True),
    ):
        asyncio.run(
            reread_draft_bill_attachment(
                bill_public_id=BILL_UUID,
                bill_service=bill_svc,
                line_item_service=bli_svc,
                blia_service=blia_svc,
                attachment_service=att_svc,
                project_service=proj_svc,
                read_fn=read_fn,
            )
        )
    assert read_fn.await_args.kwargs["attachment_public_id"] == ATT_UUID
    att_svc.read_by_id.assert_called_once_with(id=9)
