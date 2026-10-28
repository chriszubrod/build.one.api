"""U-701b4 (API): list-drop consumes the ladder's prefill (low confidence, similar_bills)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from entities.attachment.persistence.repo import AttachmentLinkState
from entities.bill.business import draft_from_attachment_service as dfs
from entities.bill.business import read_attachment_service as ras

ATT_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VENDOR_UUID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
PROJ_UUID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
SCC_UUID = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"


def _read(coding):
    return {
        "status": "ok",
        "message": None,
        "fields": {
            "vendor": {"value": {"public_id": VENDOR_UUID, "name": "Acme"}, "extracted_name": "Acme", "confidence": "0.9"},
            "bill_number": {"value": "INV-9", "confidence": "0.9"},
            "bill_date": {"value": "2026-05-01", "confidence": "0.9"},
            "total_amount": {"value": "250.00", "confidence": "0.9"},
            "description": {"value": "Materials", "confidence": "0.9"},
            "long_description": {"value": None, "confidence": None},
            "reference": {"value": None, "confidence": None},
        },
        "coding": coding,
        "model": "claude-haiku-5-5",
    }


def _coding(**kw):
    base = {
        "project_public_id": PROJ_UUID,
        "sub_cost_code_public_id": SCC_UUID,
        "sub_cost_code_number": "05.100",
        "source": "llm",
        "confidence": "0.55",
        "decision_source": "primary",
    }
    base.update(kw)
    return base


def _create_kwargs(coding, *, scc_id=501):
    att_svc = MagicMock()
    att_svc.read_by_public_id.return_value = SimpleNamespace(id=99, public_id=ATT_UUID, file_hash="h")
    blia_svc = MagicMock()
    blia_svc.read_draft_bill_public_id_by_attachment_hash.return_value = None
    bill_svc = MagicMock()
    bill_svc.create.return_value = SimpleNamespace(public_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    state = AttachmentLinkState(is_linked=False, is_taxpayer=False, is_owned=True)
    scc_svc = MagicMock()
    scc_svc.read_by_public_id.return_value = (
        SimpleNamespace(id=scc_id) if scc_id is not None else None
    )
    with patch.object(ras, "read_attachment_link_state", return_value=state), patch.object(
        dfs, "read_attachment_link_state", return_value=state
    ), patch.object(dfs, "SubCostCodeService", return_value=scc_svc), patch.object(
        dfs, "_resolve_default_payment_term_public_id", new=AsyncMock(return_value=None)
    ), patch.object(
        dfs, "_resolve_entered_by_for_memo", new=AsyncMock(return_value="Chris Z")
    ):
        asyncio.run(
            dfs.draft_bill_from_attachment(
                attachment_public_id=ATT_UUID,
                user_id=17,
                attachment_service=att_svc,
                blia_service=blia_svc,
                bill_service=bill_svc,
                read_fn=AsyncMock(return_value=_read(coding)),
            )
        )
    return bill_svc.create.call_args.kwargs


def test_below_070_answer_still_creates_a_prefilled_line():
    kw = _create_kwargs(_coding(confidence="0.55"))
    assert kw["line_sub_cost_code_id"] == 501
    assert kw["line_coding_prefill_state"] == "prefilled"
    assert kw["line_project_public_id"] == PROJ_UUID


@pytest.mark.parametrize("source", ["similar_bills", "llm", "vendor_history"])
def test_every_source_prefills_the_same_way(source):
    kw = _create_kwargs(_coding(source=source))
    assert kw["line_sub_cost_code_id"] == 501
    assert kw["line_coding_prefill_state"] == "prefilled"


def test_project_only_together_with_a_resolved_scc():
    kw = _create_kwargs(_coding(), scc_id=None)
    assert "line_sub_cost_code_id" not in kw
    assert "line_project_public_id" not in kw
    assert "line_coding_prefill_state" not in kw
