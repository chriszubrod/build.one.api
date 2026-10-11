"""U-649 — cascade rung outcomes reach App Insights as structured events.

Every run_ladder decision and every CascadeTransport rung decision emits one
INFO record whose `extra` fields become customDimensions. The cascade loggers
are raised to INFO in prod by export_info_loggers(), driven by the registry in
shared/observability.py; the root stays WARNING.
"""

import asyncio
import json
import logging
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from intelligence.cascade.core import Attempt, CascadeResult, Rung, run_ladder
from intelligence.observability import cascade_telemetry
from intelligence.observability.cascade_telemetry import (
    LADDER_EVENT,
    TRANSPORT_EVENT,
    ladder_event_fields,
)
from intelligence.observability.pricing import compute_cost_usd
from intelligence.transport.base import Done, TextDelta, TransportError, Usage
from intelligence.transport.cascade import CascadeTransport
from shared.observability import EXPORTED_INFO_LOGGERS, export_info_loggers
from tests.loop_test_helpers import ScriptedTransport, alist

R1 = Rung("foundry", "DeepSeek-V4-Flash")
R2 = Rung("anthropic", "claude-haiku-5-5")
UNPRICED = Rung("anthropic", "made-up-model-9")

_LOGGED_NAMES = (*EXPORTED_INFO_LOGGERS, "intelligence.cascade.core")
_STANDARD_RECORD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


@pytest.fixture
def restore_levels():
    saved = {name: logging.getLogger(name).level for name in _LOGGED_NAMES}
    yield
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture
def cascade_logs(caplog, restore_levels):
    export_info_loggers()
    yield caplog


def _executor(outcomes):
    async def execute(rung):
        return outcomes[rung.model]
    return execute


def _sequenced_executor(outcomes):
    """Return each outcome in turn, regardless of rung (for repeated equal rungs)."""
    remaining = iter(outcomes)

    async def execute(rung):
        return next(remaining)
    return execute


def _run_ladder(rungs, outcomes, validate=lambda parsed: (True, "ok"), threshold=0.8):
    return asyncio.run(run_ladder(rungs, _executor(outcomes), validate, threshold, "test_task"))


def _ladder_records(caplog):
    return [r for r in caplog.records if r.getMessage() == LADDER_EVENT]


def _transport_records(caplog):
    return [r for r in caplog.records if r.getMessage() == TRANSPORT_EVENT]


def _extras(record):
    return {k: v for k, v in vars(record).items() if k not in _STANDARD_RECORD_ATTRS}


def test_ladder_fallback_then_accept_emits_one_record(cascade_logs):
    outcomes = {
        R1.model: (None, None, Usage(input_tokens=100, output_tokens=5), "upstream: boom"),
        R2.model: ({"label": "x", "confidence": 0.9}, 0.9, Usage(input_tokens=200, output_tokens=40), None),
    }

    result = _run_ladder((R1, R2), outcomes)

    assert result.accepted
    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    rec = records[0]
    assert rec.accepted is True
    assert rec.needs_human is False
    assert rec.winning_index == 2
    assert rec.winning_provider == R2.provider
    assert rec.winning_model == R2.model
    assert rec.attempt_count == 2
    assert rec.input_tokens == 300
    assert rec.output_tokens == 45

    attempts = json.loads(rec.attempts_json)
    assert len(attempts) == 2
    assert attempts[0]["model"] == R1.model
    assert attempts[0]["accepted"] is False
    assert attempts[0]["error"] == "upstream: boom"
    assert attempts[1]["model"] == R2.model
    assert attempts[1]["accepted"] is True
    assert attempts[1]["validated"] is True
    assert attempts[1]["confidence"] == 0.9
    assert attempts[1]["error"] is None

    expected_cost = sum(
        compute_cost_usd(provider=r.provider, model=r.model, usage=u)
        for r, u in [
            (R1, Usage(input_tokens=100, output_tokens=5)),
            (R2, Usage(input_tokens=200, output_tokens=40)),
        ]
    )
    assert rec.cost_usd == pytest.approx(round(expected_cost, 6))


