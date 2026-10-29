"""U-701c: Foundry fallback under the Claude daily cap - cascade core, header task, guards."""
from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

import pytest

from intelligence.cascade import bill_header_extraction as bhe
from intelligence.cascade.core import (
    DEFAULT_LADDER,
    Rung,
    StructuredTask,
    run_cascade,
    run_ladder,
)
from intelligence.cascade.ladder_config import (
    DEFAULT_BILL_CODING_LADDER_V2,
    DEFAULT_BILL_CODING_SUGGESTION_LADDER,
    DEFAULT_BILL_HEADER_EXTRACTION_LADDER,
    DEFAULT_BILL_HEADER_EXTRACTION_THRESHOLD,
)
from intelligence.transport.base import Done, TextDelta, TransportError, Usage

HAIKU = Rung("anthropic", "claude-haiku-5-5")
SONNET = Rung("anthropic", "claude-sonnet-5-5")
DEEPSEEK = Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True)
NANO = Rung("foundry", "gpt-5.4-nano", fallback_only=True)
MINI = Rung("foundry", "gpt-5.4-mini", fallback_only=True)
LADDER = (HAIKU, SONNET, DEEPSEEK, NANO, MINI)
CAP = "claude_unavailable:daily_cap"
TAU = 0.5


def _cap():
    return (None, None, Usage(), CAP)


def _valid(conf: float):
    return ({"label": "x", "confidence": conf}, conf, Usage(), None)


def _error(code: str = "http_500"):
    return (None, None, Usage(), code)


def _run(replies: dict[str, Any], *, opt_in: Optional[bool]):
    calls: list[str] = []

    async def execute(rung: Rung):
        calls.append(rung.model)
        return replies[rung.model]

    kwargs = {} if opt_in is None else {"fallback_on_daily_cap": opt_in}
    result = asyncio.run(run_ladder(
        LADDER, execute, lambda _r: (True, "ok"), TAU, "u701c_test", **kwargs,
    ))
    return result, calls


def _skipped(result) -> list[str]:
    return [a.rung.model for a in result.attempts if a.error == "skipped:fallback_only"]


# ---------------------------------------------------------------- run_ladder


def test_opt_in_deepseek_runs_and_is_accepted(caplog):
    caplog.set_level(logging.INFO, logger="intelligence.cascade.core")
    result, calls = _run(
        {HAIKU.model: _cap(), SONNET.model: _cap(), DEEPSEEK.model: _valid(0.9)}, opt_in=True,
    )
    assert calls == [HAIKU.model, SONNET.model, DEEPSEEK.model]
    assert result.accepted and result.winning_rung == DEEPSEEK
    assert result.stopped_reason is None
    assert any("fallback_on_daily_cap" in r.getMessage() for r in caplog.records)


def test_opt_in_deepseek_valid_below_tau_blocks_later_foundry():
    result, calls = _run(
        {HAIKU.model: _cap(), SONNET.model: _cap(), DEEPSEEK.model: _valid(0.3)}, opt_in=True,
    )
    assert calls == [HAIKU.model, SONNET.model, DEEPSEEK.model]
    assert _skipped(result) == [NANO.model, MINI.model]
    assert not result.accepted and result.stopped_reason == "daily_cap"
    assert result.winning_rung == DEEPSEEK and result.confidence == 0.3


def test_opt_in_deepseek_error_lets_nano_run():
    result, calls = _run(
        {HAIKU.model: _cap(), SONNET.model: _cap(), DEEPSEEK.model: _error(),
         NANO.model: _valid(0.8)},
        opt_in=True,
    )
    assert calls == [HAIKU.model, SONNET.model, DEEPSEEK.model, NANO.model]
    assert result.accepted and result.winning_rung == NANO and result.stopped_reason is None


def test_opt_in_valid_haiku_then_sonnet_cap_still_skips_foundry():
    result, calls = _run({HAIKU.model: _valid(0.2), SONNET.model: _cap()}, opt_in=True)
    assert calls == [HAIKU.model, SONNET.model]
    assert _skipped(result) == [DEEPSEEK.model, NANO.model, MINI.model]
    assert result.stopped_reason == "daily_cap" and result.winning_rung == HAIKU


