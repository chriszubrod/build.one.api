"""U-676f: Claude budget gate wired into AnthropicTransport.stream."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from intelligence.cascade import claude_budget
from intelligence.messages.types import Message, Text
from intelligence.transport.anthropic import AnthropicTransport
from intelligence.transport.base import Done, TransportError, Usage


class FakeSpendRepo:
    def __init__(self, spend: Decimal = Decimal("0")) -> None:
        self.spend = spend

    def read_daily_spend(self, spend_date, provider: str) -> Decimal:
        return self.spend

    def add_daily_spend(self, spend_date, provider: str, cost_usd: Decimal) -> Decimal:
        self.spend += cost_usd
        return self.spend


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    claude_budget.configure_for_tests()
    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "6.45")
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_BASE_URL", raising=False)
    yield
    claude_budget.configure_for_tests()


async def _collect(transport: AnthropicTransport):
    events = []
    async for ev in transport.stream(
        [Message(role="user", content=[Text(text="hi")])],
        model="claude-haiku-5-5",
    ):
        events.append(ev)
    return events


def test_transport_daily_cap_no_http(monkeypatch):
    repo = FakeSpendRepo(spend=Decimal("6.45"))
    claude_budget.configure_for_tests(
        now_fn=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        repo_reader=repo,
        repo_writer=repo,
    )
    transport = AnthropicTransport(api_key="sk-test")

    class NoHttp:
        def __init__(self, *a, **k):
            raise AssertionError("no HTTP")

    monkeypatch.setattr("intelligence.transport.anthropic.httpx.AsyncClient", NoHttp)
    events = asyncio.run(_collect(transport))
    assert len(events) == 1
    assert isinstance(events[0], TransportError)
    assert events[0].code == "claude_unavailable:daily_cap"


def test_transport_no_key_one_warning(caplog, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    transport = AnthropicTransport(api_key=None)
    with caplog.at_level("WARNING"):
        events = asyncio.run(_collect(transport))
        events2 = asyncio.run(_collect(transport))
    assert events[0].code == "claude_unavailable:no_key"
    assert events2[0].code == "claude_unavailable:no_key"
    assert sum("ANTHROPIC_API_KEY" in r.message for r in caplog.records) == 1
