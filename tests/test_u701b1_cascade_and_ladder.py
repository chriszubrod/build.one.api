"""U-701b1: per-rung τ, env ladder @suffix, drop/escalate helpers."""
from __future__ import annotations

import asyncio
import logging

import pytest

from intelligence.cascade import bill_coding, ladder_config
from intelligence.cascade.core import Rung, run_ladder
from intelligence.cascade.ladder_config import (
    DEFAULT_BILL_CODING_LADDER_V2,
    DEFAULT_BILL_CODING_SUGGESTION_LADDER,
    DEFAULT_BILL_HEADER_EXTRACTION_LADDER,
    _parse_ladder_spec,
    drop_ladder,
    escalate_ladder,
    resolve_ladder,
)
from intelligence.transport.base import Usage

HAIKU = Rung("anthropic", "claude-haiku-5-5", accept_threshold=0.70)
SONNET = Rung("anthropic", "claude-sonnet-5-5", accept_threshold=0.0)
DEEP = Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True)

PAYLOAD = {
    "candidate_sccs": [{"number": "43.10", "name": "Framing"}],
    "candidate_projects": None,
}


@pytest.fixture(autouse=True)
def _clear_ladder_warned():
    ladder_config._warned_ladder.clear()
    ladder_config._warned_threshold.clear()
    yield


def _run(rungs, outcomes, *, threshold=0.5):
    calls: list[Rung] = []

    async def execute(rung):
        calls.append(rung)
        return outcomes[rung]

    def validate(parsed):
        return bill_coding.validate_bill_coding_result(parsed, payload=PAYLOAD)

    result = asyncio.run(
        run_ladder(rungs, execute, validate, threshold, "u701b1"),
    )
    return result, calls


def _ok(conf: float):
    return ({"scc_number": "43.10", "confidence": conf}, conf, Usage(), None)


def _invalid(conf: float):
    return ({"scc_number": "99.99", "confidence": conf}, conf, Usage(), None)


def _err():
    return (None, None, Usage(), "HTTP 500")


def test_haiku_069_escalates_to_sonnet():
    result, calls = _run((HAIKU, SONNET), {HAIKU: _ok(0.69), SONNET: _ok(0.9)})
    assert calls == [HAIKU, SONNET]
    assert result.accepted
    assert result.winning_rung == SONNET


def test_haiku_exactly_070_accepted_sonnet_not_called():
    result, calls = _run((HAIKU, SONNET), {HAIKU: _ok(0.70)})
    assert calls == [HAIKU]
    assert result.accepted
    assert result.winning_rung == HAIKU


def test_haiku_valid_low_sonnet_error_skips_foundry_needs_human():
    result, calls = _run(
        (HAIKU, SONNET, DEEP),
        {HAIKU: _ok(0.4), SONNET: _err()},
        threshold=0.5,
    )
    assert calls == [HAIKU, SONNET]
    assert not result.accepted
    assert result.needs_human
    assert result.winning_rung == HAIKU
    assert result.confidence == 0.4
    assert len(result.attempts) == 3
    assert result.attempts[2].error == "skipped:fallback_only"


def test_haiku_invalid_sonnet_invalid_foundry_accepted_at_task_threshold():
    result, calls = _run(
        (HAIKU, SONNET, DEEP),
        {HAIKU: _invalid(0.9), SONNET: _invalid(0.9), DEEP: _ok(0.55)},
        threshold=0.5,
    )
    assert calls == [HAIKU, SONNET, DEEP]
    assert result.accepted
    assert result.winning_rung == DEEP


def test_sonnet_valid_at_01_accepted_with_zero_rung_threshold():
    result, calls = _run((HAIKU, SONNET), {HAIKU: _ok(0.69), SONNET: _ok(0.1)})
    assert result.accepted
    assert result.winning_rung == SONNET
    assert result.confidence == 0.1


def test_none_accept_threshold_matches_task_threshold_on_default_ladders():
    """Outcomes with default ladders must match when accept_threshold is omitted vs explicit None."""
    ladder_explicit = DEFAULT_BILL_CODING_SUGGESTION_LADDER
    ladder_rebuilt = tuple(
        Rung(r.provider, r.model, r.max_tokens, r.fallback_only, None)
        for r in DEFAULT_BILL_CODING_SUGGESTION_LADDER
    )
    assert ladder_explicit == ladder_rebuilt

    threshold = 0.5
    haiku_only = (Rung("anthropic", "claude-haiku-5-5"),)
    low = _ok(0.49)
    high = _ok(0.51)

    for outcomes, expect_accept in (({haiku_only[0]: low}, False), ({haiku_only[0]: high}, True)):
        r1, _ = _run(haiku_only, outcomes, threshold=threshold)
        r2, _ = _run(ladder_rebuilt[:1], outcomes, threshold=threshold)
        assert r1.accepted == r2.accepted == expect_accept
        assert r1.needs_human == r2.needs_human

    header = DEFAULT_BILL_HEADER_EXTRACTION_LADDER[:2]
    header_rebuilt = tuple(
        Rung(r.provider, r.model, r.max_tokens, r.fallback_only, None)
        for r in header
    )

    def validate_header(parsed):
        return (parsed.get("ok") is True, "ok")

    async def run_header(rungs, outcomes):
        calls: list[Rung] = []

        async def execute(rung):
            calls.append(rung)
            return outcomes[rung]

        return await run_ladder(
            rungs, execute, validate_header, 0.4, "header",
        ), calls

    outcomes = {
        header[0]: ({"ok": True, "confidence": 0.35}, 0.35, Usage(), None),
        header[1]: ({"ok": True, "confidence": 0.9}, 0.9, Usage(), None),
    }
    r1 = asyncio.run(run_header(header, outcomes))[0]
    r2 = asyncio.run(run_header(header_rebuilt, outcomes))[0]
    assert r1.accepted == r2.accepted
    assert r1.winning_rung == r2.winning_rung


