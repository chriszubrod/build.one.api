"""U-676f R2: AnthropicTransport metering via httpx.MockTransport (real stream path)."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from intelligence.cascade import claude_budget
from intelligence.messages.types import Message, Text
from intelligence.transport import anthropic as anthropic_mod
from intelligence.transport.anthropic import AnthropicTransport
from intelligence.transport.base import TransportError


class FakeSpendRepo:
    def __init__(self, spend: Decimal = Decimal("0")) -> None:
        self.spend = spend
        self.adds: list[Decimal] = []
        self.lock = threading.Lock()

    def read_daily_spend(self, spend_date, provider: str) -> Decimal:
        return self.spend

    def add_daily_spend(self, spend_date, provider: str, cost_usd: Decimal) -> Decimal:
        with self.lock:
            self.adds.append(cost_usd)
            self.spend += cost_usd
            return self.spend


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    claude_budget.configure_for_tests()
    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "6.45")
    monkeypatch.delenv("ANTHROPIC_FOUNDRY_BASE_URL", raising=False)
    yield
    claude_budget.configure_for_tests()


def _mock_client(monkeypatch, handler):
    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)

    monkeypatch.setattr(anthropic_mod.httpx, "AsyncClient", Client)


async def _collect_events(model: str = "claude-haiku-5-5"):
    transport = AnthropicTransport(api_key="sk-test")
    return [
        ev
        async for ev in transport.stream(
            [Message(role="user", content=[Text(text="hi")])],
            model=model,
        )
    ]


def test_402_opens_breaker_via_transport(monkeypatch, caplog):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            402,
            json={
                "type": "error",
                "error": {"type": "billing_error", "message": "no credit"},
            },
        ),
    )
    with caplog.at_level("WARNING"):
        asyncio.run(_collect_events())
        asyncio.run(_collect_events())
    assert claude_budget.is_credit_breaker_open()
    assert sum("claude credit exhausted" in r.message for r in caplog.records) == 1


def test_400_credit_balance_opens_breaker_via_transport(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            400,
            json={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low",
                },
            },
        ),
    )
    asyncio.run(_collect_events())
    assert claude_budget.is_credit_breaker_open()


def test_plain_400_does_not_open_breaker(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            400,
            json={
                "type": "error",
                "error": {"type": "invalid_request_error", "message": "bad field"},
            },
        ),
    )
    asyncio.run(_collect_events())
    assert not claude_budget.is_credit_breaker_open()


def test_breaker_open_second_call_makes_no_http(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(402, json={"type": "error", "error": {"type": "billing_error"}})

    _mock_client(monkeypatch, handler)
    asyncio.run(_collect_events())
    assert calls["n"] == 1
    asyncio.run(_collect_events())
    assert calls["n"] == 1


def test_instream_error_after_message_start_records_spend(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    body = (
        "event: message_start\ndata: "
        + json.dumps(
            {
                "type": "message_start",
                "message": {
                    "model": "claude-haiku-5-5",
                    "usage": {"input_tokens": 500_000, "output_tokens": 1},
                },
            },
        )
        + "\n\n"
        "event: error\ndata: "
        + json.dumps(
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "overloaded"},
            },
        )
        + "\n\n"
    )
    _mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        ),
    )
    asyncio.run(_collect_events())
    assert repo.adds, "no spend recorded after in-stream error"


def test_read_timeout_after_message_start_records_spend(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    first_event = (
        "event: message_start\ndata: "
        + json.dumps(
            {
                "type": "message_start",
                "message": {
                    "model": "claude-haiku-5-5",
                    "usage": {"input_tokens": 500_000, "output_tokens": 0},
                },
            },
        )
        + "\n\n"
    )

    def handler(request):
        resp = httpx.Response(
            200,
            content=first_event.encode(),
            headers={"content-type": "text/event-stream"},
        )
        orig_aiter = resp.aiter_lines

        async def aiter_lines():
            async for line in orig_aiter():
                yield line
            raise httpx.ReadTimeout("dropped")

        resp.aiter_lines = aiter_lines  # type: ignore[method-assign]
        return resp

    _mock_client(monkeypatch, handler)
    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(_collect_events())
    assert repo.adds, "no spend recorded after mid-stream timeout"


class SlowWriterRepo(FakeSpendRepo):
    def add_daily_spend(self, spend_date, provider: str, cost_usd: Decimal) -> Decimal:
        time.sleep(0.05)
        return super().add_daily_spend(spend_date, provider, cost_usd)


def test_unsaved_spend_writes_to_prior_utc_day_after_midnight():
    class DatedRepo(FakeSpendRepo):
        def __init__(self):
            super().__init__()
            self.by_date: dict = {}

        def add_daily_spend(self, spend_date, provider: str, cost_usd: Decimal) -> Decimal:
            time.sleep(0.02)
            self.by_date[spend_date] = self.by_date.get(spend_date, Decimal("0")) + cost_usd
            return self.by_date[spend_date]

    repo = DatedRepo()
    t0 = datetime(2026, 10, 17, 23, 59, tzinfo=timezone.utc)
    times = [t0]

    def now_fn():
        return times[-1]

    claude_budget.configure_for_tests(
        now_fn=now_fn, repo_reader=repo, repo_writer=repo,
    )
    claude_budget.record_anthropic_spend(Decimal("0.01"))
    times.append(datetime(2026, 10, 18, 0, 0, 1, tzinfo=timezone.utc))
    claude_budget.record_anthropic_spend(Decimal("0.02"))
    assert repo.by_date[datetime(2026, 10, 17).date()] == Decimal("0.01")
    assert repo.by_date[datetime(2026, 10, 18).date()] == Decimal("0.02")


def test_concurrent_record_spend_exact_total():
    repo = SlowWriterRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)

    async def main():
        await asyncio.gather(
            *[
                asyncio.to_thread(
                    claude_budget.record_anthropic_spend, Decimal("0.010000"),
                )
                for _ in range(5)
            ],
        )

    asyncio.run(main())
    assert repo.spend == Decimal("0.05")
    assert claude_budget._unsaved_spend == Decimal("0")


def test_unpriced_model_meters_at_sonnet_rate(caplog, monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    body = (
        "event: message_start\ndata: "
        + json.dumps(
            {
                "type": "message_start",
                "message": {
                    "model": "claude-unknown-future",
                    "usage": {"input_tokens": 1_000_000, "output_tokens": 0},
                },
            },
        )
        + "\n\n"
        "event: message_stop\ndata: "
        + json.dumps({"type": "message_stop"})
        + "\n\n"
    )
    _mock_client(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        ),
    )
    with caplog.at_level("WARNING"):
        asyncio.run(_collect_events(model="claude-unknown-future"))
        asyncio.run(_collect_events(model="claude-unknown-future"))
    assert repo.adds
    assert repo.adds[0] == Decimal("2.000000")
    assert sum("no pricing for anthropic model" in r.message for r in caplog.records) == 1
