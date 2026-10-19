"""U-676k fix r1: text-tier project survives unresolved / holding SubCostCode."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.business import read_attachment_service as ras
from entities.coding_suggestion.business.bill_suggestion_service import BillSuggestion
from intelligence.cascade.core import CascadeResult, Rung

ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
PROJ_UUID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
VENDOR_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"




def _ok_header_cascade():
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result={
            "vendor_name": "Acme",
            "bill_number": "1",
            "confidence": 0.9,
        },
        confidence=0.9,
        winning_rung=Rung(provider="anthropic", model="claude-haiku-5-5"),
        attempts=[],
    )


@pytest.fixture(autouse=True)
def _settings():
    with patch.object(ras.config, "Settings") as ms:
        s = MagicMock()
        s.bill_drop_prefill_enabled = True
        s.bill_drop_prefill_timeout_seconds = 25
        s.bill_coding_text_daily_call_cap = 100
        ms.return_value = s
        yield


@pytest.fixture(autouse=True)
def _claude_ok():
    with patch.object(
        ras.claude_budget,
        "is_claude_day_stopped_async",
        new=AsyncMock(return_value=False),
    ):
        yield


def test_project_only_text_prediction_drives_history_on_p():
    """Model returns project P but no SCC; history on P still wins when strong."""
    from entities.vendor.business.vendor_name_resolver import VendorResolveMatch

    text_project_only = BillSuggestion(
        project_id=100,
        sub_cost_code_id=None,
        sub_cost_code_public_id=None,
        sub_cost_code_number=None,
        sub_cost_code_name=None,
        source="llm",
        reason="Suggested from the bill's document text",
        confidence=Decimal("0.9"),
        raw_score=None,
        top_count=None,
        second_count=None,
        total_count=None,
        text_used=True,
        text_mode="B",
    )
    history_on_p = BillSuggestion(
        project_id=100,
        sub_cost_code_id=501,
        sub_cost_code_public_id="11111111-1111-1111-1111-000000000501",
        sub_cost_code_number="05.100",
        sub_cost_code_name="Framing",
        source="vendor_project_history",
        reason="history",
        confidence=Decimal("0.85"),
        raw_score=Decimal("0.85"),
        top_count=5,
        second_count=1,
        total_count=6,
    )
    suggester = MagicMock()
    suggester.suggest.return_value = history_on_p
    suggester.project_service = MagicMock()
    suggester.sub_cost_code_service = MagicMock()

    att = SimpleNamespace(
        id=1,
        public_id=ATT_UUID,
        blob_url="x",
        file_hash="h",
        content_type="application/pdf",
        extraction_status="pending",
        extracted_text_blob_url=None,
        parse_invoice_number=False,
        is_taxpayer_document=False,
        claim_modified_datetime=None,
    )

    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=AttachmentLinkState(False, False, True)
    ), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=_ok_header_cascade()),
    ), patch.object(ras, "resolve_vendor_match_from_name") as rvm, patch.object(
        ras,
        "predict_text_coding",
        new=AsyncMock(return_value=(text_project_only, None, None)),
    ), patch(
        "entities.coding_suggestion.business.presenters.ProjectService"
    ) as PSapi, patch.object(
        ras, "resolve_accessible_text_project_id", side_effect=lambda pid, **_: pid
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "body",
        }
        rvm.return_value = VendorResolveMatch(
            public_id=VENDOR_UUID, name="Acme", confidence=0.8, vendor_id=10
        )
        PSapi.return_value.read_by_id.return_value = SimpleNamespace(
            id=100, public_id=PROJ_UUID, name="P"
        )
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id=ATT_UUID,
                suggester=suggester,
            )
        )
    assert suggester.suggest.call_args.kwargs["project_id"] == 100
    assert out["coding"]["source"] == "vendor_project_history"
    assert out["coding"]["project_public_id"] == PROJ_UUID