def test_exhausted_ladder_emits_one_needs_human_record(cascade_logs):
    outcomes = {
        R1.model: ({"label": "x", "confidence": 0.3}, 0.3, Usage(), None),
        R2.model: ({"label": "x", "confidence": 0.4}, 0.4, Usage(), None),
    }

    result = _run_ladder((R1, R2), outcomes, validate=lambda parsed: (False, "bad label"))

    assert not result.accepted
    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    assert records[0].accepted is False
    assert records[0].needs_human is True
    assert not hasattr(records[0], "accepted_via")
    assert records[0].attempt_count == 2


def test_cost_is_omitted_when_any_attempt_model_is_unpriced(cascade_logs):
    outcomes = {
        UNPRICED.model: ({"label": "x", "confidence": 0.1}, 0.1, Usage(input_tokens=50, output_tokens=5), None),
        R2.model: ({"label": "x", "confidence": 0.95}, 0.95, Usage(input_tokens=60, output_tokens=6), None),
    }

    _run_ladder((UNPRICED, R2), outcomes)

    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    assert not hasattr(records[0], "cost_usd")


def test_winning_index_for_an_equal_but_separately_built_rung(cascade_logs):
    twin = Rung(R2.provider, R2.model)  # equal to R2, not the same object
    execute = _sequenced_executor([({"ok": True}, 0.95, Usage(), None)])

    outcome = asyncio.run(run_ladder((twin,), execute, lambda p: (p["ok"], "r"), 0.8, "t"))

    assert outcome.winning_rung == R2
    assert outcome.winning_index == 1
    assert _ladder_records(cascade_logs)[0].winning_index == 1


# Repeated equal rungs. "shared" = both attempts return ONE parsed dict object,
# which defeats any rung-equality or result-identity inference; the index must
# come from run_ladder itself. The (0.7, 0.3) row guards "always the last rung".
_SHARED = {"ok": True, "label": "x"}


@pytest.mark.parametrize(
    "parsed, confidences, threshold, accepted, expected",
    [
        (({"ok": False, "label": "x"}, {"ok": True, "label": "x"}), (0.5, 0.95), 0.8, True, 2),
        ((_SHARED, _SHARED), (0.5, 0.95), 0.8, True, 2),
        ((_SHARED, _SHARED), (0.3, 0.7), 0.9, False, 2),
        ((_SHARED, _SHARED), (0.7, 0.3), 0.9, False, 1),
    ],
    ids=["distinct_dicts_accept", "shared_accept", "shared_exhausted_last_best", "shared_exhausted_first_best"],
)
def test_winning_index_for_repeated_equal_rungs(cascade_logs, parsed, confidences, threshold, accepted, expected):
    execute = _sequenced_executor([(p, c, Usage(), None) for p, c in zip(parsed, confidences)])

    outcome = asyncio.run(run_ladder((R2, R2), execute, lambda p: (p["ok"], "r"), threshold, "t"))

    assert outcome.accepted is accepted
    assert outcome.needs_human is (not accepted)
    assert outcome.winning_index == expected
    assert _ladder_records(cascade_logs)[0].winning_index == expected


def test_wrapped_ladder_lines_keep_the_callers_source_location(cascade_logs):
    shared = {"ok": True, "label": "x"}
    rung_outcomes = iter([(shared, 0.3, Usage(), None)])

    async def execute(rung):
        return next(rung_outcomes)

    asyncio.run(run_ladder((R2,), execute, lambda p: (p["ok"], "r"), 0.9, "t"))

    human = [r for r in cascade_logs.records if r.getMessage().startswith("cascade[")]
    assert human, "expected the human-readable rung and EXHAUSTED lines"
    assert len(_ladder_records(cascade_logs)) == 1
    for record in human + _ladder_records(cascade_logs):
        assert record.funcName == "run_ladder", record.funcName
        assert record.pathname.endswith("cascade/core.py"), record.pathname


