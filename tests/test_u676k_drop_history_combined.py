"""U-676k: bill drop read-attachment history+text combined rule (true-project history)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from entities.bill.business import read_attachment_service as ras
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from intelligence.cascade.core import CascadeResult, Rung

ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROJ_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
PROJ_Y_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
VENDOR_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _ok_header_cascade():
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result={
            "vendor_name": "Acme Supply",
            "bill_number": "INV-1",
            "bill_date": "2026-05-01",
            "total_amount": "100.00",
            "description": "Materials",
            "confidence": 0.9,
        },
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


def _strong_history(**kw):
    base = dict(
        project_id=200,
        sub_cost_code_id=501,
        sub_cost_code_public_id="11111111-1111-1111-1111-000000000501",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="vendor_history",
        reason="history",
        confidence=Decimal("0.85"),
        raw_score=Decimal("0.85"),
        top_count=5,
        second_count=1,
        total_count=6,
    )
    base.update(kw)
    return BillSuggestion(**base)


def _weak_history(**kw):
    base = dict(
        project_id=None,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="none",
        reason=None,
        confidence=None,
        raw_score=Decimal("0.40"),
        top_count=None,
        second_count=None,
        total_count=None,
    )
    base.update(kw)
    return BillSuggestion(**base)


def _text_llm(*, project_id=100, scc_id=502):
    return BillSuggestion(
        project_id=project_id,
        sub_cost_code_id=scc_id,
        sub_cost_code_public_id="22222222-2222-2222-2222-000000000502",
        sub_cost_code_number="06.200",
        sub_cost_code_name="From LLM",
        source="llm",
        reason="Suggested from the bill's document text",
        confidence=Decimal("0.88"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        text_used=True,
        text_mode="B",
    )


@pytest.fixture(autouse=True)
def _enable_flag():
    with patch.object(ras.config, "Settings") as mock_settings:
        s = MagicMock()
        s.bill_drop_prefill_enabled = True
        s.bill_drop_prefill_timeout_seconds = 25
        s.bill_coding_text_daily_call_cap = 100
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


@pytest.fixture(autouse=True)
def _pipeline_mocks():
    att = _attachment()
    vendor = SimpleNamespace(id=10, public_id=VENDOR_UUID, name="Acme")
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=(False, False)
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ), patch.object(ras, "resolve_vendor_match_from_name") as rvm:
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "invoice body",
        }
        from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

        rvm.return_value = VendorResolveMatch(
            public_id=VENDOR_UUID,
            name="Acme",
            confidence=0.8,
            vendor_id=10,
        )
        with patch.object(ras, "VendorService") as VS:
            VS.return_value.read_by_public_id.return_value = vendor
            yield


def test_strong_history_on_model_project_y_uses_history_scc():
    """History keyed on model project Y, not vendor-dominant X."""
    history_y = _strong_history(
        project_id=100,
        sub_cost_code_number="07.300",
        sub_cost_code_name="On Y",
        source="vendor_project_history",
    )
    history_x = _strong_history(project_id=200)
    text = _text_llm(project_id=100)
    suggester = MagicMock()
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    def _suggest(*, vendor_id, project_id, **kwargs):
        if project_id == 100:
            return history_y
        if project_id == 200:
            return history_x
        return _weak_history()

    suggester.suggest.side_effect = _suggest

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text, None, None)),
    ) as predict_mock, patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="Proj"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    predict_mock.assert_awaited_once()
    assert suggester.suggest.call_count == 1
    assert suggester.suggest.call_args.kwargs["project_id"] == 100
    assert out["coding"]["source"] == "vendor_project_history"
    assert out["coding"]["sub_cost_code_number"] == "07.300"
    assert out["coding"]["project_public_id"] == PROJ_UUID


def test_strong_on_dominant_x_weak_on_model_y_uses_model_scc():
    history_x = _strong_history(project_id=200)
    history_y = _weak_history(project_id=100)
    text = _text_llm(project_id=100)
    suggester = MagicMock()
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    def _suggest(*, vendor_id, project_id, **kwargs):
        if project_id == 100:
            return history_y
        if project_id == 200:
            return history_x
        return _weak_history()

    suggester.suggest.side_effect = _suggest

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text, None, None)),
    ), patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="Proj"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    assert out["coding"]["source"] == "llm"
    assert out["coding"]["sub_cost_code_number"] == "06.200"
    assert out["coding"]["project_public_id"] == PROJ_UUID


def test_strong_history_no_project_cost_from_history_on_true_project():
    history = _strong_history(project_id=100)
    text = _text_llm(project_id=100)
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text, None, None)),
    ) as predict_mock, patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="Proj"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    predict_mock.assert_awaited_once()
    assert suggester.suggest.call_args.kwargs["project_id"] == 100
    assert out["coding"]["source"] == "vendor_history"
    assert out["coding"]["sub_cost_code_number"] == "05.100"
    assert out["coding"]["sub_cost_code_number"] != "06.200"
    assert out["coding"]["project_public_id"] == PROJ_UUID


def test_model_no_project_strong_no_project_history_used():
    history_nop = _strong_history(project_id=200)
    text = _text_llm(project_id=None, scc_id=502)
    suggester = MagicMock()
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    def _suggest(*, vendor_id, project_id, **kwargs):
        if project_id is None:
            return history_nop
        return _weak_history()

    suggester.suggest.side_effect = _suggest

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text, None, None)),
    ), patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=200, public_id=PROJ_Y_UUID, name="Hist"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    assert out["coding"]["source"] == "vendor_history"
    assert out["coding"]["project_public_id"] == PROJ_Y_UUID


def test_model_no_project_weak_no_project_history_uses_model_scc_no_project():
    text = _text_llm(project_id=None, scc_id=502)
    suggester = MagicMock()
    suggester.suggest.return_value = _weak_history()
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text, None, None)),
    ), patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="Proj"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    assert out["coding"]["source"] == "llm"
    assert out["coding"]["sub_cost_code_number"] == "06.200"
    assert out["coding"]["project_public_id"] is None


def test_coding_daily_cap_keeps_headers_and_strong_no_project_history():
    history_nop = _strong_history(project_id=200)
    suggester = MagicMock()
    suggester.suggest.return_value = history_nop
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(None, "daily_cap", None)),
    ), patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=200, public_id=PROJ_Y_UUID, name="Hist"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    assert out["status"] == "partial"
    assert out["fields"]["bill_number"]["value"] == "INV-1"
    assert out["coding"]["source"] == "vendor_history"
    assert out["message"] is None


def test_coding_exception_keeps_headers_and_strong_no_project_history():
    history_nop = _strong_history(project_id=200)
    suggester = MagicMock()
    suggester.suggest.return_value = history_nop
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ), patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=200, public_id=PROJ_Y_UUID, name="Hist"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    assert out["status"] == "partial"
    assert out["fields"]["vendor"]["extracted_name"] == "Acme Supply"
    assert out["coding"]["sub_cost_code_number"] == "05.100"


def test_weak_history_model_decides_cost_code():
    text = _text_llm()
    suggester = MagicMock()
    suggester.suggest.return_value = _weak_history()
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text, None, None)),
    ) as predict_mock, patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="Proj"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    predict_mock.assert_awaited_once()
    assert out["coding"]["source"] == "llm"
    assert out["coding"]["sub_cost_code_number"] == "06.200"


def test_chosen_project_strong_history_skips_model():
    history = _strong_history(source="vendor_project_history")
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    with patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(),
    ) as predict_mock, patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(ras, "ProjectService") as PS, patch.object(
        ras, "assert_can_access_project"
    ), patch.object(
        ras, "resolve_accessible_text_project_id", return_value=100
    ):
        PS.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="P"
        )
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
    assert out["coding"]["source"] == "vendor_project_history"


def test_chosen_project_coding_daily_cap_keeps_header_fields_partial():
    """With a chosen project and weak history, a daily-capped coding call keeps the header
    fields and returns status "partial" with no coding (header already read; the web keeps
    the fields). Deliberate since U-676k fix round 1 (was ai_unavailable with empty fields)."""
    history = _weak_history(source="vendor_project_history")
    suggester = MagicMock()
    suggester.suggest.return_value = history
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()
    with patch.object(
        ras, "predict_text_coding", new=AsyncMock(return_value=(None, "daily_cap", None))
    ) as predict_mock, patch.object(ras, "ProjectService") as PS, patch.object(
        ras, "assert_can_access_project"
    ):
        PS.return_value.read_by_public_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="P"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                project_public_id=UUID(PROJ_UUID),
                suggester=suggester,
            )
        )
    predict_mock.assert_awaited_once()
    assert out["status"] == "partial"
    assert out["coding"] is None
    assert out["fields"]["bill_number"]["value"]  # header kept