def test_parse_ladder_at_suffix_valid_and_mixed():
    ladder = _parse_ladder_spec(
        "anthropic:claude-haiku-5-5@0.70,anthropic:claude-sonnet-5-5@0,"
        "foundry!:DeepSeek-V4-Flash",
    )
    assert ladder == (
        Rung("anthropic", "claude-haiku-5-5", accept_threshold=0.70),
        Rung("anthropic", "claude-sonnet-5-5", accept_threshold=0.0),
        Rung("foundry", "DeepSeek-V4-Flash", fallback_only=True),
    )


@pytest.mark.parametrize(
    "spec",
    [
        "anthropic:claude-haiku-5-5@1.01",
        "anthropic:claude-haiku-5-5@nan",
        "anthropic:claude-haiku-5-5@foo",
        "anthropic:claude-haiku-5-5@",
        "anthropic:claude-haiku-5-5@,foundry!:DeepSeek-V4-Flash",
    ],
)
def test_parse_ladder_invalid_at_invalidates_whole_spec(spec):
    assert _parse_ladder_spec(spec) is None


def test_parse_ladder_missing_suffix_unchanged():
    ladder = _parse_ladder_spec("anthropic:claude-haiku-5-5,foundry!:DeepSeek-V4-Flash")
    assert ladder[0].accept_threshold is None


def test_resolve_ladder_env_logs_and_parses(monkeypatch, caplog):
    monkeypatch.setenv(
        "CASCADE_LADDER_BILL_CODING_SUGGESTION",
        "anthropic:claude-haiku-5-5@0.70,foundry!:DeepSeek-V4-Flash",
    )
    with caplog.at_level(logging.INFO):
        ladder = resolve_ladder(
            "bill_coding_suggestion", DEFAULT_BILL_CODING_SUGGESTION_LADDER,
        )
    assert ladder[0].accept_threshold == 0.70
    assert any("CASCADE_LADDER_BILL_CODING_SUGGESTION resolved" in r.message for r in caplog.records)


def test_resolve_ladder_invalid_at_uses_default(monkeypatch, caplog):
    monkeypatch.setenv(
        "CASCADE_LADDER_BILL_CODING_SUGGESTION",
        "anthropic:claude-haiku-5-5@2.0",
    )
    with caplog.at_level(logging.WARNING):
        ladder = resolve_ladder(
            "bill_coding_suggestion", DEFAULT_BILL_CODING_SUGGESTION_LADDER,
        )
    assert ladder == DEFAULT_BILL_CODING_SUGGESTION_LADDER
    assert any("invalid CASCADE_LADDER" in r.message for r in caplog.records)


def test_drop_and_escalate_ladder_v2():
    assert drop_ladder(DEFAULT_BILL_CODING_LADDER_V2) == (
        DEFAULT_BILL_CODING_LADDER_V2[0],
        *DEFAULT_BILL_CODING_LADDER_V2[2:],
    )
    assert escalate_ladder(DEFAULT_BILL_CODING_LADDER_V2) == (
        DEFAULT_BILL_CODING_LADDER_V2[1],
    )


def test_drop_escalate_edge_ladders():
    single = (Rung("anthropic", "claude-haiku-5-5"),)
    assert drop_ladder(single) == single
    assert escalate_ladder(single) == ()

    no_fallback = (
        Rung("anthropic", "claude-haiku-5-5"),
        Rung("anthropic", "claude-sonnet-5-5"),
    )
    assert drop_ladder(no_fallback) == (no_fallback[0],)
    assert escalate_ladder(no_fallback) == (no_fallback[1],)

    only_fallback = (DEEP,)
    assert drop_ladder(only_fallback) == only_fallback
    assert escalate_ladder(only_fallback) == ()


def test_resolve_ladder_logs_resolved_ladder_once(monkeypatch, caplog):
    import intelligence.cascade.ladder_config as lc

    monkeypatch.setattr(lc, "_logged_ladder", set())
    monkeypatch.setenv(
        "CASCADE_LADDER_BILL_CODING_SUGGESTION",
        "anthropic:claude-haiku-5-5@0.70,foundry!:DeepSeek-V4-Flash",
    )
    with caplog.at_level(logging.INFO):
        for _ in range(3):
            resolve_ladder("bill_coding_suggestion", DEFAULT_BILL_CODING_SUGGESTION_LADDER)
    hits = [r for r in caplog.records if "CASCADE_LADDER_BILL_CODING_SUGGESTION resolved" in r.message]
    assert len(hits) == 1