def test_cost_is_absent_not_zero_when_pricing_raises_for_one_attempt(cascade_logs, monkeypatch):
    real_cost = cascade_telemetry.compute_cost_usd

    def pricing_for_r2_only(*, provider, model, usage):
        if model == R1.model:
            raise RuntimeError("pricing exploded")
        return real_cost(provider=provider, model=model, usage=usage)

    monkeypatch.setattr(cascade_telemetry, "compute_cost_usd", pricing_for_r2_only)
    outcomes = {
        R1.model: (None, None, Usage(input_tokens=10, output_tokens=1), "upstream: boom"),
        R2.model: ({"label": "x", "confidence": 0.9}, 0.9, Usage(input_tokens=20, output_tokens=2), None),
    }

    _run_ladder((R1, R2), outcomes)

    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    assert not hasattr(records[0], "cost_usd")


class _RaisingSink(logging.Handler):
    def emit(self, record):
        raise RuntimeError("log sink exploded")


@pytest.fixture
def raising_log_sink(cascade_logs):
    sink = _RaisingSink()
    loggers = [logging.getLogger(name) for name in EXPORTED_INFO_LOGGERS]
    for logger in loggers:
        logger.addHandler(sink)
    yield
    for logger in loggers:
        logger.removeHandler(sink)


class _HostileResult:
    @property
    def accepted(self):
        raise RuntimeError("accepted exploded")

    @property
    def needs_human(self):
        raise RuntimeError("needs_human exploded")

    @property
    def attempts(self):
        raise RuntimeError("attempts exploded")


@pytest.mark.parametrize("path", ["accepted", "exhausted"])
def test_run_ladder_returns_normally_when_the_log_sink_raises(raising_log_sink, path):
    rungs = (R1, R2) if path == "accepted" else (R1,)
    outcomes = {
        R1.model: ({"ok": False}, 0.1, Usage(), None),
        R2.model: ({"ok": True}, 0.95, Usage(), None),
    }

    result = _run_ladder(rungs, outcomes, validate=lambda p: (p["ok"], "r"))

    assert result.accepted is (path == "accepted")
    assert result.needs_human is (path == "exhausted")


@pytest.mark.parametrize("sink", ["normal", "raising"])
@pytest.mark.parametrize("case", ["first_rung", "fallback_then_accept", "all_fail"])
def test_transport_yields_full_sequence(request, cascade_logs, sink, case):
    if sink == "raising":
        request.getfixturevalue("raising_log_sink")
    done = Done(usage=Usage())
    rate_limited = TransportError(message="rate limited", code="rate_limited")
    overloaded = TransportError(message="overloaded", code="overloaded")
    if case == "first_rung":
        cascade = _cascade_transport((R2,), {R2.provider: [TextDelta(text="hi"), done]})
        expected = [TextDelta(text="hi"), done]
    elif case == "fallback_then_accept":
        cascade = _cascade_transport(
            (R1, R2), {R1.provider: [rate_limited], R2.provider: [TextDelta(text="hi"), done]},
        )
        expected = [TextDelta(text="hi"), done]
    else:
        cascade = _cascade_transport((R1, R2), {R1.provider: [rate_limited], R2.provider: [overloaded]})
        expected = [overloaded]

    assert _drive_transport(cascade) == expected


def test_log_ladder_outcome_never_raises_on_a_hostile_result(cascade_logs):
    cascade_telemetry.log_ladder_outcome(logging.getLogger("intelligence.cascade.core"), "t", _HostileResult())

    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    assert records[0].telemetry_error == "RuntimeError"


BAD_OUTPUTS = {
    "usage_none": ({"ok": False}, 0.1, None, None),
    "confidence_decimal": ({"ok": False}, Decimal("0.9"), Usage(), None),
    "error_exception": (None, None, Usage(), ValueError("boom")),
    "cost_raises": ({"ok": False}, 0.1, Usage(input_tokens=1), None),
}


@pytest.mark.parametrize("path", ["accepted", "exhausted"])
@pytest.mark.parametrize("case", list(BAD_OUTPUTS))
def test_malformed_attempt_never_breaks_the_cascade(cascade_logs, monkeypatch, case, path):
    if case == "cost_raises":
        def boom(**_kwargs):
            raise RuntimeError("pricing exploded")
        monkeypatch.setattr(cascade_telemetry, "compute_cost_usd", boom)
    rungs = (R1, R2) if path == "accepted" else (R1,)
    outcomes = {
        R1.model: BAD_OUTPUTS[case],
        R2.model: ({"ok": True}, 0.95, Usage(), None),
    }

    result = _run_ladder(rungs, outcomes, validate=lambda p: (p["ok"], "r"))

    assert result.accepted is (path == "accepted")
    assert result.needs_human is (path == "exhausted")
    assert len(_ladder_records(cascade_logs)) == 1


