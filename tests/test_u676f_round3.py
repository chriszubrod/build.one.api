"""U-676f R3: metering-before-terminal-yield, midnight carry-over, settings
hardening, monotonic spend cache, unsaved mirror, lock-free DB write."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest

from config import Settings
from intelligence.cascade import claude_budget
from intelligence.cascade.core import Rung
from intelligence.messages.types import Message, Text
from intelligence.transport import anthropic as anthropic_mod
from intelligence.transport.anthropic import AnthropicTransport
from intelligence.transport.base import Done, TransportError, TurnStart, Usage
from intelligence.transport.cascade import CascadeTransport


# ---------------------------------------------------------------- fixtures

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
    monkeypatch.delenv("CLAUDE_CREDIT_BREAKER_MINUTES", raising=False)
    monkeypatch.delenv("CLAUDE_SPEND_CACHE_SECONDS", raising=False)
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


def _msg_start(inp: int, out: int = 1, model: str = "claude-haiku-5-5") -> str:
    return "event: message_start\ndata: " + json.dumps({
        "type": "message_start",
        "message": {"model": model, "usage": {"input_tokens": inp, "output_tokens": out}},
    }) + "\n\n"


_OK_TAIL = (
    "event: content_block_delta\ndata: " + json.dumps({
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "text_delta", "text": "{}"},
    }) + "\n\n"
    "event: message_delta\ndata: " + json.dumps({
        "type": "message_delta", "delta": {"stop_reason": "end_turn"},
        "usage": {"output_tokens": 10},
    }) + "\n\n"
    "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"
)


def _instream_error(err_type: str = "overloaded_error") -> str:
    return "event: error\ndata: " + json.dumps({
        "type": "error", "error": {"type": err_type, "message": "x"},
    }) + "\n\n"


def _sse(body: str):
    return lambda req: httpx.Response(
        200, content=body.encode(), headers={"content-type": "text/event-stream"},
    )


def _stream(model: str = "claude-haiku-5-5"):
    t = AnthropicTransport(api_key="sk-test")
    return t.stream([Message(role="user", content=[Text(text="hi")])], model=model)


# ------------------------------------------- A. meter BEFORE terminal yield

@pytest.mark.parametrize("status,payload", [
    (402, {"type": "error", "error": {"type": "billing_error", "message": "no credit"}}),
    (400, {"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "Your credit balance is too low"}}),
])
def test_a_breaker_open_when_consumer_receives_http_error(monkeypatch, status, payload):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(monkeypatch, lambda r: httpx.Response(status, json=payload))
    seen = {}

    async def go():
        g = _stream()
        async for ev in g:
            if isinstance(ev, TransportError):
                seen["code"] = ev.code
                seen["open"] = claude_budget.is_credit_breaker_open()
                break
        await g.aclose()

    asyncio.run(go())
    assert seen.get("code") == f"http_{status}", seen
    assert seen["open"] is True, "breaker not open at the moment TransportError is received"
    assert claude_budget.is_credit_breaker_open()


def test_a_instream_error_spend_recorded_when_consumer_receives_it(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(monkeypatch, _sse(_msg_start(300_000) + _instream_error()))
    seen = {}

    async def go():
        g = _stream()
        async for ev in g:
            if isinstance(ev, TransportError):
                seen["adds"] = list(repo.adds)
                break
        await g.aclose()

    asyncio.run(go())
    assert "adds" in seen, "no TransportError received"
    assert len(seen["adds"]) == 1 and seen["adds"][0] > 0, seen
    assert len(repo.adds) == 1, f"double-metered after close: {repo.adds}"


def test_a_instream_billing_error_breaker_open_when_received(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(monkeypatch, _sse(_msg_start(1000) + _instream_error("billing_error")))
    seen = {}

    async def go():
        g = _stream()
        async for ev in g:
            if isinstance(ev, TransportError):
                seen["open"] = claude_budget.is_credit_breaker_open()
                seen["adds"] = list(repo.adds)
                break
        await g.aclose()

    asyncio.run(go())
    assert seen.get("open") is True, seen
    assert len(seen["adds"]) == 1, seen
    assert len(repo.adds) == 1, repo.adds


def test_a_done_spend_recorded_when_consumer_receives_done(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(monkeypatch, _sse(_msg_start(100_000) + _OK_TAIL))
    seen = {}

    async def go():
        g = _stream()
        async for ev in g:
            if isinstance(ev, Done):
                seen["adds"] = list(repo.adds)
                break
        await g.aclose()

    asyncio.run(go())
    assert "adds" in seen, "no Done received"
    assert len(seen["adds"]) == 1 and seen["adds"][0] > 0, seen
    assert len(repo.adds) == 1, f"double-metered after close: {repo.adds}"


def test_a_full_consume_meters_exactly_once(monkeypatch):
    for body in (_msg_start(100_000) + _OK_TAIL, _msg_start(100_000) + _instream_error()):
        repo = FakeSpendRepo()
        claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
        _mock_client(monkeypatch, _sse(body))

        async def go():
            return [e async for e in _stream()]

        asyncio.run(go())
        assert len(repo.adds) == 1, repo.adds


def test_a_http_402_full_consume_breaker_opens_once_no_spend(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(monkeypatch, lambda r: httpx.Response(
        402, json={"type": "error", "error": {"type": "billing_error"}}))

    async def go():
        return [e async for e in _stream()]

    evs = asyncio.run(go())
    assert isinstance(evs[-1], TransportError)
    assert claude_budget.is_credit_breaker_open()
    assert repo.adds == []


def test_a_cascade_402_second_anthropic_rung_makes_no_http(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    calls: list[str] = []

    def handler(req):
        calls.append(json.loads(req.content)["model"])
        return httpx.Response(
            402, json={"type": "error", "error": {"type": "billing_error", "message": "x"}},
        )

    _mock_client(monkeypatch, handler)
    t = AnthropicTransport(api_key="sk-test")
    ct = CascadeTransport(
        ladder=(Rung("anthropic", "claude-haiku-5-5"), Rung("anthropic", "claude-sonnet-5-5")),
        transport_for=lambda p: t,
    )

    async def go():
        return [e async for e in ct.stream(
            [Message(role="user", content=[Text(text="hi")])], model="x")]

    asyncio.run(go())
    assert calls == ["claude-haiku-5-5"], calls


def test_a_cascade_instream_error_spend_recorded_by_end_of_stream(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(monkeypatch, _sse(_msg_start(300_000) + _instream_error()))
    t = AnthropicTransport(api_key="sk-test")
    ct = CascadeTransport(ladder=(Rung("anthropic", "claude-haiku-5-5"),),
                          transport_for=lambda p: t)
    seen = {}

    async def go():
        [e async for e in ct.stream(
            [Message(role="user", content=[Text(text="hi")])], model="x")]
        seen["adds"] = list(repo.adds)

    asyncio.run(go())
    assert len(seen["adds"]) == 1, seen


# ----------------------------------------- B. break after TurnStart + aclose

def test_b_break_after_turnstart_aclose_records_input_spend(monkeypatch):
    repo = FakeSpendRepo()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    _mock_client(monkeypatch, _sse(_msg_start(200_000, 0) + _OK_TAIL))
    seen = {}

    async def go():
        g = _stream()
        async for ev in g:
            seen["first"] = ev
            break
        await g.aclose()

    asyncio.run(go())
    assert isinstance(seen.get("first"), TurnStart), seen
    assert len(repo.adds) == 1, repo.adds
    expected = claude_budget._usage_cost_usd(
        "claude-haiku-5-5", Usage(input_tokens=200_000),
    )
    assert repo.adds[0] >= expected > 0, (repo.adds, expected)


# --------------------------------------- C. failed write at 23:59 → carry over

def test_c_failed_write_before_midnight_lands_on_prior_day():
    yesterday = date(2026, 10, 17)
    today = date(2026, 10, 18)

    class DatedFlaky:
        def __init__(self):
            self.by_date: dict = {}
            self.fail = True

        def read_daily_spend(self, spend_date, provider):
            return self.by_date.get(spend_date, Decimal("0"))

        def add_daily_spend(self, spend_date, provider, cost_usd):
            if self.fail:
                raise RuntimeError("db down")
            self.by_date[spend_date] = self.by_date.get(spend_date, Decimal("0")) + cost_usd
            return self.by_date[spend_date]

    repo = DatedFlaky()
    times = [datetime(2026, 10, 17, 23, 59, tzinfo=timezone.utc)]
    claude_budget.configure_for_tests(
        now_fn=lambda: times[-1], repo_reader=repo, repo_writer=repo,
    )
    claude_budget.record_anthropic_spend(Decimal("0.01"))
    assert repo.by_date == {}
    repo.fail = False
    times.append(datetime(2026, 10, 18, 0, 0, 1, tzinfo=timezone.utc))
    claude_budget.record_anthropic_spend(Decimal("0.02"))
    assert repo.by_date.get(yesterday) == Decimal("0.01"), repo.by_date
    assert repo.by_date.get(today) == Decimal("0.02"), repo.by_date
    assert not any(v > 0 for v in claude_budget._unsaved_by_date.values()), \
        claude_budget._unsaved_by_date
    assert claude_budget._unsaved_spend == Decimal("0")


# ------------------------------------------------ D. settings hardening

def test_d_garbage_env_constructs_and_defaults(monkeypatch):
    monkeypatch.setenv("CLAUDE_CREDIT_BREAKER_MINUTES", "abc")
    monkeypatch.setenv("CLAUDE_SPEND_CACHE_SECONDS", "abc")
    s = Settings()
    assert claude_budget._breaker_minutes(s) == 60
    assert claude_budget._cache_ttl_seconds(s) == 30


@pytest.mark.parametrize("raw", ["0", "-5"])
def test_d_nonpositive_breaker_minutes_default_60(monkeypatch, raw):
    monkeypatch.setenv("CLAUDE_CREDIT_BREAKER_MINUTES", raw)
    assert claude_budget._breaker_minutes(Settings()) == 60


def test_d_negative_cache_seconds_default_30_zero_valid(monkeypatch):
    monkeypatch.setenv("CLAUDE_SPEND_CACHE_SECONDS", "-1")
    assert claude_budget._cache_ttl_seconds(Settings()) == 30
    monkeypatch.setenv("CLAUDE_SPEND_CACHE_SECONDS", "0")
    assert claude_budget._cache_ttl_seconds(Settings()) == 0


def test_d_valid_values_pass_through(monkeypatch):
    monkeypatch.setenv("CLAUDE_CREDIT_BREAKER_MINUTES", "15")
    monkeypatch.setenv("CLAUDE_SPEND_CACHE_SECONDS", "5")
    s = Settings()
    assert claude_budget._breaker_minutes(s) == 15
    assert claude_budget._cache_ttl_seconds(s) == 5


def test_d_garbage_breaker_minutes_open_breaker_works(monkeypatch):
    monkeypatch.setenv("CLAUDE_CREDIT_BREAKER_MINUTES", "abc")
    t0 = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    times = [t0]
    claude_budget.configure_for_tests(now_fn=lambda: times[-1])
    claude_budget.open_credit_breaker()
    assert claude_budget.is_credit_breaker_open()
    times.append(datetime(2026, 10, 18, 12, 59, tzinfo=timezone.utc))
    assert claude_budget.is_credit_breaker_open()
    times.append(datetime(2026, 10, 18, 13, 0, 1, tzinfo=timezone.utc))
    assert not claude_budget.is_credit_breaker_open()


# ------------------------------------- E. stale read must not lower the cache

def test_e_stale_read_does_not_lower_cached_total(monkeypatch):
    monkeypatch.setenv("CLAUDE_SPEND_CACHE_SECONDS", "0")  # every read hits the DB
    v0 = Decimal("1.00")
    c = Decimal("0.50")

    class Racing:
        def read_daily_spend(self, spend_date, provider):
            return v0  # older snapshot taken before the write committed

        def add_daily_spend(self, spend_date, provider, cost_usd):
            return v0 + cost_usd

    repo = Racing()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    claude_budget.record_anthropic_spend(c)
    time.sleep(0.01)
    assert claude_budget.get_today_anthropic_spend() >= v0 + c


def test_e_stale_read_cannot_unstop_capped_day(monkeypatch):
    monkeypatch.setenv("CLAUDE_SPEND_CACHE_SECONDS", "0")
    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "1.00")

    class Racing:
        def read_daily_spend(self, spend_date, provider):
            return Decimal("0.90")

        def add_daily_spend(self, spend_date, provider, cost_usd):
            return Decimal("0.90") + cost_usd

    repo = Racing()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    claude_budget.record_anthropic_spend(Decimal("0.20"))
    time.sleep(0.01)
    assert claude_budget.is_claude_day_stopped() is True


def test_e_new_day_read_replaces_cache():
    """Monotonic max applies only within the same day."""
    times = [datetime(2026, 10, 17, 23, 59, tzinfo=timezone.utc)]

    class Repo:
        def read_daily_spend(self, spend_date, provider):
            return Decimal("0") if spend_date == date(2026, 10, 18) else Decimal("5")

        def add_daily_spend(self, spend_date, provider, cost_usd):
            return Decimal("5") + cost_usd

    repo = Repo()
    claude_budget.configure_for_tests(now_fn=lambda: times[-1], repo_reader=repo, repo_writer=repo)
    claude_budget.record_anthropic_spend(Decimal("1"))
    times.append(datetime(2026, 10, 18, 0, 0, 1, tzinfo=timezone.utc))
    assert claude_budget.get_today_anthropic_spend() == Decimal("0")


# ------------------------------------------ F. mirror after a failed write

def test_f_unsaved_mirror_matches_after_failed_write():
    class Bad:
        def read_daily_spend(self, spend_date, provider):
            return Decimal("0")

        def add_daily_spend(self, spend_date, provider, cost_usd):
            raise RuntimeError("db down")

    repo = Bad()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    claude_budget.record_anthropic_spend(Decimal("0.03"))
    assert claude_budget._unsaved_spend == sum(
        claude_budget._unsaved_by_date.values(), Decimal("0"))
    assert claude_budget._unsaved_spend == Decimal("0.03")
    claude_budget.record_anthropic_spend(Decimal("0.02"))
    assert claude_budget._unsaved_spend == Decimal("0.05")
    assert claude_budget._unsaved_spend == sum(
        claude_budget._unsaved_by_date.values(), Decimal("0"))


# -------------------------------------- G. lock not held during the DB write

class _BlockingFirstWrite(FakeSpendRepo):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()
        self.first_entered = threading.Event()
        self._n = 0
        self._n_lock = threading.Lock()

    def add_daily_spend(self, spend_date, provider, cost_usd):
        with self._n_lock:
            self._n += 1
            n = self._n
        if n == 1:
            self.first_entered.set()
            self.release.wait(timeout=10)
        return super().add_daily_spend(spend_date, provider, cost_usd)


def _run_while_first_write_blocked(fn):
    repo = _BlockingFirstWrite()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    t1 = threading.Thread(
        target=claude_budget.record_anthropic_spend, args=(Decimal("0.01"),), daemon=True)
    t1.start()
    try:
        assert repo.first_entered.wait(timeout=5), "first write never started"
        done = threading.Event()

        def second():
            fn()
            done.set()

        t2 = threading.Thread(target=second, daemon=True)
        t2.start()
        finished = done.wait(timeout=2)
    finally:
        repo.release.set()
        t1.join(timeout=10)
    return finished, repo


def test_g_day_stopped_check_not_blocked_by_inflight_write():
    finished, _ = _run_while_first_write_blocked(claude_budget.is_claude_day_stopped)
    assert finished, "is_claude_day_stopped waited on an in-flight DB write"


def test_g_second_record_not_blocked_by_inflight_write():
    finished, repo = _run_while_first_write_blocked(
        lambda: claude_budget.record_anthropic_spend(Decimal("0.02")))
    assert finished, "record_anthropic_spend waited on an in-flight DB write (lock held)"
    time.sleep(0.05)
    assert repo.spend == Decimal("0.03"), repo.adds
    assert claude_budget._unsaved_spend == Decimal("0")


def test_g_concurrent_records_exact_total_slow_writer():
    class Slow(FakeSpendRepo):
        def add_daily_spend(self, spend_date, provider, cost_usd):
            time.sleep(0.05)
            return super().add_daily_spend(spend_date, provider, cost_usd)

    repo = Slow()
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    threads = [threading.Thread(
        target=claude_budget.record_anthropic_spend, args=(Decimal("0.010000"),))
        for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert repo.spend == Decimal("0.05"), repo.adds
    assert claude_budget._unsaved_spend == Decimal("0")
    assert not claude_budget._unsaved_by_date
    assert claude_budget.get_today_anthropic_spend() == Decimal("0.05")


# ------------------- round-3 review: F1 settle order, F2 breaker clamp, F3 metering failure

def test_f1_settle_never_hides_persisted_spend(monkeypatch):
    """Between storing the DB total and dropping the in-flight amount, an
    unlocked reader may over-count but must never see less than true spend."""
    repo = FakeSpendRepo(Decimal("1.00"))
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    today = claude_budget._utc_today()
    assert claude_budget.get_today_anthropic_spend() == Decimal("1.00")
    seen: list[Decimal] = []
    real_settle = claude_budget._settle_inflight

    def spying_settle(spend_date, amount):
        real_settle(spend_date, amount)
        # the in-flight amount is gone: the persisted total must already be cached
        seen.append(claude_budget._effective_db_total(today))

    monkeypatch.setattr(claude_budget, "_settle_inflight", spying_settle)
    claude_budget.record_anthropic_spend(Decimal("0.50"))
    assert seen and min(seen) >= Decimal("1.50"), seen
    assert claude_budget._effective_db_total(today) == Decimal("1.50")


def test_f2_huge_breaker_minutes_clamped_and_breaker_opens(monkeypatch):
    monkeypatch.setenv("CLAUDE_CREDIT_BREAKER_MINUTES", "5000000000")
    assert claude_budget._breaker_minutes(Settings()) == 60
    claude_budget.open_credit_breaker()
    assert claude_budget.is_credit_breaker_open()


def test_f2_one_week_breaker_is_allowed(monkeypatch):
    monkeypatch.setenv("CLAUDE_CREDIT_BREAKER_MINUTES", str(7 * 24 * 60))
    assert claude_budget._breaker_minutes(Settings()) == 7 * 24 * 60


def test_f3_metering_failure_does_not_replace_done(monkeypatch):
    """A finished Claude answer stays an answer when recording spend raises."""
    _mock_client(monkeypatch, _sse(_msg_start(100) + _OK_TAIL))

    async def boom(**_kw):
        raise RuntimeError("metering down")

    monkeypatch.setattr(claude_budget, "post_call_metering", boom)

    async def go():
        return [ev async for ev in _stream()]

    events = asyncio.run(go())
    assert isinstance(events[-1], Done)
    assert not any(isinstance(ev, TransportError) for ev in events)


def test_f1_cached_read_waits_for_spend_lock():
    """The gate's cache-hit and read-failure branches read cache + unsaved +
    in-flight under _spend_lock; an unlocked read can miss an amount in motion."""
    repo = FakeSpendRepo(Decimal("1.00"))
    claude_budget.configure_for_tests(repo_reader=repo, repo_writer=repo)
    assert claude_budget.get_today_anthropic_spend() == Decimal("1.00")  # warm cache
    done = threading.Event()

    def reader():
        claude_budget.get_today_anthropic_spend()
        done.set()

    with claude_budget._spend_lock:
        t = threading.Thread(target=reader, daemon=True)
        t.start()
        assert not done.wait(0.3), "cached read did not take _spend_lock"
    assert done.wait(5)
    t.join(5)
