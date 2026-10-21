"""U-702: u676a budget skip classification + batch abort."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from entities.bill.business.extraction_mapper import BillExtractionMapper
from intelligence.cascade.core import Attempt, CascadeResult, Rung
from scripts import u654a_text_backtest as u654a
from scripts import u676a_bill_field_bakeoff as u676a


def _row(bill_id: int) -> u676a.HeaderBillRow:
    return u676a.HeaderBillRow(
        bill_id=bill_id,
        vendor_id=10,
        vendor_public_id="11111111-1111-1111-1111-111111111111",
        bill_number="INV-100",
        bill_date=date(2026, 5, 1),
        total_amount=Decimal("100.00"),
        created=datetime(2026, 5, 2, 10, 0, 0),
        intake_source="manual",
        extracted_text_blob_url="https://example/blob",
        first_line_description="Framing labor",
    )


def test_arm_prediction_error_kind_budget_skip():
    cascade = CascadeResult(
        accepted=False,
        needs_human=True,
        result=None,
        confidence=None,
        winning_rung=None,
        attempts=[
            Attempt(
                rung=Rung("anthropic", "claude-haiku-5-5"),
                accepted=False,
                error="claude_unavailable:no_key",
            )
        ],
    )
    pred = u676a.arm_prediction_from_cascade(cascade, BillExtractionMapper())
    assert pred.error_kind == "budget_skip:no_key"


@pytest.mark.parametrize(
    "token",
    ["daily_cap", "credit", "no_key", "disabled"],
)
def test_llm_arm_batch_stops_after_budget_skip(token: str):
    work = [(_row(i), {"bill_text": "x"}) for i in (1, 2, 3)]
    call_n = 0

    async def fake_header(_payload, transport_for=None, ladder=None):
        nonlocal call_n
        call_n += 1
        if call_n == 2:
            return CascadeResult(
                accepted=False,
                needs_human=True,
                result=None,
                confidence=None,
                winning_rung=None,
                attempts=[
                    Attempt(
                        rung=Rung("anthropic", "claude-haiku-5-5"),
                        accepted=False,
                        error=f"claude_unavailable:{token}",
                    )
                ],
            )
        return CascadeResult(
            accepted=True,
            needs_human=False,
            result={
                "vendor_name": "Acme",
                "bill_number": "1",
                "bill_date": "2026-05-01",
                "total_amount": "100",
                "long_description": "x",
            },
            confidence=0.9,
            winning_rung=Rung("foundry", "DeepSeek-V4-Flash"),
            attempts=[],
        )

    cost_state = {"usd": 0.0, "stopped": False}
    out: dict[int, u676a.ArmPrediction] = {}
    ladder = u676a.BILL_CODING_FOUNDRY_LADDER
    with patch.object(u676a, "run_bill_header_extraction", side_effect=fake_header):
        with patch.object(u654a, "estimate_ladder_first_rung_cost_usd", return_value=0.01):
            asyncio.run(
                u676a.run_llm_arm_batch(
                    work,
                    ladder=ladder,
                    transport_for=lambda _p: None,
                    concurrency=2,
                    max_cost_usd=100.0,
                    cost_state=cost_state,
                    mapper=BillExtractionMapper(),
                    out=out,
                )
            )
    assert cost_state.get("budget_abort") is True
    assert cost_state.get("budget_skip_token") == token
    assert 2 not in out
    assert 3 not in out
    assert len(out) == 1
