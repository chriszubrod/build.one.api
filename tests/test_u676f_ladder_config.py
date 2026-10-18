"""U-676f: ladder_config parsing and invalid settings."""
from __future__ import annotations

import logging

import pytest

from intelligence.cascade.core import Rung
from intelligence.cascade import ladder_config
from intelligence.cascade.ladder_config import (
    DEFAULT_BILL_CODING_SUGGESTION_LADDER,
    _parse_ladder_spec,
    resolve_ladder,
    resolve_threshold,
)


@pytest.fixture(autouse=True)
def _clear_warned():
    ladder_config._warned_ladder.clear()
    ladder_config._warned_threshold.clear()
    yield


def test_parse_fallback_marker():
    ladder = _parse_ladder_spec("anthropic:claude-haiku-5-5,foundry!:DeepSeek-V4-Flash")
    assert ladder == (
        Rung("anthropic", "claude-haiku-5-5", fallback_only=False),
        Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True),
    )


def test_invalid_ladder_env_warning(monkeypatch, caplog):
    monkeypatch.setenv("CASCADE_LADDER_BILL_CODING_SUGGESTION", "bogus")
    with caplog.at_level(logging.WARNING):
        ladder = resolve_ladder("bill_coding_suggestion", DEFAULT_BILL_CODING_SUGGESTION_LADDER)
    assert ladder == DEFAULT_BILL_CODING_SUGGESTION_LADDER
    assert any("invalid CASCADE_LADDER" in r.message for r in caplog.records)


def test_invalid_threshold_warning(monkeypatch, caplog):
    monkeypatch.setenv("CASCADE_THRESHOLD_BILL_CODING_SUGGESTION", "2.0")
    with caplog.at_level(logging.WARNING):
        t = resolve_threshold("bill_coding_suggestion", 0.5)
    assert t == 0.5


def test_nan_threshold_uses_default(monkeypatch, caplog):
    monkeypatch.setenv("CASCADE_THRESHOLD_BILL_CODING_SUGGESTION", "nan")
    with caplog.at_level(logging.WARNING):
        t = resolve_threshold("bill_coding_suggestion", 0.4)
    assert t == 0.4


def test_invalid_ladder_warns_once_per_value(monkeypatch, caplog):
    monkeypatch.setenv("CASCADE_LADDER_BILL_CODING_SUGGESTION", "bogus")
    with caplog.at_level(logging.WARNING):
        resolve_ladder("bill_coding_suggestion", DEFAULT_BILL_CODING_SUGGESTION_LADDER)
        resolve_ladder("bill_coding_suggestion", DEFAULT_BILL_CODING_SUGGESTION_LADDER)
    assert sum("invalid CASCADE_LADDER" in r.message for r in caplog.records) == 1


def test_invalid_claude_cap_string_settings_and_effective_default(monkeypatch, caplog):
    from config import Settings
    from intelligence.cascade import claude_budget

    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "abc")
    Settings()
    with caplog.at_level(logging.WARNING):
        cap = claude_budget._daily_cap_usd(Settings())
    assert cap == claude_budget._DEFAULT_CAP_USD
    assert any("CLAUDE_DAILY_SPEND_CAP_USD" in r.message for r in caplog.records)


def test_negative_claude_cap_uses_default(monkeypatch, caplog):
    from datetime import datetime, timezone
    from decimal import Decimal

    from intelligence.cascade import claude_budget

    class FakeSpendRepo:
        def read_daily_spend(self, *a, **k):
            return Decimal("0")

        def add_daily_spend(self, *a, **k):
            return Decimal("0")

    monkeypatch.setenv("CLAUDE_DAILY_SPEND_CAP_USD", "-1")
    claude_budget.configure_for_tests(
        now_fn=lambda: datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc),
        repo_reader=FakeSpendRepo(),
        repo_writer=FakeSpendRepo(),
    )
    with caplog.at_level(logging.WARNING):
        assert not claude_budget.is_claude_day_stopped()
    assert any("CLAUDE_DAILY_SPEND_CAP_USD" in r.message for r in caplog.records)
