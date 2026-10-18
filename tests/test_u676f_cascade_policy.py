"""U-676f: fallback-only rungs, daily-cap stop reason in run_ladder."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from intelligence.cascade import claude_budget
from intelligence.cascade.core import Attempt, Rung, run_ladder
from intelligence.cascade.ladder_config import (
    DEFAULT_BILL_CODING_SUGGESTION_LADDER,
    resolve_ladder,
    resolve_threshold,
)
from intelligence.transport.base import Usage


HAIKU = Rung("anthropic", "claude-haiku-5-5")
SONNET = Rung("anthropic", "claude-sonnet-5-5")
DEEP = Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True)
NANO = Rung("foundry", "gpt-5.4-nano", fallback_only=True)
DEEP_PLAIN = Rung("foundry", "DeepSeek-V4-Flash")


class FakeSpendRepo:
    def __init__(self, spend: Decimal = Decimal("0")) -> None:
        self.spend = spend
        self.adds: list[Decimal] = []

    def read_daily_spend(self, spend_date, provider: str) -> Decimal:
        return self.spend

    def add_daily_spend(self, spend_date, provider: str, cost_usd: Decimal) -> Decimal:
        self.adds.append(cost_usd)
        self.spend += cost_usd
        return self.spend


def _executor(outcomes: dict[Rung, tuple]):
    async def execute(rung: Rung):
        return outcomes[rung]

    return execute


def _run(rungs, outcomes, *, threshold=0.8, validate=lambda p: (True, "ok")):
    return asyncio.run(
        run_ladder(rungs, _executor(outcomes), validate, threshold, "test"),
    )


@pytest.fixture(autouse=True)
def _reset_budget(monkeypatch):
    claude_budget.configure_for_tests()
    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "6.45")
    claude_budget.configure_for_tests(
        now_fn=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        repo_reader=FakeSpendRepo(),
        repo_writer=FakeSpendRepo(),
    )
    yield
    claude_budget.configure_for_tests()


def test_default_ladder_rungs_have_no_fallback_only_flag():
    from intelligence.cascade.core import DEFAULT_LADDER

    assert all(not r.fallback_only for r in DEFAULT_LADDER)
    assert [(r.provider, r.model) for r in DEFAULT_LADDER] == [
        ("foundry", "DeepSeek-V4-Flash"),
        ("anthropic", "claude-haiku-5-5"),
        ("foundry", "gpt-5.4-nano"),
        ("foundry", "gpt-5.4-mini"),
        ("anthropic", "claude-sonnet-5-5"),
    ]


def test_live_bill_coding_default_ladder_pinned():
    assert [(r.provider, r.model, r.fallback_only) for r in DEFAULT_BILL_CODING_SUGGESTION_LADDER] == [
        ("anthropic", "claude-haiku-5-5", False),
        ("foundry", "DeepSeek-V4-Flash", True),
        ("foundry", "gpt-5.4-nano", True),
        ("foundry", "gpt-5.4-mini", True),
    ]
    assert resolve_threshold("bill_coding_suggestion", 0.5) == 0.5


def test_claude_parsed_below_bar_skips_foundry_fallback():
    ok = ({"label": "a", "confidence": 0.3}, 0.3, Usage(), None)
    ladder = (HAIKU, DEEP)
    result = _run(ladder, {HAIKU: ok}, threshold=0.5)
    assert result.needs_human
    assert len(result.attempts) == 2
    assert result.attempts[1].error == "skipped:fallback_only"


def test_claude_error_allows_foundry_fallback():
    err = (None, None, Usage(), "timeout")
    ok = ({"label": "a", "confidence": 0.9}, 0.9, Usage(), None)
    result = _run((HAIKU, DEEP), {HAIKU: err, DEEP: ok}, threshold=0.5)
    assert result.accepted
    assert result.winning_rung == DEEP


def test_header_haiku_low_confidence_escalates_sonnet_not_foundry():
    low = ({"label": "a", "confidence": 0.2}, 0.2, Usage(), None)
    high = ({"label": "a", "confidence": 0.9}, 0.9, Usage(), None)
    ladder = (HAIKU, SONNET, DEEP)
    result = _run(ladder, {HAIKU: low, SONNET: high}, threshold=0.4)
    assert result.accepted
    assert result.winning_rung == SONNET
    assert DEEP not in [a.rung for a in result.attempts]


def test_daily_cap_skips_claude_and_fallback_only_foundry():
    cap_err = (None, None, Usage(), "claude_unavailable:daily_cap")
    ok = ({"label": "a", "confidence": 0.99}, 0.99, Usage(), None)
    result = _run((HAIKU, DEEP), {HAIKU: cap_err, DEEP: ok}, threshold=0.5)
    assert result.needs_human
    assert result.stopped_reason == "daily_cap"
    assert result.attempts[0].error == "claude_unavailable:daily_cap"
    assert result.attempts[1].error == "skipped:fallback_only"


def test_daily_cap_on_claude_but_later_foundry_accept_has_no_stopped_reason():
    fx = Rung("foundry", "DeepSeek-V4-Flash")
    fy = Rung("foundry", "gpt-5.4-nano")
    cap_err = (None, None, Usage(), "claude_unavailable:daily_cap")
    fail = (None, None, Usage(), "timeout")
    ok = ({"label": "a", "confidence": 0.95}, 0.95, Usage(), None)
    result = _run((fx, HAIKU, fy), {fx: fail, HAIKU: cap_err, fy: ok}, threshold=0.5)
    assert result.accepted
    assert result.winning_rung == fy
    assert result.stopped_reason is None


def test_daily_cap_default_ladder_still_runs_plain_foundry():
    from intelligence.cascade.core import DEFAULT_LADDER

    first = DEFAULT_LADDER[0]
    haiku = DEFAULT_LADDER[1]
    nano = DEFAULT_LADDER[2]
    cap_err = (None, None, Usage(), "claude_unavailable:daily_cap")
    ok = ({"label": "x", "confidence": 0.99}, 0.99, Usage(), None)
    outcomes = {
        first: (None, None, Usage(), "fail"),
        haiku: cap_err,
        nano: ok,
    }
    result = _run(DEFAULT_LADDER, outcomes, threshold=0.5)
    assert result.accepted
    assert result.winning_rung == nano


def test_cap_zero_disables_claude_but_allows_fallback():
    disabled = (None, None, Usage(), "claude_unavailable:disabled")
    ok = ({"label": "a", "confidence": 0.9}, 0.9, Usage(), None)
    result = _run((HAIKU, DEEP), {HAIKU: disabled, DEEP: ok}, threshold=0.5)
    assert result.accepted
    assert result.winning_rung == DEEP
    assert result.attempts[0].error == "claude_unavailable:disabled"


def test_credit_breaker_skips_claude_allows_fallback():
    credit = (None, None, Usage(), "claude_unavailable:credit")
    ok = ({"label": "a", "confidence": 0.9}, 0.9, Usage(), None)
    result = _run((HAIKU, DEEP), {HAIKU: credit, DEEP: ok}, threshold=0.5)
    assert result.accepted
    assert result.attempts[0].error == "claude_unavailable:credit"


def test_402_opens_breaker_one_warning(caplog):
    usage = Usage(input_tokens=100, output_tokens=10)
    claude_budget.configure_for_tests(
        now_fn=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        repo_reader=FakeSpendRepo(),
        repo_writer=FakeSpendRepo(),
    )
    with caplog.at_level("WARNING"):
        claude_budget.finalize_after_attempt(
            usage=usage, model="claude-haiku-5-5", error="http_402: billing",
        )
        claude_budget.finalize_after_attempt(
            usage=usage, model="claude-haiku-5-5", error="http_402: billing",
        )
    assert claude_budget.is_credit_breaker_open()
    assert sum("claude credit exhausted" in r.message for r in caplog.records) == 1


def test_credit_breaker_closes_and_reopens_logs_twice(caplog):
    t0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    times = [t0]

    def now_fn():
        return times[-1]

    claude_budget.configure_for_tests(
        now_fn=now_fn, repo_reader=FakeSpendRepo(), repo_writer=FakeSpendRepo(),
    )
    with caplog.at_level("WARNING"):
        claude_budget.open_credit_breaker()
        times.append(datetime(2026, 10, 18, 13, 1, tzinfo=timezone.utc))
        assert not claude_budget.is_credit_breaker_open()
        claude_budget.open_credit_breaker()
    assert sum("claude credit exhausted" in r.message for r in caplog.records) == 2


def test_400_credit_balance_opens_breaker():
    assert claude_budget._is_credit_exhausted_error("http_400: credit balance too low")
    assert not claude_budget._is_credit_exhausted_error("http_400: bad request")


def test_spend_recorded_after_claude_usage():
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(
        now_fn=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        repo_reader=repo,
        repo_writer=repo,
    )
    usage = Usage(input_tokens=1_000_000, output_tokens=0)
    claude_budget.finalize_after_attempt(
        usage=usage, model="claude-haiku-5-5", error=None,
    )
    assert len(repo.adds) == 1
    assert isinstance(repo.adds[0], Decimal)


def test_spend_recorded_on_error_after_tokens_used():
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(
        now_fn=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        repo_reader=repo,
        repo_writer=repo,
    )
    usage = Usage(input_tokens=500_000, output_tokens=100)
    claude_budget.finalize_after_attempt(
        usage=usage, model="claude-haiku-5-5", error="billing_error: stream died",
    )
    assert len(repo.adds) == 1


def test_read_failure_fail_closed_blocks_claude_day(caplog):
    class BoomRepo:
        def read_daily_spend(self, *a, **k):
            raise RuntimeError("db down")

        def add_daily_spend(self, *a, **k):
            raise RuntimeError("db down")

    claude_budget.configure_for_tests(
        now_fn=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        repo_reader=BoomRepo(),
        repo_writer=BoomRepo(),
    )
    with caplog.at_level("WARNING"):
        assert claude_budget.is_claude_day_stopped()
    assert any("LlmDailySpend read failed" in r.message for r in caplog.records)


def test_rollback_lever_restores_foundry_first_ladder(monkeypatch):
    monkeypatch.setenv(
        "CASCADE_LADDER_BILL_CODING_SUGGESTION",
        "foundry:DeepSeek-V4-Flash,foundry:gpt-5.4-nano,foundry:gpt-5.4-mini",
    )
    monkeypatch.setenv("CASCADE_THRESHOLD_BILL_CODING_SUGGESTION", "0.85")
    ladder = resolve_ladder("bill_coding_suggestion", DEFAULT_BILL_CODING_SUGGESTION_LADDER)
    assert all(not r.fallback_only for r in ladder)
    assert [(r.provider, r.model) for r in ladder] == [
        ("foundry", "DeepSeek-V4-Flash"),
        ("foundry", "gpt-5.4-nano"),
        ("foundry", "gpt-5.4-mini"),
    ]
    assert resolve_threshold("bill_coding_suggestion", 0.5) == 0.85