@pytest.mark.parametrize("opt_in", [None, False, True])
def test_credit_token_reaches_foundry_regardless(opt_in):
    credit = (None, None, Usage(), "claude_unavailable:credit")
    result, calls = _run(
        {HAIKU.model: credit, SONNET.model: credit, DEEPSEEK.model: _valid(0.9)}, opt_in=opt_in,
    )
    assert calls == [HAIKU.model, SONNET.model, DEEPSEEK.model]
    assert result.accepted and result.stopped_reason is None


@pytest.mark.parametrize("opt_in", [None, False])
def test_default_daily_cap_skips_every_foundry_rung(opt_in):
    result, calls = _run({HAIKU.model: _cap(), SONNET.model: _cap()}, opt_in=opt_in)
    assert calls == [HAIKU.model, SONNET.model]
    assert _skipped(result) == [DEEPSEEK.model, NANO.model, MINI.model]
    assert result.stopped_reason == "daily_cap" and result.result is None


# ---------------------------------------------------------------- run_cascade / header task


class _Transport:
    """anthropic -> daily cap; foundry -> a valid header JSON at `conf`."""

    def __init__(self, provider: str, log: list[tuple[str, str]], conf: float):
        self.provider = provider
        self.log = log
        self.conf = conf

    async def stream(self, messages, *, model, **_kw):
        self.log.append((self.provider, model))
        if self.provider == "anthropic":
            yield TransportError(message="cap", code=CAP)
            return
        body = {
            "vendor_name": "Acme Supply", "bill_number": "INV-1", "bill_date": "2026-05-01",
            "total_amount": "100.00", "short_description": "Lumber",
            "long_description": None, "reference": None, "confidence": self.conf,
        }
        yield TextDelta(text=json.dumps(body))
        yield Done(usage=Usage(input_tokens=3, output_tokens=2))


def _transport_for(log, conf=0.9):
    return lambda provider: _Transport(provider, log, conf)


def test_run_cascade_honours_task_fallback_on_daily_cap():
    def task(flag: bool) -> StructuredTask:
        return StructuredTask(
            name="u701c", system_prompt="s", threshold=0.5,
            build_user_message=lambda p: "u", validate=lambda r: (True, "ok"),
            ladder=(HAIKU, DEEPSEEK), fallback_on_daily_cap=flag,
        )

    off_log: list = []
    off = asyncio.run(run_cascade(task(False), {}, transport_for=_transport_for(off_log)))
    on_log: list = []
    on = asyncio.run(run_cascade(task(True), {}, transport_for=_transport_for(on_log)))
    assert off_log == [("anthropic", HAIKU.model)] and off.stopped_reason == "daily_cap"
    assert on_log == [("anthropic", HAIKU.model), ("foundry", DEEPSEEK.model)]
    assert on.accepted and on.winning_rung == DEEPSEEK


@pytest.fixture
def default_header_ladder(monkeypatch):
    monkeypatch.setattr(bhe, "resolve_ladder", lambda _n, default: default)
    monkeypatch.setattr(bhe, "resolve_threshold", lambda _n, default: default)


@pytest.mark.parametrize("explicit_ladder", [False, True])
def test_header_extraction_default_never_requests_foundry_under_cap(
    default_header_ladder, explicit_ladder
):
    log: list = []
    kw = {"ladder": DEFAULT_BILL_HEADER_EXTRACTION_LADDER} if explicit_ladder else {}
    out = asyncio.run(bhe.run_bill_header_extraction(
        {"bill_text": "invoice"}, transport_for=_transport_for(log), **kw,
    ))
    assert {p for p, _m in log} == {"anthropic"}
    assert out.stopped_reason == "daily_cap" and out.result is None


