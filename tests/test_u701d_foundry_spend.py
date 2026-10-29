"""U-701d: Foundry spend metering, threshold alert, opt-in cap, isolation."""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from intelligence.cascade import claude_budget, foundry_spend
from intelligence.transport.base import Usage

DAY1 = datetime(2026, 10, 28, 23, 59, tzinfo=timezone.utc)
DAY2 = datetime(2026, 10, 29, 0, 1, tzinfo=timezone.utc)
ALERT_LOGGER = "intelligence.cascade.foundry_spend"


class FakeRepo:
    """Per-(date, provider) totals, like dbo.LlmDailySpend."""

    def __init__(self, start: dict | None = None) -> None:
        self.totals: dict[tuple[date, str], Decimal] = dict(start or {})
        self.adds: list[tuple[date, str, Decimal]] = []
        self.reads: list[tuple[date, str]] = []
        self.fail_writes = 0
        self.fail_reads = False
        self.lock = threading.Lock()

    def read_daily_spend(self, spend_date, provider):
        self.reads.append((spend_date, provider))
        if self.fail_reads:
            raise RuntimeError("db down")
        return self.totals.get((spend_date, provider), Decimal("0"))

    def add_daily_spend(self, spend_date, provider, cost_usd):
        with self.lock:
            if self.fail_writes:
                self.fail_writes -= 1
                raise RuntimeError("db down")
            self.adds.append((spend_date, provider, cost_usd))
            key = (spend_date, provider)
            self.totals[key] = self.totals.get(key, Decimal("0")) + cost_usd
            return self.totals[key]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for var in ("FOUNDRY_SPEND_ALERT_USD", "FOUNDRY_DAILY_SPEND_CAP_USD", "CLAUDE_SPEND_CACHE_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    claude_budget.configure_for_tests()
    foundry_spend.configure_for_tests()
    yield
    claude_budget.configure_for_tests()
    foundry_spend.configure_for_tests()


def _use(repo: FakeRepo, now=DAY1) -> None:
    clock = {"now": now}
    claude_budget.configure_for_tests(now_fn=lambda: clock["now"], repo_reader=repo, repo_writer=repo)
    foundry_spend.configure_for_tests(now_fn=lambda: clock["now"])
    return clock


def _by_date(buf) -> dict:
    out: dict = {}
    for (_repo, _alert, d), amount in buf.items():
        out[d] = out.get(d, Decimal("0")) + amount
    return out


def _alerts(caplog):
    return [r for r in caplog.records if getattr(r, "event_name", None) == foundry_spend.ALERT_EVENT_NAME]


# --- pricing ---------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected",
    [
        # 1M in + 1M out at the published per-million rates.
        ("DeepSeek-V4-Flash", Decimal("0.420000")),
        ("gpt-5.4-nano", Decimal("1.450000")),
        ("gpt-5.4-mini", Decimal("5.250000")),
    ],
)
def test_pricing_for_the_three_models(model, expected):
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert foundry_spend.usage_cost_usd(model, usage) == expected


def test_unpriced_model_uses_mini_rates_and_warns_once(caplog):
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        a = foundry_spend.usage_cost_usd("mystery-model", usage)
        b = foundry_spend.usage_cost_usd("mystery-model", usage)
    assert a == b == Decimal("5.250000")
    warnings = [r for r in caplog.records if "mystery-model" in r.getMessage()]
    assert len(warnings) == 1


def test_zero_usage_writes_nothing():
    repo = FakeRepo()
    _use(repo)
    foundry_spend.record_usage("gpt-5.4-mini", Usage())
    assert repo.adds == []
    assert foundry_spend.usage_cost_usd("gpt-5.4-mini", Usage()) is None


def test_record_usage_writes_foundry_provider():
    repo = FakeRepo()
    _use(repo)
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000, output_tokens=1000))
    assert repo.adds == [(DAY1.date(), "foundry", Decimal("0.005250"))]


# --- buffer / retry --------------------------------------------------------