def test_enrichment_failure_falls_back_to_minimal_event(cascade_logs, monkeypatch):
    def boom(name, result):
        raise RuntimeError("enrichment exploded")
    monkeypatch.setattr(cascade_telemetry, "ladder_event_fields", boom)
    outcomes = {R2.model: ({"ok": True}, 0.9, Usage(), None)}

    result = _run_ladder((R2,), outcomes, validate=lambda p: (p["ok"], "r"))

    assert result.accepted
    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    assert _extras(records[0]) == {
        "event_name": LADDER_EVENT,
        "task": "test_task",
        "accepted": True,
        "needs_human": False,
        "telemetry_error": "RuntimeError",
    }


@pytest.mark.parametrize("accepted", [True, False])
def test_rung_none_attempt_is_logged_without_raising(cascade_logs, accepted):
    attempt = Attempt(rung=None, accepted=accepted, confidence=0.9, validated=True, result={"ok": True}, usage=Usage())
    outcome = CascadeResult(
        accepted=accepted, needs_human=not accepted, result={"ok": True}, confidence=0.9,
        winning_rung=None, attempts=[attempt], accepted_via="threshold" if accepted else None,
    )

    cascade_telemetry.log_ladder_outcome(logging.getLogger("intelligence.cascade.core"), "t", outcome)

    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    assert _extras(records[0])["attempt_count"] == 1
    assert "cost_usd" not in _extras(records[0])


@pytest.mark.parametrize("accepted", [True, False])
def test_disabled_info_skips_enrichment(restore_levels, monkeypatch, caplog, accepted):
    real_fields = cascade_telemetry.ladder_event_fields
    enrichment_calls = []

    def counting(name, result):
        enrichment_calls.append(name)
        return real_fields(name, result)

    monkeypatch.setattr(cascade_telemetry, "ladder_event_fields", counting)
    for name in _LOGGED_NAMES:
        logging.getLogger(name).setLevel(logging.WARNING)
    outcomes = {R1.model: ({"ok": accepted}, 0.95, Usage(), None)}

    result = _run_ladder((R1,), outcomes, validate=lambda p: (p["ok"], "r"))

    assert result.accepted is accepted
    assert enrichment_calls == []
    assert _ladder_records(caplog) == []


def test_ladder_event_extras_are_flat_scalars(cascade_logs):
    outcomes = {
        R1.model: (None, None, Usage(input_tokens=10, output_tokens=1), "upstream: boom"),
        R2.model: ({"label": "x", "confidence": 0.9}, 0.9, Usage(input_tokens=20, output_tokens=2), None),
    }
    _run_ladder((R1, R2), outcomes)

    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    extras = _extras(records[0])
    assert extras["event_name"] == LADDER_EVENT
    for key, value in extras.items():
        assert value is not None, key
        assert isinstance(value, (str, int, float, bool)), (key, type(value))


def test_ladder_event_fields_match_the_documented_schema():
    outcomes = {R2.model: ({"label": "x", "confidence": 0.9}, 0.9, Usage(), None)}
    result = asyncio.run(run_ladder((R2,), _executor(outcomes), lambda p: (True, "ok"), 0.8, "t"))

    fields = ladder_event_fields("t", result)

    assert set(fields) == {
        "event_name", "task", "accepted", "needs_human", "accepted_via",
        "winning_provider", "winning_model", "winning_index", "attempt_count",
        "latency_ms_total", "input_tokens", "output_tokens", "cost_usd", "attempts_json",
    }
    assert fields["task"] == "t"
    assert fields["winning_index"] == 1


def _cascade_transport(ladder, scripts):
    transports = {provider: ScriptedTransport([script]) for provider, script in scripts.items()}
    return CascadeTransport(ladder=ladder, transport_for=transports.__getitem__)


