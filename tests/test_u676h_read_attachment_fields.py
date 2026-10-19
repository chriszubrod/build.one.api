"""U-676h: read-attachment maps short/long/reference header fields."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from intelligence.cascade.core import CascadeResult, Rung

from entities.bill.business import read_attachment_service as ras


def _cascade(result: dict) -> CascadeResult:
    return CascadeResult(
        accepted=True,
        needs_human=False,
        result=result,
        confidence=0.88,
        winning_rung=Rung(provider="anthropic", model="claude-haiku-5-5"),
        attempts=[],
    )


def test_header_maps_short_long_reference():
    with patch.object(ras, "resolve_vendor_match_from_name", return_value=None):
        fields, ok, _model = ras._header_from_cascade(
            _cascade(
                {
                    "vendor_name": None,
                    "bill_number": None,
                    "bill_date": None,
                    "total_amount": None,
                    "short_description": "biweekly mowing",
                    "long_description": "Biweekly mowing, Oct 1-15, 5224 Williamsburg",
                    "reference": "Job 2026-044",
                    "confidence": 0.88,
                }
            )
        )
    assert ok
    assert fields["description"]["value"] == "Biweekly Mowing"
    assert fields["description"]["confidence"] == "0.8800"
    assert "Oct 1-15" in fields["long_description"]["value"]
    assert fields["reference"]["value"] == "Job 2026-044"


def test_long_description_fallback_formats_short_line():
    with patch.object(ras, "resolve_vendor_match_from_name", return_value=None):
        fields, ok, _ = ras._header_from_cascade(
            _cascade(
                {
                    "vendor_name": "Acme",
                    "bill_number": "1",
                    "bill_date": "2026-01-01",
                    "total_amount": "10",
                    "short_description": None,
                    "long_description": (
                        "Biweekly mowing service, Oct 1-15, 5224 Williamsburg"
                    ),
                    "reference": None,
                    "confidence": 0.9,
                }
            )
        )
    assert ok
    assert fields["description"]["value"] == "Biweekly Mowing Service, Oct 1-15"
    assert len(fields["description"]["value"] or "") <= 60


def test_legacy_description_only_formats_short_from_long():
    with patch.object(ras, "resolve_vendor_match_from_name", return_value=None):
        fields, ok, _ = ras._header_from_cascade(
            _cascade(
                {
                    "vendor_name": "Acme",
                    "bill_number": None,
                    "bill_date": None,
                    "total_amount": None,
                    "description": "plywood purchase white oak",
                    "confidence": 0.5,
                }
            )
        )
    assert ok
    assert fields["description"]["value"] == "Plywood Purchase White Oak"
    assert fields["long_description"]["value"] == "plywood purchase white oak"


def test_run_returns_seven_field_keys():
    from types import SimpleNamespace

    att = SimpleNamespace(
        id=1,
        public_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )
    cascade = _cascade(
        {
            "vendor_name": "V",
            "bill_number": "1",
            "bill_date": "2026-01-01",
            "total_amount": "1.00",
            "short_description": "parts",
            "long_description": "parts delivery",
            "reference": None,
            "confidence": 0.9,
        }
    )
    with patch.object(ras, "AttachmentService") as AS, patch.object(
        ras, "AttachmentExtractionService"
    ) as ES, patch.object(
        ras, "read_attachment_link_state", return_value=(False, False)
    ), patch.object(ras, "resolve_vendor_match_from_name", return_value=None), patch(
        "entities.bill.business.read_attachment_service.run_bill_header_extraction",
        new=AsyncMock(return_value=cascade),
    ):
        AS.return_value.read_by_public_id.return_value = att
        ES.return_value.extract_for_bill_drop_prefill.return_value = {
            "status": "completed",
            "masked_text": "x",
        }
        out = asyncio.run(
            ras.run_bill_read_attachment(
                attachment_public_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )
        )
    assert set(out["fields"].keys()) == {
        "vendor",
        "bill_number",
        "bill_date",
        "total_amount",
        "description",
        "long_description",
        "reference",
    }