def test_write_failure_buffers_and_retries_under_original_date_across_midnight():
    repo = FakeRepo()
    clock = _use(repo, DAY1)
    repo.fail_writes = 1
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000, output_tokens=1000))
    assert repo.adds == []
    assert _by_date(foundry_spend._unsaved) == {DAY1.date(): Decimal("0.005250")}
    clock["now"] = DAY2
    foundry_spend.record_usage("gpt-5.4-nano", Usage(input_tokens=1000, output_tokens=1000))
    assert sorted(repo.adds) == [
        (DAY1.date(), "foundry", Decimal("0.005250")),
        (DAY2.date(), "foundry", Decimal("0.001450")),
    ]
    assert foundry_spend._unsaved == {}
    assert foundry_spend._inflight == {}


def test_record_usage_never_raises_when_repo_or_pricing_explodes(monkeypatch):
    class Boom:
        def add_daily_spend(self, *a):
            raise RuntimeError("boom")

        def read_daily_spend(self, *a):
            raise RuntimeError("boom")

    monkeypatch.setattr(claude_budget, "get_spend_repo", lambda: Boom())
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=10))
    monkeypatch.setattr(foundry_spend, "usage_cost_usd", MagicMock(side_effect=ValueError("x")))
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=10))  # no raise


def test_record_usage_never_awaits_db_on_the_caller_thread():
    """With a real executor, a hung DB write does not block record_usage."""
    gate = threading.Event()

    class SlowRepo(FakeRepo):
        def add_daily_spend(self, *a):
            gate.wait(5)
            return super().add_daily_spend(*a)

    repo = SlowRepo()
    _use(repo)
    ex = ThreadPoolExecutor(max_workers=1)
    foundry_spend.configure_for_tests(now_fn=lambda: DAY1, executor=ex)
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))  # returns at once
    assert repo.adds == []
    gate.set()
    ex.shutdown(wait=True)
    assert len(repo.adds) == 1


def test_concurrent_record_usage_flushes_disjoint_amounts():
    repo = FakeRepo()
    _use(repo)
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="foundry-spend-test")
    foundry_spend.configure_for_tests(now_fn=lambda: DAY1, executor=ex)
    n_threads, per_thread = 8, 25
    usage = Usage(input_tokens=1000, output_tokens=0)  # 0.000750 each (mini)
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()
        for _ in range(per_thread):
            foundry_spend.record_usage("gpt-5.4-mini", usage)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ex.shutdown(wait=True)
    # Any final straggler (submitted after the last flush took its batch) is flushed above.
    expected = Decimal("0.000750") * n_threads * per_thread
    assert sum(a for _, _, a in repo.adds) == expected
    assert repo.totals[(DAY1.date(), "foundry")] == expected
    assert foundry_spend._unsaved == {} and foundry_spend._inflight == {}


def test_concurrent_direct_flushes_never_double_count():
    """Two flush threads racing over the same buffer persist disjoint amounts."""
    repo = FakeRepo()
    _use(repo)
    foundry_spend.configure_for_tests(now_fn=lambda: DAY1, executor=MagicMock())  # no auto flush
    for _ in range(50):
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))
    threads = [threading.Thread(target=foundry_spend._flush) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(a for _, _, a in repo.adds) == Decimal("0.000750") * 50


# --- threshold alert -------------------------------------------------------

def test_threshold_fires_exactly_once_crossing_from_below(caplog, monkeypatch):
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "0.01")
    repo = FakeRepo()
    _use(repo)
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        for _ in range(5):  # 0.00525 each: below, cross, above, above, above
            foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000, output_tokens=1000))
    alerts = _alerts(caplog)
    assert len(alerts) == 1
    assert alerts[0].threshold_usd == "0.01"
    assert alerts[0].total_usd == "0.010500"
    assert alerts[0].spend_date == "2026-10-28"
    assert "llm.spend.foundry_threshold_crossed" in alerts[0].getMessage()