@pytest.mark.parametrize("explicit_ladder", [False, True])
def test_header_extraction_kwarg_true_reaches_foundry(default_header_ladder, explicit_ladder):
    log: list = []
    kw = {"ladder": DEFAULT_BILL_HEADER_EXTRACTION_LADDER} if explicit_ladder else {}
    out = asyncio.run(bhe.run_bill_header_extraction(
        {"bill_text": "invoice"}, transport_for=_transport_for(log),
        fallback_on_daily_cap=True, **kw,
    ))
    assert log[-1] == ("foundry", "DeepSeek-V4-Flash")
    assert out.accepted and out.result["vendor_name"] == "Acme Supply"
    assert out.stopped_reason is None
    # The module task constant is never mutated.
    assert bhe.BILL_HEADER_EXTRACTION_TASK.fallback_on_daily_cap is False


# ---------------------------------------------------------------- guards

ROOT = Path(__file__).resolve().parent.parent
# Callers that pass fallback_on_daily_cap= : the two opt-in call sites plus the internal
# plumbing (decision wrapper, header task, cascade core).
ALLOWED_CALLERS = {
    "entities/bill/business/read_attachment_service.py",
    "entities/coding_suggestion/business/bill_text_suggestion_service.py",
    "entities/coding_suggestion/business/bill_coding_decision.py",
    "intelligence/cascade/bill_header_extraction.py",
    "intelligence/cascade/core.py",
}
SKIP_DIRS = {".venv", "tests", ".git", "node_modules", "__pycache__"}


def _callers_passing_kwarg() -> set[str]:
    found: set[str] = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if SKIP_DIRS & set(rel.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "fallback_on_daily_cap" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(
                kw.arg == "fallback_on_daily_cap" for kw in node.keywords
            ):
                found.add(rel.as_posix())
            # A **{"fallback_on_daily_cap": ...} spread counts as passing it too.
            if isinstance(node, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "fallback_on_daily_cap"
                for k in node.keys
            ):
                found.add(rel.as_posix())
    return found


def test_only_the_opt_in_sites_pass_fallback_on_daily_cap():
    found = _callers_passing_kwarg()
    assert found <= ALLOWED_CALLERS, found - ALLOWED_CALLERS
    assert "entities/bill/business/read_attachment_service.py" in found
    assert "entities/coding_suggestion/business/bill_text_suggestion_service.py" in found


def test_constants_unchanged():
    assert bhe.BILL_HEADER_EXTRACTION_TASK.fallback_on_daily_cap is False
    assert StructuredTask.__dataclass_fields__["fallback_on_daily_cap"].default is False
    assert DEFAULT_LADDER == (
        Rung("foundry", "DeepSeek-V4-Flash"),
        Rung("anthropic", "claude-haiku-5-5"),
        Rung("foundry", "gpt-5.4-nano"),
        Rung("foundry", "gpt-5.4-mini"),
        Rung("anthropic", "claude-sonnet-5-5"),
    )
    assert DEFAULT_BILL_HEADER_EXTRACTION_LADDER == LADDER
    assert DEFAULT_BILL_HEADER_EXTRACTION_THRESHOLD == 0.4
    assert DEFAULT_BILL_CODING_LADDER_V2 == (
        Rung("anthropic", "claude-haiku-5-5", accept_threshold=0.70),
        Rung("anthropic", "claude-sonnet-5-5", accept_threshold=0.0),
        DEEPSEEK, NANO, MINI,
    )
    assert DEFAULT_BILL_CODING_SUGGESTION_LADDER == (HAIKU, DEEPSEEK, NANO, MINI)
    from intelligence.cascade.bill_coding import (
        BILL_CODING_SUGGESTION_TASK,
        BILL_CODING_SUGGESTION_TASK_V2,
    )
    assert BILL_CODING_SUGGESTION_TASK.fallback_on_daily_cap is False
    assert BILL_CODING_SUGGESTION_TASK_V2.fallback_on_daily_cap is False


def test_config_setting_defaults_off():
    import config

    field = config.Settings.model_fields["bill_coding_foundry_on_claude_cap_enabled"]
    assert field.default is False


def test_attempt_was_skipped_matches_the_skip_marker():
    from intelligence.cascade.core import Attempt, attempt_was_skipped

    result, _calls = _run({HAIKU.model: _cap(), SONNET.model: _cap()}, opt_in=False)
    assert [attempt_was_skipped(a) for a in result.attempts] == [False, False, True, True, True]
    assert not attempt_was_skipped(Attempt(rung=HAIKU, accepted=False, error=None))
