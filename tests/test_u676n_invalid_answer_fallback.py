"""U-676n: a parsed-but-invalid Claude answer lets the Foundry fallback run; no rationale field."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from intelligence.cascade import bill_coding, claude_budget
from intelligence.cascade.core import Rung, run_ladder
from intelligence.transport.base import Usage


HAIKU = Rung("anthropic", "claude-haiku-5-5")
DEEP = Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True)
NANO = Rung("foundry", "gpt-5.4-nano", fallback_only=True)

PAYLOAD = {
    "candidate_sccs": [{"number": "43.10", "name": "Framing"}],
    "candidate_projects": None,
}


class _SpendRepo:
    def read_daily_spend(self, spend_date, provider):
        return Decimal("0")

    def add_daily_spend(self, spend_date, provider, cost_usd):
        return cost_usd


@pytest.fixture(autouse=True)
def _reset_budget(monkeypatch):
    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "6.45")
    claude_budget.configure_for_tests(
        now_fn=lambda: datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc),
        repo_reader=_SpendRepo(),
        repo_writer=_SpendRepo(),
    )
    yield
    claude_budget.configure_for_tests()


def _run(rungs, outcomes, *, threshold=0.5):
    calls: list[Rung] = []

    async def execute(rung):
        calls.append(rung)
        return outcomes[rung]

    def validate(parsed):
        return bill_coding.validate_bill_coding_result(parsed, payload=PAYLOAD)

    result = asyncio.run(run_ladder(rungs, execute, validate, threshold, "test"))
    return result, calls


def test_invalid_claude_answer_runs_foundry_fallback():
    invalid = ({"scc_number": "99.99", "confidence": 0.5}, 0.5, Usage(), None)
    good = ({"scc_number": "43.10", "confidence": 0.9}, 0.9, Usage(), None)
    result, calls = _run((HAIKU, DEEP, NANO), {HAIKU: invalid, DEEP: good})
    assert calls == [HAIKU, DEEP]
    assert result.accepted
    assert result.winning_rung == DEEP


def test_invalid_answers_walk_every_fallback_rung():
    invalid = ({"scc_number": "99.99", "confidence": 0.9}, 0.9, Usage(), None)
    result, calls = _run((HAIKU, DEEP, NANO), {HAIKU: invalid, DEEP: invalid, NANO: invalid})
    assert calls == [HAIKU, DEEP, NANO]
    assert result.needs_human


def test_valid_claude_answer_below_bar_still_skips_fallback():
    low = ({"scc_number": "43.10", "confidence": 0.3}, 0.3, Usage(), None)
    result, calls = _run((HAIKU, DEEP), {HAIKU: low})
    assert calls == [HAIKU]
    assert result.needs_human
    assert result.attempts[1].error == "skipped:fallback_only"


def test_valid_fallback_answer_stops_later_fallback_rungs():
    invalid = ({"scc_number": "99.99", "confidence": 0.9}, 0.9, Usage(), None)
    low = ({"scc_number": "43.10", "confidence": 0.2}, 0.2, Usage(), None)
    result, calls = _run((HAIKU, DEEP, NANO), {HAIKU: invalid, DEEP: low})
    assert calls == [HAIKU, DEEP]
    assert result.attempts[2].error == "skipped:fallback_only"


def test_long_rationale_no_longer_rejects():
    ok, reason = bill_coding.validate_bill_coding_result(
        {"scc_number": "43.10", "confidence": 0.9, "rationale": "x" * 300},
        payload=PAYLOAD,
    )
    assert ok, reason


def test_prompt_no_longer_asks_for_rationale():
    assert "rationale" not in bill_coding._SYSTEM_PROMPT.lower()
