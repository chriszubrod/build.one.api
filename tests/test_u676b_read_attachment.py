"""U-676b: bill create read-attachment endpoint (pure, fakes)."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.attachment.business.extraction_service import (
    AttachmentExtractionService,
    REDACTION_VERSION,
    extraction_payload_is_current,
)
from entities.attachment.business.tin_redaction import mask_tins
from entities.bill.api.read_attachment_router import (
    read_bill_attachment_router,
    router as read_attachment_router,
)
from entities.bill.business import read_attachment_service as ras
from entities.coding_suggestion.business.presenters import suggestion_to_api_payload
from entities.bill.business.read_attachment_service import AttachmentNotReadable
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from entities.coding_suggestion.business.bill_text_suggestion_service import CatalogSnapshot
from intelligence.cascade.core import CascadeResult, Rung

ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROJ_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
VENDOR_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"




@pytest.fixture
def client_can_create() -> TestClient:
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(read_attachment_router)
    dep = inspect.signature(read_bill_attachment_router).parameters["_"].default.dependency
    app.dependency_overrides[dep] = lambda: {"sub": "u1"}
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_no_create() -> TestClient:
    app = FastAPI()
    from shared.api.errors import install_error_handlers

    install_error_handlers(app)
    app.include_router(read_attachment_router)
    dep = inspect.signature(read_bill_attachment_router).parameters["_"].default.dependency

    def _deny():
        raise HTTPException(status_code=403, detail="Forbidden")

    app.dependency_overrides[dep] = _deny
    return TestClient(app, raise_server_exceptions=False)


def _mode_b_catalog(**kw) -> CatalogSnapshot:
    base = dict(
        scc_by_id={
            501: SimpleNamespace(
                number="05.100",
                name="Framing",
                created=datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        },
        scc_number_to_id={"05.100": 501},
        ambiguous_scc_numbers=frozenset(),
        proj_abbrev_to_id={"P1": 100},
        ambiguous_project_abbrevs=frozenset(),
        candidate_projects=[{"abbreviation": "P1", "name": "Proj"}],
        vendor_name="Acme",
    )
    base.update(kw)
    return CatalogSnapshot(**base)


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


def _attachment(**kw):
    base = dict(
        id=1,
        public_id=ATT_UUID,
        blob_url="https://blob/in.pdf",
        file_hash="abc",
        content_type="application/pdf",
        extraction_status="pending",
        extracted_text_blob_url=None,
        parse_invoice_number=False,
        is_taxpayer_document=False,
        claim_modified_datetime=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _enable_flag():
    with patch.object(ras.config, "Settings") as mock_settings:
        s = MagicMock()
        s.bill_drop_prefill_enabled = True
        s.bill_drop_prefill_timeout_seconds = 25
        mock_settings.return_value = s
        yield s


@pytest.fixture(autouse=True)
def _claude_ok():
    with patch.object(
        ras.claude_budget,
        "is_claude_day_stopped_async",
        new=AsyncMock(return_value=False),
    ):
        yield


def test_rbac_without_can_create_403(client_no_create):
    resp = client_no_create.post(
        "/api/v1/bill/read-attachment",
        json={"attachment_public_id": ATT_UUID},
    )
    assert resp.status_code == 403


def test_missing_attachment_404(client_can_create):
    with patch(
        "entities.bill.api.read_attachment_router.run_bill_read_attachment_with_timeout",
        side_effect=AttachmentNotReadable("not_found"),
    ):
        resp = client_can_create.post(
            "/api/v1/bill/read-attachment",
            json={"attachment_public_id": ATT_UUID},
        )
    assert resp.status_code == 404


def test_linked_attachment_404(client_can_create):
    att = _attachment()

    async def _run(**kwargs):
        return await ras.run_bill_read_attachment(
            attachment_public_id=str(kwargs.get("attachment_public_id", ATT_UUID)),
            attachment_service=MagicMock(read_by_public_id=MagicMock(return_value=att)),
        )

    with patch.object(ras, "read_attachment_link_state", return_value=AttachmentLinkState(True, False, True)):
        with patch(
            "entities.bill.api.read_attachment_router.run_bill_read_attachment_with_timeout",
            side_effect=_run,
        ):
            resp = client_can_create.post(
                "/api/v1/bill/read-attachment",
                json={"attachment_public_id": ATT_UUID},
            )
    assert resp.status_code == 404


def test_flag_off_disabled_no_pipeline(client_can_create, _enable_flag):
    _enable_flag.bill_drop_prefill_enabled = False
    with patch(
        "entities.bill.api.read_attachment_router.run_bill_read_attachment_with_timeout",
        wraps=ras.run_bill_read_attachment,
    ) as run:
        with patch.object(ras.claude_budget, "is_claude_day_stopped_async") as cap:
            resp = client_can_create.post(
                "/api/v1/bill/read-attachment",
                json={"attachment_public_id": ATT_UUID},
            )
            cap.assert_not_called()
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "disabled"
    assert data["fields"]["bill_number"]["value"] is None


def test_cap_stopped_ai_unavailable():
    with patch.object(
        ras.claude_budget,
        "is_claude_day_stopped_async",
        new=AsyncMock(return_value=True),
    ):
        out = asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    assert out["status"] == "ai_unavailable"
    assert out["message"] == "AI suggestions are paused for today"


def test_header_cascade_daily_cap_ai_unavailable():
    att = _attachment()
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(
            return_value=CascadeResult(
                accepted=False,
                needs_human=True,
                result=None,
                confidence=None,
                winning_rung=None,
                attempts=[],
                stopped_reason="daily_cap",
            )
        ),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "invoice body",
        }
        out = asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    assert out["status"] == "ai_unavailable"


def test_timeout_envelope(client_can_create):
    async def slow(**kwargs):
        await asyncio.sleep(2)
        return ras._result_shell("ok")

    with patch.object(ras.config, "Settings") as mock_settings:
        s = MagicMock()
        s.bill_drop_prefill_enabled = True
        s.bill_drop_prefill_timeout_seconds = 0.05
        mock_settings.return_value = s
        with patch(
            "entities.bill.business.read_attachment_service.run_bill_read_attachment",
            side_effect=slow,
        ):
            resp = client_can_create.post(
                "/api/v1/bill/read-attachment",
                json={"attachment_public_id": ATT_UUID},
            )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "timeout"


def test_invoice_masking_keeps_amount_masks_ssn():
    text = "Total $1,234.56 Invoice date 2026-01-02 SSN 123-45-6789"
    masked, _ = mask_tins(text, aggressive=False)
    assert "1234.56" in masked or "1,234.56" in masked
    assert "123-45-6789" not in masked


def test_taxpayer_aggressive_masking():
    text = "EIN 12-3456789 Amount 500.00"
    masked_inv, _ = mask_tins(text, aggressive=False)
    masked_tax, _ = mask_tins(text, aggressive=True)
    assert "500.00" in masked_inv
    assert "12-3456789" not in masked_inv
    assert "500.00" not in masked_tax or "12-3456789" not in masked_tax


def test_persist_extraction_uses_explicit_aggressive():
    svc = AttachmentExtractionService(
        repo=MagicMock(),
        storage=MagicMock(),
        di=MagicMock(),
    )
    att = _attachment()
    svc.storage.download_file.return_value = (b"%PDF-1.4\n" + b"x" * 200, {})
    with patch.object(svc, "_persist_bill_drop_extraction_blob") as persist:
        persist.return_value = ("name", "https://blob/out.json", "masked")
        with patch.object(svc, "_commit_terminal", return_value={"status": "completed"}):
            with patch(
                "entities.attachment.business.extraction_service._has_usable_text",
                return_value=True,
            ):
                svc._bill_drop_extract(att, aggressive=False)
    assert persist.call_args.kwargs.get("aggressive") is False


def test_extraction_reused_when_already_completed():
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url="https://blob/ex.json",
    )
    svc = AttachmentExtractionService(repo=MagicMock(), storage=MagicMock())
    blob = json.dumps(
        {
            "content": "masked invoice text long enough for usable here",
            "redaction_version": REDACTION_VERSION,
            "source_file_hash": "abc",
            "masking": "standard",
        }
    ).encode()
    svc.storage.download_file.return_value = (blob, {})
    out = svc.extract_for_bill_drop_prefill(att, is_taxpayer_document=False)
    assert out.get("reused") is True
    assert "masked invoice" in out.get("masked_text", "")


def test_ok_vendor_matched_header_and_history_coding():
    att = _attachment()
    vendor = SimpleNamespace(id=10, public_id=VENDOR_UUID, name="Acme")
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
    )
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.project_service.read_by_id.return_value = SimpleNamespace(id=100, public_id=PROJ_UUID, name="P")
    suggester.sub_cost_code_service = MagicMock()

    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)), patch.object(
        ras, "resolve_vendor_match_from_name"
    ) as rvm, patch.object(ras, "predict_text_coding", new=AsyncMock()) as predict_mock, patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "invoice",
        }
        from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

        rvm.return_value = VendorResolveMatch(
            public_id=VENDOR_UUID, name="Acme", confidence=0.8
        )
        with patch.object(ras, "VendorService") as VS, patch.object(ras, "ProjectService") as PS:
            VS.return_value.read_by_public_id.return_value = vendor
            PS.return_value.read_by_public_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="P"
            )
            with patch.object(ras, "assert_can_access_project"), patch.object(
                ras, "resolve_accessible_text_project_id", return_value=100
            ), patch(
                "entities.coding_suggestion.business.presenters.ProjectService"
            ) as PSapi:
                PSapi.return_value.read_by_id.return_value = SimpleNamespace(
                    id=100, public_id=PROJ_UUID, name="P"
                )
                out = asyncio.run(
                    ras.run_bill_read_attachment(
                        attachment_public_id=ATT_UUID,
                        project_public_id=UUID(PROJ_UUID),
                        suggester=suggester,
                    )
                )
        predict_mock.assert_not_awaited()
    assert out["status"] == "ok"
    assert out["fields"]["vendor"]["value"]["public_id"] == VENDOR_UUID
    assert out["coding"] is not None
    assert out["coding"]["source"] == "vendor_project_history"


def test_vendor_unmatched_coding_null_status_ok():
    att = _attachment()
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name", return_value=None), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade(vendor_name="Unknown Co")),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "x",
        }
        out = asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    assert out["status"] == "ok"
    assert out["coding"] is None
    assert out["fields"]["vendor"]["value"] is None


def test_text_coding_mode_b_shape_matches_suggest_payload():
    att = _attachment()
    vendor = SimpleNamespace(id=10, public_id=VENDOR_UUID, name="Acme")
    weak = BillSuggestion(
        project_id=None,
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
    suggester.project_service.read_by_id.return_value = None
    suggester.sub_cost_code_service = MagicMock()
    suggester.sub_cost_code_service.read_by_id.return_value = SimpleNamespace(
        id=501,
        public_id="11111111-1111-1111-1111-000000000501",
        number="05.100",
        name="Framing",
    )
    suggester.sub_cost_code_service.is_holding_code.return_value = False

    text_suggestion = BillSuggestion(
        project_id=100,
        sub_cost_code_id=501,
        sub_cost_code_public_id="11111111-1111-1111-1111-000000000501",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="llm",
        reason="Suggested from the bill's document text",
        confidence=Decimal("0.88"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        model_version="u654b-text-v1",
        text_used=True,
        text_mode="B",
        text_model="claude-haiku-5-5",
    )

    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text_suggestion, None, None)),
    ) as predict_mock, patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ), patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", return_value=100
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "text",
        }
        from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

        rvm.return_value = VendorResolveMatch(
            public_id=VENDOR_UUID, name="Acme", confidence=0.7
        )
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="Proj"
        )
        with patch.object(ras, "VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            out = asyncio.run(
                ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    suggester=suggester,
                )
            )
            expected_keys = set(suggestion_to_api_payload(text_suggestion).keys())
    predict_mock.assert_awaited_once()
    assert set(out["coding"].keys()) == expected_keys
    assert out["coding"]["source"] == "llm"


def test_inaccessible_predicted_project_dropped():
    att = _attachment()
    vendor = SimpleNamespace(id=10, public_id=VENDOR_UUID, name="Acme")
    suggester = MagicMock()
    suggester.suggest.return_value = BillSuggestion(
        project_id=None,
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
    suggester.project_service = MagicMock()
    suggester.project_service.read_by_id.return_value = SimpleNamespace(id=999, public_id=PROJ_UUID)
    suggester.sub_cost_code_service = MagicMock()
    suggester.sub_cost_code_service.read_by_id.return_value = SimpleNamespace(
        id=501, public_id="x", number="05.100", name="Framing"
    )
    suggester.sub_cost_code_service.is_holding_code.return_value = False
    text_suggestion = BillSuggestion(
        project_id=None,
        sub_cost_code_id=501,
        sub_cost_code_public_id="x",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="llm",
        reason="Suggested from the bill's document text",
        confidence=Decimal("0.9"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        model_version="u654b-text-v1",
        text_used=True,
        text_mode="B",
        text_model="claude-haiku-5-5",
    )
    text_repo = MagicMock()
    text_repo.read_vendor_scc_counts.return_value = []
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text_suggestion, None, None)),
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ), patch.object(
        ras,
        "resolve_accessible_text_project_id",
        side_effect=lambda pid, **_: None if pid == 999 else pid,
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "t",
        }
        from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

        rvm.return_value = VendorResolveMatch(
            public_id=VENDOR_UUID, name="Acme", confidence=0.7
        )
        with patch.object(ras, "VendorService") as VS, patch(
            "entities.coding_suggestion.business.presenters.ProjectService"
        ) as PSapi:
            VS.return_value.read_by_public_id.return_value = vendor
            PSapi.return_value.read_by_id.return_value = None
            out = asyncio.run(
                ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    suggester=suggester,
                )
            )
    assert out["coding"] is not None
    assert out["coding"]["project_public_id"] is None


def test_response_always_has_seven_field_keys():
    shell = ras._result_shell("disabled")
    assert set(shell["fields"].keys()) == {
        "vendor",
        "bill_number",
        "bill_date",
        "total_amount",
        "description",
        "long_description",
        "reference",
    }


def test_no_bill_text_in_logs(caplog):
    caplog.set_level(logging.INFO)
    att = _attachment()
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name", return_value=None), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "SECRET INVOICE LINE ITEMS",
        }
        asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    for record in caplog.records:
        assert "SECRET INVOICE" not in record.getMessage()


def test_extraction_payload_is_current_stamp():
    payload = {"redaction_version": REDACTION_VERSION, "content": "x", "source_file_hash": "h"}
    assert extraction_payload_is_current(payload)


def test_link_state_db_error_fail_closed_not_found():
    att = _attachment()
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentRepository"
    ) as Repo:
        Repo.return_value.read_link_state_with_ownership.side_effect = RuntimeError("db down")
        AS.return_value.read_by_public_id.return_value = att
        with pytest.raises(AttachmentNotReadable):
            asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))


def test_processing_in_flight_waits_then_reuses():
    att = _attachment(extraction_status="processing")
    calls = {"n": 0}

    def extract_side_effect(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"status": "failed", "reason": "processing_in_flight"}
        return {"status": "completed", "masked_text": "ready text"}

    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ), patch.object(ras, "resolve_vendor_match_from_name", return_value=None), patch(
        "entities.bill.business.read_attachment_service.time.sleep"
    ):
        AS.return_value.read_by_public_id.return_value = att
        AS.return_value.read_by_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.side_effect = extract_side_effect
        out = asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    assert calls["n"] == 2
    assert out["status"] == "ok"
    assert out["fields"]["bill_number"]["value"] == "INV-1"


def test_header_needs_human_still_returns_fields():
    att = _attachment()
    low_conf = CascadeResult(
        accepted=False,
        needs_human=True,
        result={
            "vendor_name": "Acme",
            "bill_number": "N-1",
            "bill_date": "2026-02-01",
            "total_amount": "50.00",
            "description": "Parts",
            "confidence": 0.12,
        },
        confidence=0.12,
        winning_rung=Rung(provider="anthropic", model="claude-haiku-5-5"),
        attempts=[],
    )
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name", return_value=None), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=low_conf),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "x",
        }
        out = asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    assert out["fields"]["bill_number"]["value"] == "N-1"
    assert out["fields"]["bill_number"]["confidence"] == "0.1200"


def test_text_coding_mode_a_asserts_project_access():
    att = _attachment()
    vendor = SimpleNamespace(id=10, public_id=VENDOR_UUID, name="Acme")
    weak = BillSuggestion(
        project_id=100,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=Decimal("0.10"),
        top_count=None,
        second_count=None,
        total_count=None,
    )
    suggester = MagicMock()
    suggester.suggest.return_value = weak
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()
    suggester.sub_cost_code_service.read_by_id.return_value = SimpleNamespace(
        id=501,
        public_id="11111111-1111-1111-1111-000000000501",
        number="05.100",
        name="Framing",
    )
    suggester.sub_cost_code_service.is_holding_code.return_value = False
    text_suggestion = BillSuggestion(
        project_id=100,
        sub_cost_code_id=501,
        sub_cost_code_public_id="11111111-1111-1111-1111-000000000501",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="llm",
        reason="Suggested from the bill's document text",
        confidence=Decimal("0.9"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        model_version="u654b-text-v1",
        text_used=True,
        text_mode="A",
        text_model="claude-haiku-5-5",
    )
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text_suggestion, None, None)),
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ), patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi:
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "text",
        }
        from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

        rvm.return_value = VendorResolveMatch(
            public_id=VENDOR_UUID, name="Acme", confidence=0.7
        )
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="P"
        )
        with patch.object(ras, "VendorService") as VS, patch.object(ras, "ProjectService") as PS:
            VS.return_value.read_by_public_id.return_value = vendor
            PS.return_value.read_by_public_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="P"
            )
            with patch.object(ras, "assert_can_access_project") as access:
                asyncio.run(
                    ras.run_bill_read_attachment(
                        attachment_public_id=ATT_UUID,
                        project_public_id=UUID(PROJ_UUID),
                        suggester=suggester,
                    )
                )
                access.assert_called_once_with(100)


def test_mode_a_inaccessible_project_404():
    att = _attachment()
    vendor = SimpleNamespace(id=10, public_id=VENDOR_UUID, name="Acme")
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "t",
        }
        from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

        rvm.return_value = VendorResolveMatch(
            public_id=VENDOR_UUID, name="Acme", confidence=0.7
        )
        with patch.object(ras, "VendorService") as VS, patch.object(ras, "ProjectService") as PS:
            VS.return_value.read_by_public_id.return_value = vendor
            PS.return_value.read_by_public_id.return_value = SimpleNamespace(
                id=100, public_id=PROJ_UUID, name="P"
            )
            with patch.object(
                ras,
                "assert_can_access_project",
                side_effect=ras.EntityNotAccessibleError("Project", 100),
            ), patch(
                "entities.bill.business.read_attachment_service.run_bill_header_extraction",
                new=AsyncMock(),
            ) as header_mock:
                with pytest.raises(AttachmentNotReadable):
                    asyncio.run(
                        ras.run_bill_read_attachment(
                            attachment_public_id=ATT_UUID,
                            project_public_id=UUID(PROJ_UUID),
                        )
                    )
                header_mock.assert_not_awaited()


def test_coding_error_yields_partial_with_header():
    att = _attachment()
    vendor = SimpleNamespace(id=10, public_id=VENDOR_UUID, name="Acme")
    weak = BillSuggestion(
        project_id=None,
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
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
        ras, "predict_text_coding", side_effect=RuntimeError("boom")
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "t",
        }
        from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

        rvm.return_value = VendorResolveMatch(
            public_id=VENDOR_UUID, name="Acme", confidence=0.7
        )
        with patch.object(ras, "VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            out = asyncio.run(
                ras.run_bill_read_attachment(
                    attachment_public_id=ATT_UUID,
                    suggester=suggester,
                )
            )
    assert out["status"] == "partial"
    assert out["fields"]["bill_number"]["value"] == "INV-1"
    assert out["coding"] is None


def test_persist_extraction_stamps_u535g_redaction_version():
    svc = AttachmentExtractionService(
        repo=MagicMock(),
        storage=MagicMock(),
        di=MagicMock(),
    )
    att = _attachment()
    captured: dict = {}

    def capture_upload(**kwargs):
        captured.update(json.loads(kwargs["file_content"].decode("utf-8")))
        return "https://blob/out.json"

    svc.storage.upload_file.side_effect = capture_upload
    svc._persist_bill_drop_extraction_blob(
        att,
        "invoice text long enough for extraction here",
        [],
        1,
        "text_layer",
        aggressive=False,
    )
    assert captured.get("redaction_version") == REDACTION_VERSION


def test_vendor_list_cache_single_read_all_within_ttl():
    from entities.vendor.business import vendor_name_resolver as vnr

    vnr.configure_vendor_list_cache_for_tests()
    t = {"now": 1000.0}
    vnr.configure_vendor_list_cache_for_tests(now_fn=lambda: t["now"])
    with patch.object(vnr, "VendorService") as VS:
        VS.return_value.read_all.return_value = [
            SimpleNamespace(public_id=VENDOR_UUID, name="Acme"),
        ]
        vnr.resolve_vendor_match_from_name("Acme Supply", confidence=0.9)
        vnr.resolve_vendor_match_from_name("Acme Supply", confidence=0.9)
        assert VS.return_value.read_all.call_count == 1
        t["now"] += 61.0
        vnr.resolve_vendor_match_from_name("Acme Supply", confidence=0.9)
        assert VS.return_value.read_all.call_count == 2
    vnr.configure_vendor_list_cache_for_tests()


def test_vendor_resolver_exact_match_no_class_monkeypatch():
    from entities.vendor.business import vendor_name_resolver as vnr
    from entities.vendor.business.service import VendorService

    vnr.configure_vendor_list_cache_for_tests(
        vendor_list_loader=lambda: [
            SimpleNamespace(public_id=VENDOR_UUID, name="Acme Lumber Supply"),
        ]
    )
    orig_read_all = VendorService.read_all
    m = vnr.resolve_vendor_match_from_name("acme lumber supply", confidence=0.9)
    assert m is not None
    assert m.public_id == VENDOR_UUID
    assert VendorService.read_all is orig_read_all
    vnr.configure_vendor_list_cache_for_tests()


def test_vendor_resolver_fuzzy_match():
    from entities.vendor.business import vendor_name_resolver as vnr

    vnr.configure_vendor_list_cache_for_tests(
        vendor_list_loader=lambda: [
            SimpleNamespace(public_id=VENDOR_UUID, name="Acme Lumber Supply"),
        ]
    )
    m = vnr.resolve_vendor_match_from_name("Acme Lumber Supply Inc", confidence=0.9)
    assert m is not None
    assert m.public_id == VENDOR_UUID
    vnr.configure_vendor_list_cache_for_tests()


def test_vendor_resolver_no_match():
    from entities.vendor.business import vendor_name_resolver as vnr

    vnr.configure_vendor_list_cache_for_tests(
        vendor_list_loader=lambda: [
            SimpleNamespace(public_id=VENDOR_UUID, name="Totally Different Co"),
        ]
    )
    assert vnr.resolve_vendor_match_from_name("Unknown Vendor XYZ", confidence=0.9) is None
    vnr.configure_vendor_list_cache_for_tests()


def test_vendor_cache_hit_still_matches():
    from entities.vendor.business import vendor_name_resolver as vnr

    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return [SimpleNamespace(public_id=VENDOR_UUID, name="Acme Supply")]

    vnr.configure_vendor_list_cache_for_tests(vendor_list_loader=loader)
    m1 = vnr.resolve_vendor_match_from_name("Acme Supply", confidence=0.9)
    m2 = vnr.resolve_vendor_match_from_name("Acme Supply", confidence=0.9)
    assert calls["n"] == 1
    assert m1 is not None and m2 is not None
    assert m1.public_id == VENDOR_UUID
    vnr.configure_vendor_list_cache_for_tests()


def test_mode_a_coding_returns_request_project_public_id():
    catalog = _mode_b_catalog(proj_abbrev_to_id={})
    cascade = CascadeResult(
        accepted=True,
        needs_human=False,
        result={"scc_number": "05.100", "confidence": 0.9},
        confidence=0.9,
        winning_rung=Rung(provider="anthropic", model="claude-haiku-5-5"),
        attempts=[],
    )
    project_service = MagicMock()
    project_service.read_by_id.return_value = SimpleNamespace(
        id=100, public_id=PROJ_UUID, name="P"
    )
    sub_cost_code_service = MagicMock()
    sub_cost_code_service.read_by_id.return_value = SimpleNamespace(
        id=501,
        public_id="11111111-1111-1111-1111-000000000501",
        number="05.100",
        name="Framing",
    )
    sub_cost_code_service.is_holding_code.return_value = False
    with patch(
        "entities.coding_suggestion.business.bill_suggestion_service.assert_can_access_project"
    ):
        from entities.coding_suggestion.business.bill_suggestion_service import (
            bill_suggestion_from_text_cascade,
        )

        suggestion = bill_suggestion_from_text_cascade(
            cascade,
            catalog=catalog,
            mode="A",
            chosen_project_id=100,
            project_service=project_service,
            sub_cost_code_service=sub_cost_code_service,
        )
    assert suggestion is not None
    with patch("entities.coding_suggestion.business.presenters.ProjectService") as PS:
        PS.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="P"
        )
        payload = suggestion_to_api_payload(suggestion)
    assert payload["project_public_id"] == PROJ_UUID


def test_all_null_header_is_unreadable_not_partial():
    att = _attachment()
    empty_cascade = CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[],
    )
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=empty_cascade),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "some text",
        }
        out = asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    assert out["status"] == "unreadable"
    assert out["message"] == "Couldn't read this file"


def test_total_amount_two_decimal_string():
    att = _attachment()
    cascade = _ok_header_cascade(total_amount="1234.5")
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch.object(ras, "resolve_vendor_match_from_name", return_value=None), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=cascade),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "x",
        }
        out = asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    assert out["fields"]["total_amount"]["value"] == "1234.50"


def test_extract_pending_outcome_has_no_masked_text():
    att = _attachment(parse_invoice_number=False)
    repo = MagicMock()
    repo.update_extraction.return_value = att
    storage = MagicMock()
    storage.download_file.return_value = (b"%PDF-1.4\n" + b"x" * 300, {})
    storage.upload_file.return_value = "https://blob/out.json"
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=MagicMock())
    with patch(
        "entities.attachment.business.extraction_service._has_usable_text",
        return_value=True,
    ):
        out = svc._extract(att, force=False)
    assert "masked_text" not in out
    assert storage.download_file.call_count == 1
    assert storage.upload_file.call_count == 1


def test_masking_standard_blob_not_reused_for_taxpayer():
    svc = AttachmentExtractionService(repo=MagicMock(), storage=MagicMock())
    att = _attachment(extraction_status="completed", extracted_text_blob_url="https://b/x.json")
    att.file_hash = "abc"
    svc.storage.download_file.return_value = (
        json.dumps(
            {
                "content": "x" * 40,
                "redaction_version": REDACTION_VERSION,
                "source_file_hash": "abc",
                "masking": "standard",
            }
        ).encode(),
        {},
    )
    assert svc._bill_drop_reusable_payload(att, aggressive=True) is None
    assert svc._bill_drop_reusable_payload(att, aggressive=False) is not None


def test_stale_processing_lease_extracts_now():
    att = _attachment(
        extraction_status="processing",
        modified_datetime=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    svc = AttachmentExtractionService(
        repo=MagicMock(read_by_id=MagicMock(return_value=att)),
        storage=MagicMock(),
        di=MagicMock(),
    )
    with patch.object(svc, "_bill_drop_extract", return_value={"status": "completed"}) as ext:
        with patch.object(svc, "_prefill_outcome_with_text", side_effect=lambda _a, o, **_k: o):
            svc.extract_for_bill_drop_prefill(att, is_taxpayer_document=False)
    ext.assert_called_once()


def test_sync_fail_does_not_clobber_completed_row():
    svc = AttachmentExtractionService(repo=MagicMock(), storage=MagicMock())
    att = _attachment(extraction_status="processing")
    completed = _attachment(
        extraction_status="completed",
        extracted_text_blob_url="https://b/x.json",
    )
    completed.file_hash = "abc"
    svc.repo.read_by_id.return_value = completed
    svc.storage.download_file.return_value = (
        json.dumps(
            {
                "content": "x" * 40,
                "redaction_version": REDACTION_VERSION,
                "source_file_hash": "abc",
                "masking": "standard",
            }
        ).encode(),
        {},
    )
    with patch.object(svc, "_commit_terminal") as commit:
        out = svc._bill_drop_fail(att, "blob_download_error")
    commit.assert_not_called()
    assert out["status"] == "completed"


def test_blocking_work_runs_off_event_loop_thread():
    import threading

    att = _attachment()
    worker_threads: list[int] = []

    def track_link_state(aid):
        worker_threads.append(threading.get_ident())
        return AttachmentLinkState(False, False, True)

    main_ident = threading.get_ident()

    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(ras, "read_attachment_link_state", side_effect=track_link_state), patch.object(
        ras, "resolve_vendor_match_from_name", return_value=None
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "x",
        }
        asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))
    assert worker_threads
    assert all(t != main_ident for t in worker_threads)


def test_ownership_non_owner_404():
    att = _attachment()
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(True, False, True)
    ):
        AS.return_value.read_by_public_id.return_value = att
        with pytest.raises(AttachmentNotReadable):
            asyncio.run(ras.run_bill_read_attachment(attachment_public_id=ATT_UUID))


def test_contextvars_reach_link_state_call():
    from shared.authz.context import (
        current_company_id,
        current_is_system_admin,
        current_user_id,
    )

    att = _attachment()
    seen: dict = {}

    def capture_link_state(aid, **kwargs):
        seen["attachment_id"] = aid
        repo = kwargs.get("repo")
        if repo is not None and hasattr(repo, "read_link_state"):
            seen["repo"] = repo
        return AttachmentLinkState(False, False, True)

    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "read_attachment_link_state", side_effect=capture_link_state
    ), patch.object(ras, "AttachmentExtractionService") as ES, patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ), patch.object(ras, "resolve_vendor_match_from_name", return_value=None):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "x",
        }

        async def run_with_ctx():
            current_user_id.set(42)
            current_company_id.set(7)
            current_is_system_admin.set(False)
            await ras.run_bill_read_attachment(attachment_public_id=ATT_UUID)

        asyncio.run(run_with_ctx())
    assert seen.get("attachment_id") == att.id


def test_persist_extraction_records_masking_mode_when_explicit():
    svc = AttachmentExtractionService(
        repo=MagicMock(),
        storage=MagicMock(),
        di=MagicMock(),
    )
    att = _attachment()
    captured: dict = {}

    def capture_upload(**kwargs):
        captured.update(json.loads(kwargs["file_content"].decode("utf-8")))
        return "https://blob/out.json"

    svc.storage.upload_file.side_effect = capture_upload
    svc._persist_bill_drop_extraction_blob(
        att,
        "invoice text long enough for extraction here",
        [],
        1,
        "text_layer",
        aggressive=False,
    )
    assert captured.get("masking") == "standard"


PDF_TEXT_LAYER = "INVOICE 12345 Acme Supply total 100.00 " * 5


def test_processing_lease_fresh_accepts_sproc_datetime_string():
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    att = _attachment(extraction_status="processing", modified_datetime=stamp)
    svc = AttachmentExtractionService(repo=MagicMock(), storage=MagicMock())
    assert svc._processing_lease_fresh(att) is True


def test_legacy_blob_without_masking_key_reused_for_standard_sync():
    """Every current blob masks at least the standard shapes, so an invoice read may reuse
    a background-written blob that carries no masking key (R4)."""
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url="https://b/ex.json",
    )
    att.file_hash = "h"
    blob = json.dumps(
        {
            "content": PDF_TEXT_LAYER,
            "redaction_version": REDACTION_VERSION,
            "source_file_hash": "h",
        }
    ).encode()
    repo = MagicMock()
    repo.read_by_id.return_value = att
    storage = MagicMock()
    storage.download_file.side_effect = lambda u: (blob, {}) if "ex.json" in u else (b"%PDF-", {})
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=MagicMock())
    out = svc.extract_for_bill_drop_prefill(att, is_taxpayer_document=False)
    assert out.get("status") == "completed" and out.get("reused") is True
    assert out.get("masked_text") == PDF_TEXT_LAYER
    storage.upload_file.assert_not_called()


def test_legacy_blob_not_reused_for_aggressive_caller():
    """A blob without a masking key may have been masked only to the standard shapes, so a
    caller that needs aggressive masking must not reuse it."""
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url="https://b/ex.json",
    )
    att.file_hash = "h"
    blob = json.dumps(
        {
            "content": PDF_TEXT_LAYER,
            "redaction_version": REDACTION_VERSION,
            "source_file_hash": "h",
        }
    ).encode()
    svc = AttachmentExtractionService(repo=MagicMock(), storage=MagicMock())
    svc.storage.download_file.return_value = (blob, {})
    assert svc._bill_drop_reusable_payload(att, aggressive=True) is None
    assert svc._bill_drop_reusable_payload(att, aggressive=False) is not None


def test_bill_drop_extract_parses_vendor_invoice_number(monkeypatch):
    from entities.attachment.business import extraction_service as es_mod

    monkeypatch.setattr(es_mod, "_extract_text_layer", lambda c, t: (PDF_TEXT_LAYER, 1))
    att = _attachment()
    svc = AttachmentExtractionService(
        repo=MagicMock(update_extraction=MagicMock(return_value=att)),
        storage=MagicMock(),
        di=MagicMock(),
    )
    svc.storage.download_file.return_value = (b"%PDF-", {})
    with patch.object(
        svc, "_persist_bill_drop_extraction_blob", return_value=("n", "https://b/n.json", "t")
    ):
        svc._bill_drop_extract(att, aggressive=False)
    assert svc.repo.update_extraction.call_args.kwargs.get("vendor_invoice_number") == "12345"


def test_background_extract_pending_matches_master_io(monkeypatch):
    from entities.attachment.business import extraction_service as es_mod

    monkeypatch.setattr(es_mod, "_extract_text_layer", lambda c, t: (PDF_TEXT_LAYER, 1))
    monkeypatch.setattr(
        es_mod.config,
        "Settings",
        lambda: SimpleNamespace(attachment_extraction_groups="bill"),
    )
    att = _attachment(extraction_status="processing", claim_modified_datetime="x")
    repo = MagicMock()
    repo.claim_for_extraction.side_effect = [[att], []]
    repo.update_extraction.return_value = att
    storage = MagicMock()
    storage.download_file.return_value = (b"%PDF-", {"content_type": "application/pdf"})
    storage.upload_file.return_value = "https://b/new.json"
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=MagicMock())
    out = svc.extract_pending(limit=5)
    r = out["results"][0]
    assert r == {
        "status": "completed",
        "attachment_id": 1,
        "method": "text_layer",
        "pages_count": 1,
    }
    assert "masked_text" not in r
    assert storage.download_file.call_count == 1
    assert repo.read_by_id.call_count == 0


def test_background_claim_lost_outcome_unchanged(monkeypatch):
    from entities.attachment.business import extraction_service as es_mod

    monkeypatch.setattr(es_mod, "_extract_text_layer", lambda c, t: (PDF_TEXT_LAYER, 1))
    att = _attachment(extraction_status="processing", claim_modified_datetime="x")
    repo = MagicMock()
    repo.update_extraction.return_value = None
    storage = MagicMock()
    storage.download_file.return_value = (b"%PDF-", {"content_type": "application/pdf"})
    storage.upload_file.return_value = "https://b/new.json"
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=MagicMock())
    out = svc._extract(att)
    assert out == {"status": "skipped", "reason": "claim_lost", "attachment_id": 1}


def test_force_reextract_failure_reports_failed_not_completed():
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url="https://b/ex.json",
    )
    repo = MagicMock()
    repo.read_by_id.return_value = att
    storage = MagicMock()
    storage.download_file.side_effect = RuntimeError("blob gone")
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=MagicMock())
    out = svc._extract(att, force=True)
    assert out["status"] == "failed"


# --------------------------------------------------------------------------- #
# Round 3 (EM): only MASKED text may leave the bill-drop extraction path.
# --------------------------------------------------------------------------- #

_SSN = "123-45-6789"
_EIN = "12-3456789"
_TIN_INVOICE_TEXT = (
    "ACME SUPPLY INVOICE INV-1001 Date 2026-05-01 Total $1,234.56 "
    f"Remit to ACME SUPPLY. Owner SSN {_SSN}. Federal EIN {_EIN}. "
    "Thank you for your business, materials delivered to site."
)


def _fresh_drop_service(monkeypatch):
    from entities.attachment.business import extraction_service as es_mod

    monkeypatch.setattr(es_mod, "_extract_text_layer", lambda c, t: (_TIN_INVOICE_TEXT, 1))
    att = _attachment()
    repo = MagicMock()
    repo.read_by_id.return_value = att
    repo.update_extraction.return_value = att
    storage = MagicMock()
    storage.download_file.return_value = (b"%PDF-1.4 fresh", {})
    storage.upload_file.return_value = "https://blob/fresh.json"
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=MagicMock())
    return svc, att, storage


def test_fresh_bill_drop_extraction_returns_masked_text_only(monkeypatch):
    svc, att, storage = _fresh_drop_service(monkeypatch)
    out = svc.extract_for_bill_drop_prefill(att, is_taxpayer_document=False)
    assert out.get("status") == "completed"
    text = out.get("masked_text") or ""
    # Precondition: real text came through (not an empty / failed outcome).
    assert "ACME SUPPLY" in text and "1,234.56" in text
    assert _SSN not in text and _EIN not in text
    stored = json.loads(storage.upload_file.call_args.kwargs["file_content"])
    assert stored["content"] == text  # what the model gets is exactly what was stored


def test_read_attachment_sends_only_masked_text_to_the_model(monkeypatch):
    """End to end through run_bill_read_attachment with the REAL extraction service:
    the text handed to header extraction carries no SSN/EIN."""
    svc, att, _storage = _fresh_drop_service(monkeypatch)
    sent: list[str] = []

    async def fake_header(payload, **_kw):
        sent.append(payload["bill_text"])
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(ras, "run_bill_header_extraction", fake_header)
    monkeypatch.setattr(
        ras, "read_attachment_link_state", MagicMock(return_value=AttachmentLinkState(False, False, True))
    )
    att_svc = MagicMock()
    att_svc.read_by_public_id.return_value = att
    att_svc.read_by_id.return_value = att
    asyncio.run(
        ras.run_bill_read_attachment(
            attachment_public_id=str(att.public_id),
            extraction_service=svc,
            attachment_service=att_svc,
        )
    )
    assert len(sent) == 1
    assert "ACME SUPPLY" in sent[0]
    assert _SSN not in sent[0] and _EIN not in sent[0]
