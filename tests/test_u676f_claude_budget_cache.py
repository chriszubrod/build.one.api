"""U-676f: Claude spend cache TTL, unsaved buffer, fail-closed grace."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from intelligence.cascade import claude_budget


class FakeSpendRepo:
    def __init__(self) -> None:
        self.spend = Decimal("1.00")
        self.reads = 0

    def read_daily_spend(self, spend_date, provider: str) -> Decimal:
        self.reads += 1
        return self.spend

    def add_daily_spend(self, spend_date, provider: str, cost_usd: Decimal) -> Decimal:
        self.spend += cost_usd
        return self.spend


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    claude_budget.configure_for_tests()
    monkeypatch.setenv("CLAUDE_SPEND_CACHE_SECONDS", "30")
    yield
    claude_budget.configure_for_tests()


def test_cache_ttl_avoids_repeated_reads(monkeypatch):
    repo = FakeSpendRepo()
    fixed = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    claude_budget.configure_for_tests(now_fn=lambda: fixed, repo_reader=repo, repo_writer=repo)
    assert claude_budget.get_today_anthropic_spend() == Decimal("1.00")
    assert claude_budget.get_today_anthropic_spend() == Decimal("1.00")
    assert repo.reads == 1

    monkeypatch.setenv("CLAUDE_SPEND_CACHE_SECONDS", "0")
    claude_budget.configure_for_tests(now_fn=lambda: fixed, repo_reader=repo, repo_writer=repo)
    claude_budget.get_today_anthropic_spend()
    claude_budget.get_today_anthropic_spend()
    assert repo.reads == 3


def test_local_cache_increment_after_record():
    repo = FakeSpendRepo()
    fixed = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    claude_budget.configure_for_tests(now_fn=lambda: fixed, repo_reader=repo, repo_writer=repo)
    claude_budget.get_today_anthropic_spend()
    claude_budget.record_anthropic_spend(Decimal("0.50"))
    assert claude_budget.get_today_anthropic_spend() == Decimal("1.50")
    assert repo.reads == 1


def test_write_failure_keeps_unsaved_then_retries():
    class FlakyWriter(FakeSpendRepo):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0

        def add_daily_spend(self, spend_date, provider: str, cost_usd: Decimal) -> Decimal:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("write down")
            return super().add_daily_spend(spend_date, provider, cost_usd)

    repo = FlakyWriter()
    fixed = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    claude_budget.configure_for_tests(now_fn=lambda: fixed, repo_reader=repo, repo_writer=repo)
    claude_budget.get_today_anthropic_spend()
    claude_budget.record_anthropic_spend(Decimal("0.25"))
    assert claude_budget.get_today_anthropic_spend() == Decimal("1.25")
    claude_budget.record_anthropic_spend(Decimal("0.10"))
    assert repo.spend == Decimal("1.35")