def _drive_transport(cascade):
    return asyncio.run(alist(cascade.stream([], model="ignored")))


def test_transport_fallback_then_accept_emits_fallback_and_accepted(cascade_logs):
    cascade = _cascade_transport(
        (R1, R2),
        {
            R1.provider: [TransportError(message="rate limited", code="rate_limited")],
            R2.provider: [TextDelta(text="hi"), Done(usage=Usage())],
        },
    )

    _drive_transport(cascade)

    records = _transport_records(cascade_logs)
    assert [(r.outcome, r.rung_index) for r in records] == [("fallback", 1), ("accepted", 2)]
    fallback, accepted = records
    assert fallback.provider == R1.provider
    assert fallback.model == R1.model
    assert fallback.error_code == "rate_limited"
    assert accepted.provider == R2.provider
    assert accepted.model == R2.model
    assert accepted.fallbacks == 1


def test_transport_lines_and_events_keep_the_callers_source_location(cascade_logs):
    cascade = _cascade_transport(
        (R1, R2),
        {
            R1.provider: [TransportError(message="rate limited", code="rate_limited")],
            R2.provider: [TextDelta(text="hi"), Done(usage=Usage())],
        },
    )

    _drive_transport(cascade)

    records = [r for r in cascade_logs.records if r.name == "intelligence.transport.cascade"]
    assert len(records) >= 4, [r.getMessage() for r in records]  # 2 human lines + 2 structured events
    for record in records:
        assert record.funcName == "stream", (record.getMessage(), record.funcName)
        assert record.pathname.endswith("transport/cascade.py"), record.pathname


def test_transport_single_rung_success_emits_accepted_with_zero_fallbacks(cascade_logs):
    cascade = _cascade_transport(
        (R2,),
        {R2.provider: [TextDelta(text="hi"), Done(usage=Usage())]},
    )

    _drive_transport(cascade)

    records = _transport_records(cascade_logs)
    assert len(records) == 1
    assert records[0].outcome == "accepted"
    assert records[0].rung_index == 1
    assert records[0].fallbacks == 0


def test_transport_all_rungs_fail_emits_exhausted(cascade_logs):
    cascade = _cascade_transport(
        (R1, R2),
        {
            R1.provider: [TransportError(message="rate limited", code="rate_limited")],
            R2.provider: [TransportError(message="overloaded", code="overloaded")],
        },
    )

    _drive_transport(cascade)

    records = _transport_records(cascade_logs)
    outcomes = [r.outcome for r in records]
    assert outcomes == ["fallback", "fallback", "exhausted"]
    exhausted = records[-1]
    assert exhausted.rung_count == 2
    assert exhausted.error_code == "overloaded"


def test_transport_extras_are_flat_and_omit_missing_error_code(cascade_logs):
    accepted_after_fallback = _cascade_transport(
        (R1, R2),
        {
            R1.provider: [TransportError(message="rate limited", code="rate_limited")],
            R2.provider: [TextDelta(text="hi"), Done(usage=Usage())],
        },
    )
    codeless_failure = _cascade_transport(
        (R1,),
        {R1.provider: [TransportError(message="down")]},
    )
    _drive_transport(accepted_after_fallback)
    _drive_transport(codeless_failure)

    records = _transport_records(cascade_logs)
    assert [r.outcome for r in records] == ["fallback", "accepted", "fallback", "exhausted"]
    for record in records:
        extras = _extras(record)
        assert extras["event_name"] == TRANSPORT_EVENT
        for key, value in extras.items():
            assert value is not None, key
            assert isinstance(value, (str, int, float, bool)), (key, type(value))
    assert not hasattr(records[2], "error_code")
    assert not hasattr(records[3], "error_code")


_ISOLATION_PROBE = """
import json, logging
from shared.observability import EXPORTED_INFO_LOGGERS, export_info_loggers

def real_loggers():
    return {n for n, lg in logging.root.manager.loggerDict.items() if isinstance(lg, logging.Logger)}

before = real_loggers()
export_info_loggers()
created = sorted(real_loggers() - before - set(EXPORTED_INFO_LOGGERS))
print(json.dumps({n: logging.root.manager.loggerDict[n].level for n in created}))
"""