def test_threshold_fires_when_one_call_jumps_past(caplog):
    repo = FakeRepo()  # default threshold 2.00
    _use(repo)
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))
    assert len(_alerts(caplog)) == 1


def test_threshold_fires_once_per_process_when_already_above(caplog, monkeypatch):
    """Another worker already pushed the day over: this worker still alerts,
    once, and again only after a restart (fresh process state)."""
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "1")
    repo = FakeRepo({(DAY1.date(), "foundry"): Decimal("5")})
    _use(repo)
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        for _ in range(4):
            foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))
    assert len(_alerts(caplog)) == 1
    caplog.clear()
    _use(repo)  # restart
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))
    assert len(_alerts(caplog)) == 1


class _InterleavedRepo(FakeRepo):
    """AddLlmDailySpend returns its total AFTER COMMIT: another worker's add
    commits between ours and our SELECT, so the total we see already includes
    it (prev = total - amount is >= threshold even though WE crossed it)."""

    def __init__(self, start, other):
        super().__init__(start)
        self.other = other

    def add_daily_spend(self, spend_date, provider, cost_usd):
        super().add_daily_spend(spend_date, provider, cost_usd)
        return super().add_daily_spend(spend_date, provider, self.other)


def test_missed_alert_interleaving_now_fires(caplog, monkeypatch):
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "1")
    # 0.5 + ours 0.75 (=1.25, crossed) + other 0.75 -> we see 2.00; prev = 1.25 >= 1.
    repo = _InterleavedRepo({(DAY1.date(), "foundry"): Decimal("0.5")}, Decimal("0.75"))
    _use(repo)
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000))
    alerts = _alerts(caplog)
    assert len(alerts) == 1 and alerts[0].total_usd == "2.000000"


def test_nothing_fires_at_zero(caplog, monkeypatch):
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "0")
    repo = FakeRepo()
    _use(repo)
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert _alerts(caplog) == []


def test_invalid_alert_setting_uses_default_and_warns_once(caplog, monkeypatch):
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "lots")
    from config import Settings

    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        assert foundry_spend.alert_threshold_usd(Settings()) == Decimal("2.00")
        assert foundry_spend.alert_threshold_usd(Settings()) == Decimal("2.00")
    assert len([r for r in caplog.records if "FOUNDRY_SPEND_ALERT_USD" in r.getMessage()]) == 1


def test_nothing_fires_when_isolated(caplog, monkeypatch):
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "0.01")
    claude_budget.configure_for_tests(now_fn=lambda: DAY1)
    claude_budget.use_isolated_spend_ledger(Decimal("100"))
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert _alerts(caplog) == []


# --- cap gate --------------------------------------------------------------

def test_cap_gate_off_by_default_never_reads():
    repo = FakeRepo({(DAY1.date(), "foundry"): Decimal("999")})
    _use(repo)
    assert asyncio.run(foundry_spend.pre_call_gate()) is None
    assert repo.reads == []


@pytest.mark.parametrize("raw", ["", "0", "  ", "nope", "-1"])
def test_cap_gate_off_for_empty_zero_invalid(monkeypatch, raw):
    monkeypatch.setenv("FOUNDRY_DAILY_SPEND_CAP_USD", raw)
    repo = FakeRepo({(DAY1.date(), "foundry"): Decimal("999")})
    _use(repo)
    assert asyncio.run(foundry_spend.pre_call_gate()) is None


def test_cap_gate_on_and_reached(monkeypatch):
    monkeypatch.setenv("FOUNDRY_DAILY_SPEND_CAP_USD", "1.00")
    repo = FakeRepo({(DAY1.date(), "foundry"): Decimal("0.998")})
    _use(repo)
    assert asyncio.run(foundry_spend.pre_call_gate()) is None
    assert repo.reads == [(DAY1.date(), "foundry")]
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000, output_tokens=1000))
    assert asyncio.run(foundry_spend.pre_call_gate()) == "daily_cap"


