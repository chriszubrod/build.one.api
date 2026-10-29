"""U-701d: FoundryTransport metering via httpx.MockTransport (real stream path)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from intelligence.cascade import claude_budget, foundry_spend
from intelligence.messages.types import Message, Text
from intelligence.transport import foundry as foundry_mod
from intelligence.transport.base import Done, TransportError
from intelligence.transport.foundry import FoundryTransport

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


class FakeRepo:
    def __init__(self, spend: Decimal = Decimal("0")) -> None:
        self.spend = spend
        self.adds: list[tuple] = []

    def read_daily_spend(self, spend_date, provider):
        return self.spend

    def add_daily_spend(self, spend_date, provider, cost_usd):
        self.adds.append((spend_date, provider, cost_usd))
        self.spend += cost_usd
        return self.spend


class RaisingRepo(FakeRepo):
    def add_daily_spend(self, *a):
        raise RuntimeError("db down")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("FOUNDRY_DAILY_SPEND_CAP_USD", raising=False)
    monkeypatch.delenv("FOUNDRY_SPEND_ALERT_USD", raising=False)
    claude_budget.configure_for_tests()
    foundry_spend.configure_for_tests()
    yield
    claude_budget.configure_for_tests()
    foundry_spend.configure_for_tests()


def _use(repo) -> None:
    claude_budget.configure_for_tests(now_fn=lambda: NOW, repo_reader=repo, repo_writer=repo)
    foundry_spend.configure_for_tests(now_fn=lambda: NOW)


def _sse(usage: dict | None = None) -> bytes:
    chunks = [
        {"model": "gpt-5.4-mini", "choices": [{"delta": {"content": "hi"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 1000}},
    ]
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _mock_client(monkeypatch, handler):
    real = httpx.AsyncClient
    calls = {"n": 0}

    def counting(request):
        calls["n"] += 1
        return handler(request)

    class Client(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(counting)
            super().__init__(*a, **k)

    monkeypatch.setattr(foundry_mod.httpx, "AsyncClient", Client)
    return calls


def _ok(request):
    return httpx.Response(200, content=_sse(), headers={"content-type": "text/event-stream"})


async def _collect(api_key="k", endpoint="https://res.services.ai.azure.com", on_event=None):
    t = FoundryTransport(api_key=api_key, endpoint=endpoint)
    out = []
    async for ev in t.stream([Message(role="user", content=[Text(text="hello")])], model="gpt-5.4-mini"):
        if on_event:
            on_event(ev)
        out.append(ev)
    return out


def test_successful_call_is_metered_as_foundry(monkeypatch):
    repo = FakeRepo()
    _use(repo)
    _mock_client(monkeypatch, _ok)
    events = asyncio.run(_collect())
    assert isinstance(events[-1], Done)
    assert repo.adds == [(NOW.date(), "foundry", Decimal("0.005250"))]


def test_metering_happens_before_done_is_yielded(monkeypatch):
    repo = FakeRepo()
    _use(repo)
    _mock_client(monkeypatch, _ok)
    seen_at_done = []

    def on_event(ev):
        if isinstance(ev, Done):
            seen_at_done.append(list(repo.adds))

    asyncio.run(_collect(on_event=on_event))
    assert seen_at_done == [[(NOW.date(), "foundry", Decimal("0.005250"))]]


def test_consumer_that_stops_at_done_is_still_metered(monkeypatch):
    repo = FakeRepo()
    _use(repo)
    _mock_client(monkeypatch, _ok)

    async def run():
        t = FoundryTransport(api_key="k", endpoint="https://res.services.ai.azure.com")
        gen = t.stream([Message(role="user", content=[Text(text="x")])], model="gpt-5.4-mini")
        async for ev in gen:
            if isinstance(ev, Done):
                break
        await gen.aclose()

    asyncio.run(run())
    assert len(repo.adds) == 1


def test_done_reaches_consumer_when_repo_raises(monkeypatch):
    repo = RaisingRepo()
    _use(repo)
    _mock_client(monkeypatch, _ok)
    events = asyncio.run(_collect())
    assert isinstance(events[-1], Done)
    assert list(foundry_spend._unsaved.values()) == [Decimal("0.005250")]


def test_done_reaches_consumer_when_record_usage_raises(monkeypatch):
    _use(FakeRepo())
    _mock_client(monkeypatch, _ok)

    def boom(*a, **k):
        raise RuntimeError("metering regression")

    monkeypatch.setattr(foundry_spend, "record_usage", boom)
    events = asyncio.run(_collect())
    assert isinstance(events[-1], Done)
    assert events[-1].usage.input_tokens == 1000


def test_gated_call_emits_daily_cap_without_http(monkeypatch):
    monkeypatch.setenv("FOUNDRY_DAILY_SPEND_CAP_USD", "1.00")
    repo = FakeRepo(Decimal("1.00"))
    _use(repo)
    calls = _mock_client(monkeypatch, _ok)
    events = asyncio.run(_collect())
    assert calls["n"] == 0
    assert len(events) == 1 and isinstance(events[0], TransportError)
    assert events[0].code == "foundry_unavailable:daily_cap"
    assert events[0].message == "foundry budget gate: daily_cap"
    assert repo.adds == []


def test_gate_off_by_default_makes_the_call(monkeypatch):
    repo = FakeRepo(Decimal("1000"))
    _use(repo)
    calls = _mock_client(monkeypatch, _ok)
    events = asyncio.run(_collect())
    assert calls["n"] == 1
    assert isinstance(events[-1], Done)


def test_gate_exception_fails_open(monkeypatch):
    _use(FakeRepo())
    calls = _mock_client(monkeypatch, _ok)

    async def boom():
        raise RuntimeError("gate bug")

    monkeypatch.setattr(foundry_spend, "pre_call_gate", boom)
    events = asyncio.run(_collect())
    assert calls["n"] == 1 and isinstance(events[-1], Done)


def test_http_error_records_nothing(monkeypatch):
    repo = FakeRepo()
    _use(repo)
    _mock_client(monkeypatch, lambda r: httpx.Response(400, content=b"bad request"))
    events = asyncio.run(_collect())
    assert isinstance(events[-1], TransportError) and events[-1].code == "http_400"
    assert repo.adds == []
    assert foundry_spend._unsaved == {}


@pytest.mark.parametrize("kw,code", [({"api_key": None}, "missing_api_key"), ({"endpoint": None}, "missing_endpoint")])
def test_missing_key_or_endpoint_records_nothing(monkeypatch, kw, code):
    repo = FakeRepo()
    _use(repo)
    monkeypatch.setattr(foundry_mod.config, "Settings", lambda: type("S", (), {})())
    calls = _mock_client(monkeypatch, _ok)
    args = {"api_key": "k", "endpoint": "https://res.services.ai.azure.com", **kw}
    events = asyncio.run(_collect(**args))
    assert [e.code for e in events] == [code]
    assert calls["n"] == 0 and repo.adds == []


def test_transport_module_imports_first_in_a_fresh_interpreter():
    # A top-level foundry_spend import cycled back into this module (U-701d review P2).
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", "import intelligence.transport.foundry"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr[-500:]