def test_export_leaves_every_logger_it_creates_at_notset():
    repo_root = Path(__file__).resolve().parent.parent
    completed = subprocess.run(
        [sys.executable, "-c", _ISOLATION_PROBE], cwd=repo_root, capture_output=True, text=True, check=True,
    )

    created_levels = json.loads(completed.stdout)
    assert {name: level for name, level in created_levels.items() if level != logging.NOTSET} == {}


def test_latency_total_is_the_sum_of_attempt_latencies():
    parsed = {"label": "x", "confidence": 0.9}
    attempts = [
        Attempt(rung=R1, accepted=False, latency_ms=120, usage=Usage(), error="upstream: boom"),
        Attempt(rung=R2, accepted=True, latency_ms=45, confidence=0.9, validated=True, result=parsed, usage=Usage()),
    ]
    outcome = CascadeResult(
        accepted=True, needs_human=False, result=parsed, confidence=0.9,
        winning_rung=R2, attempts=attempts, accepted_via="threshold",
    )

    assert ladder_event_fields("t", outcome)["latency_ms_total"] == 165


def test_exhausted_winning_index_is_the_best_validated_attempt(cascade_logs):
    outcomes = {
        R1.model: ({"valid": True, "label": "x", "confidence": 0.3}, 0.3, Usage(), None),
        R2.model: ({"valid": True, "label": "x", "confidence": 0.7}, 0.7, Usage(), None),
        UNPRICED.model: ({"valid": False, "label": "x", "confidence": 0.9}, 0.9, Usage(), None),
    }

    result = _run_ladder((R1, R2, UNPRICED), outcomes, validate=lambda p: (p["valid"], "r"))

    assert result.needs_human
    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    assert records[0].winning_index == 2
    assert records[0].winning_model == R2.model


def test_consensus_accepts_two_validated_rungs_that_agree(cascade_logs):
    outcomes = {
        R1.model: ({"label": "x"}, 0.5, Usage(), None),
        R2.model: ({"label": "x"}, 0.6, Usage(), None),
    }

    result = asyncio.run(run_ladder(
        (R1, R2), _executor(outcomes), lambda p: (True, "ok"), 0.8, "test_task",
        consensus_k=2, consensus_key="label",
    ))

    assert result.accepted
    assert result.accepted_via == "consensus"
    records = _ladder_records(cascade_logs)
    assert len(records) == 1
    assert records[0].accepted is True
    assert records[0].accepted_via == "consensus"
    assert records[0].winning_index == 2


def test_human_readable_cascade_lines_are_kept(cascade_logs):
    outcomes = {
        R1.model: (None, None, Usage(), "upstream: boom"),
        R2.model: ({"label": "x", "confidence": 0.9}, 0.9, Usage(), None),
    }
    _run_ladder((R1, R2), outcomes)
    _drive_transport(_cascade_transport(
        (R1, R2),
        {
            R1.provider: [TransportError(message="rate limited", code="rate_limited")],
            R2.provider: [TextDelta(text="hi"), Done(usage=Usage())],
        },
    ))

    messages = [r.getMessage() for r in cascade_logs.records]
    assert any(m.startswith("cascade[test_task] rung foundry/DeepSeek-V4-Flash -> escalate") for m in messages)
    assert any(m.startswith("cascade[test_task] rung anthropic/claude-haiku-5-5 -> THRESHOLD") for m in messages)
    assert any(m.startswith("cascade transport: rung 1 foundry/DeepSeek-V4-Flash -> fallback (") for m in messages)
    assert any(m.startswith("cascade transport: accepted rung 2 anthropic/claude-haiku-5-5 after 1 fallback(s)") for m in messages)


def test_exhausted_ladder_keeps_the_warning_line(caplog):
    outcomes = {R1.model: ({"ok": False}, 0.3, Usage(), None)}

    _run_ladder((R1,), outcomes, validate=lambda p: (p["ok"], "r"))

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and r.getMessage().startswith("cascade[test_task] EXHAUSTED 1 rungs")
    ]
    assert len(warnings) == 1