def test_cap_gate_counts_unsaved_spend(monkeypatch):
    monkeypatch.setenv("FOUNDRY_DAILY_SPEND_CAP_USD", "1.00")
    repo = FakeRepo()
    _use(repo)
    repo.fail_writes = 99
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000))
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000))
    assert asyncio.run(foundry_spend.pre_call_gate()) == "daily_cap"


def test_cap_gate_read_failure_fails_open(monkeypatch):
    monkeypatch.setenv("FOUNDRY_DAILY_SPEND_CAP_USD", "0.01")
    repo = FakeRepo()
    repo.fail_reads = True
    _use(repo)
    assert asyncio.run(foundry_spend.pre_call_gate()) is None


def test_cap_gate_disabled_when_isolated(monkeypatch):
    monkeypatch.setenv("FOUNDRY_DAILY_SPEND_CAP_USD", "0.01")
    claude_budget.configure_for_tests(now_fn=lambda: DAY1)
    claude_budget.use_isolated_spend_ledger(Decimal("100"))
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000))
    assert asyncio.run(foundry_spend.pre_call_gate()) is None


# --- isolation (U-702) -----------------------------------------------------

class _ExplodingRepo:
    def __init__(self, *a, **k):
        raise AssertionError("LlmSpendRepository must not be constructed in isolated mode")


def test_isolated_ledger_tracks_foundry_separately(monkeypatch):
    monkeypatch.setitem(sys.modules, "shared.llm_spend_repo", MagicMock(LlmSpendRepository=_ExplodingRepo))
    claude_budget.configure_for_tests(now_fn=lambda: DAY1)
    claude_budget.use_isolated_spend_ledger(Decimal("1.00"))
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert claude_budget.get_isolated_spend_total("foundry") == Decimal("5.250000")
    # Foundry never counts against the Claude run total or cap.
    assert claude_budget.get_isolated_spend_total() == Decimal("0")
    assert claude_budget.get_isolated_spend_total(claude_budget.ANTHROPIC_PROVIDER) == Decimal("0")
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) is None
    claude_budget.record_anthropic_spend(Decimal("0.40"))
    assert claude_budget.get_isolated_spend_total() == Decimal("0.40")
    assert claude_budget.get_isolated_spend_total("foundry") == Decimal("5.250000")
    assert asyncio.run(claude_budget.pre_call_gate(has_api_key=True)) is None


def test_foundry_resolves_repo_through_claude_budget(monkeypatch):
    calls = []
    repo = FakeRepo()
    monkeypatch.setattr(claude_budget, "get_spend_repo", lambda: calls.append(1) or repo)
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))
    assert calls and repo.adds


# --- round 2: ledger switches, repo binding, settings cache, daemon --------

class _Deferred:
    """Executor that queues flushes until run() (lets a test reorder events)."""

    def __init__(self):
        self.fns = []

    def submit(self, fn):
        self.fns.append(fn)

    def run(self):
        fns, self.fns = self.fns, []
        for fn in fns:
            fn()


def test_buffered_amounts_flush_to_the_repo_they_were_recorded_against(monkeypatch):
    live, other = FakeRepo(), FakeRepo()
    _use(live)
    ex = _Deferred()
    foundry_spend.configure_for_tests(now_fn=lambda: DAY1, executor=ex)
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))  # 0.00075 -> live
    monkeypatch.setattr(claude_budget, "get_spend_repo", lambda: other)
    foundry_spend.record_usage("gpt-5.4-nano", Usage(input_tokens=1000))  # 0.0002 -> other
    ex.run()
    assert [a for _, _, a in live.adds] == [Decimal("0.000750")]
    assert [a for _, _, a in other.adds] == [Decimal("0.000200")]


def test_live_to_isolated_switch_drops_live_buffer_and_never_mixes(monkeypatch):
    monkeypatch.setitem(sys.modules, "shared.llm_spend_repo", MagicMock(LlmSpendRepository=_ExplodingRepo))
    live = FakeRepo()
    _use(live)
    ex = _Deferred()
    foundry_spend.configure_for_tests(now_fn=lambda: DAY1, executor=ex)
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))  # queued for live
    claude_budget.use_isolated_spend_ledger(Decimal("10"))  # resets foundry state
    assert foundry_spend._unsaved == {}
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000))  # 0.75 isolated
    ex.run()
    assert live.adds == []
    assert claude_budget.get_isolated_spend_total("foundry") == Decimal("0.750000")


