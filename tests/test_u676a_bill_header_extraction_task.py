"""Offline tests for U-676a bill header StructuredTask."""
from __future__ import annotations

import json

import pytest

from intelligence.cascade import bill_header_extraction as mod
from intelligence.cascade.bill_header_extraction import (
    ALLOWED_PAYLOAD_KEYS,
    BILL_HEADER_EXTRACTION_TASK,
    BILL_HEADER_TEXT_CHAR_BUDGET,
    validate_bill_header_result,
)


def test_payload_user_message_only_allowed_keys():
    payload = {"bill_text": "Invoice 123 total $50"}
    assert set(payload.keys()) == ALLOWED_PAYLOAD_KEYS
    msg = BILL_HEADER_EXTRACTION_TASK.build_user_message(payload)
    parsed = json.loads(msg)
    assert set(parsed.keys()) == {"bill_text"}
    assert len(parsed["bill_text"]) <= BILL_HEADER_TEXT_CHAR_BUDGET + 20
    with pytest.raises(ValueError, match="unexpected payload keys"):
        BILL_HEADER_EXTRACTION_TASK.build_user_message({**payload, "vendor_name": "x"})


def test_validator_accepts_valid():
    ok, reason = validate_bill_header_result(
        {
            "vendor_name": "Acme",
            "bill_number": "INV-1",
            "bill_date": "2026-05-01",
            "total_amount": "100.50",
            "description": "Lumber",
            "confidence": 0.85,
        }
    )
    assert ok and reason == "ok"


def test_validator_rejects_bad_date():
    ok, reason = validate_bill_header_result(
        {
            "vendor_name": None,
            "bill_number": None,
            "bill_date": "not-a-date",
            "total_amount": None,
            "description": None,
            "confidence": 0.5,
        }
    )
    assert not ok and "bill_date" in reason


def test_validator_rejects_negative_amount():
    ok, _ = validate_bill_header_result(
        {
            "vendor_name": None,
            "bill_number": None,
            "bill_date": None,
            "total_amount": "-1",
            "description": None,
            "confidence": 0.5,
        }
    )
    assert not ok


def test_validator_rejects_non_numeric_amount():
    ok, _ = validate_bill_header_result(
        {
            "vendor_name": None,
            "bill_number": None,
            "bill_date": None,
            "total_amount": "abc",
            "description": None,
            "confidence": 0.5,
        }
    )
    assert not ok


def test_validator_truncates_overlong_legacy_description():
    data = {
        "vendor_name": None,
        "bill_number": None,
        "bill_date": None,
        "total_amount": None,
        "description": "x" * 250,
        "confidence": 0.5,
    }
    ok, reason = validate_bill_header_result(data)
    assert ok, reason
    assert len(data["description"]) <= 200


def test_validator_rejects_confidence_out_of_range():
    ok, _ = validate_bill_header_result(
        {
            "vendor_name": None,
            "bill_number": None,
            "bill_date": None,
            "total_amount": None,
            "description": None,
            "confidence": 1.5,
        }
    )
    assert not ok


def test_task_validator_is_validate_bill_header_result():
    ok, reason = BILL_HEADER_EXTRACTION_TASK.validate(
        {
            "vendor_name": "Acme",
            "bill_number": "1",
            "bill_date": "2026-01-01",
            "total_amount": "10",
            "description": "x",
            "confidence": 0.9,
        }
    )
    assert ok and reason == "ok"


def test_run_bill_header_extraction_honors_ladder_override():
    import asyncio
    from unittest.mock import patch

    from intelligence.cascade.bill_coding import CLAUDE_HAIKU_LADDER
    from intelligence.cascade.bill_header_extraction import run_bill_header_extraction
    from intelligence.cascade.core import CascadeResult

    captured: dict[str, object] = {}

    async def fake_run_cascade(task, _payload, transport_for=None):
        captured["ladder"] = task.ladder
        return CascadeResult(
            accepted=True,
            needs_human=False,
            result={
                "vendor_name": "Acme",
                "bill_number": "1",
                "bill_date": "2026-01-01",
                "total_amount": "10",
                "description": "x",
                "confidence": 0.9,
            },
            confidence=0.9,
            winning_rung=task.ladder[0],
            attempts=[],
        )

    with patch(
        "intelligence.cascade.bill_header_extraction.run_cascade",
        side_effect=fake_run_cascade,
    ):
        asyncio.run(run_bill_header_extraction({"bill_text": "inv"}, ladder=CLAUDE_HAIKU_LADDER))
    assert captured["ladder"] == CLAUDE_HAIKU_LADDER