def test_isolated_to_live_switch_never_writes_isolated_batch_to_live(monkeypatch, caplog):
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "0.01")
    live = FakeRepo()
    _use(live)
    ex = _Deferred()
    foundry_spend.configure_for_tests(now_fn=lambda: DAY1, executor=ex)
    claude_budget.use_isolated_spend_ledger(Decimal("10"))
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000))  # queued, isolated
    claude_budget.clear_isolated_spend_ledger()  # resets foundry state
    assert foundry_spend._unsaved == {} and foundry_spend._alerted_dates == set()
    claude_budget.configure_for_tests(now_fn=lambda: DAY1, repo_reader=live, repo_writer=live)
    foundry_spend.record_usage("gpt-5.4-nano", Usage(input_tokens=1000))  # 0.0002 live
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        ex.run()
    assert [a for _, _, a in live.adds] == [Decimal("0.000200")]


def test_alert_eligibility_is_bound_at_record_time(monkeypatch, caplog):
    """An amount recorded while isolated never alerts, even if it flushes after
    isolation is (without a reset) no longer reported."""
    monkeypatch.setenv("FOUNDRY_SPEND_ALERT_USD", "0.01")
    claude_budget.configure_for_tests(now_fn=lambda: DAY1)
    ex = _Deferred()
    foundry_spend.configure_for_tests(now_fn=lambda: DAY1, executor=ex)
    claude_budget.use_isolated_spend_ledger(Decimal("10"))
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000))
    monkeypatch.setattr(foundry_spend, "_is_isolated", lambda: False)
    with caplog.at_level(logging.WARNING, logger=ALERT_LOGGER):
        ex.run()
    assert claude_budget.get_isolated_spend_total("foundry") == Decimal("0.750000")
    assert _alerts(caplog) == []


def test_reset_mid_write_does_not_requeue_the_failed_amount():
    class ResetThenFail(FakeRepo):
        def add_daily_spend(self, *a):
            foundry_spend.reset_process_state()
            raise RuntimeError("db down")

    _use(ResetThenFail())
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))
    assert foundry_spend._unsaved == {} and foundry_spend._inflight == {}


def test_settings_built_once_not_per_call(monkeypatch):
    from config import Settings as RealSettings

    built = []

    def counting():
        built.append(1)
        return RealSettings()

    monkeypatch.setattr(foundry_spend, "Settings", counting)
    _use(FakeRepo())
    for _ in range(20):
        asyncio.run(foundry_spend.pre_call_gate())
        foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert len(built) == 1


def test_settings_refresh_after_interval(monkeypatch):
    _use(FakeRepo())
    assert foundry_spend._settings().cap_usd is None
    monkeypatch.setenv("FOUNDRY_DAILY_SPEND_CAP_USD", "3")
    assert foundry_spend._settings().cap_usd is None  # cached
    monkeypatch.setattr(foundry_spend, "_SETTINGS_REFRESH_SECONDS", 0.0)
    assert foundry_spend._settings().cap_usd == Decimal("3")


def test_default_worker_is_a_daemon_thread():
    done = threading.Event()

    class Signal(FakeRepo):
        def add_daily_spend(self, *a):
            out = super().add_daily_spend(*a)
            done.set()
            return out

    repo = Signal()
    _use(repo)
    foundry_spend.configure_for_tests(now_fn=lambda: DAY1, executor=None)
    foundry_spend.record_usage("gpt-5.4-mini", Usage(input_tokens=1000))
    worker = foundry_spend._executor.thread
    assert worker.daemon is True and worker.name == "foundry-spend"
    assert done.wait(5)
    assert len(repo.adds) == 1
