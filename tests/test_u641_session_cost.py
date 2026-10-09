"""U-641 — a session's cost is the sum of its per-turn costs, replayed as persisted."""

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from intelligence.api.replay import tail_session
from intelligence.loop.events import Done, TurnEnd
from intelligence.persistence.session_repo import AgentSession, AgentTurn
from tests.loop_test_helpers import (
    HAIKU,
    ScriptedTransport,
    alist,
    drive,
    error_turn,
    final_turn,
    tool_turn,
)


def _drive(transport: ScriptedTransport) -> list[Any]:
    return drive(transport, provider="anthropic")


def _turn_ends(events: list[Any]) -> list[TurnEnd]:
    return [e for e in events if isinstance(e, TurnEnd)]


def _done(events: list[Any]) -> Done:
    return next(e for e in events if isinstance(e, Done))


def test_session_cost_is_sum_of_per_request_turn_costs():
    transport = ScriptedTransport([
        tool_turn(HAIKU, 60_000),
        final_turn(HAIKU, 60_000),
    ])
    events = _drive(transport)

    turn_ends = _turn_ends(events)
    assert turn_ends[0].cost_usd == pytest.approx(0.006)
    assert turn_ends[1].cost_usd == pytest.approx(0.006)
    done = _done(events)
    assert done.reason == "end_turn"
    assert done.usage.input_tokens == 120_000
    assert done.cost_usd == pytest.approx(0.012)


def test_session_cost_sums_turns_priced_on_different_models():
    transport = ScriptedTransport([
        tool_turn("gpt-5.4-nano", 60_000),
        final_turn(HAIKU, 60_000),
    ])
    done = _done(_drive(transport))
    assert done.cost_usd == pytest.approx(0.018)


def test_unknown_turn_pricing_makes_session_cost_unknown():
    transport = ScriptedTransport([
        tool_turn("no-such-model", 60_000),
        final_turn(HAIKU, 60_000),
    ])
    events = _drive(transport)

    turn_ends = _turn_ends(events)
    assert turn_ends[0].cost_usd is None
    assert turn_ends[1].cost_usd == pytest.approx(0.006)
    assert _done(events).cost_usd is None


def test_error_exit_reports_cost_of_completed_turns_only():
    transport = ScriptedTransport([
        tool_turn(HAIKU, 60_000),
        error_turn(HAIKU),
    ])
    done = _done(_drive(transport))
    assert done.reason == "error"
    assert done.usage.input_tokens == 60_000
    assert done.cost_usd == pytest.approx(0.006)


def _terminal_session(total_cost_usd):
    return AgentSession(
        id=7,
        public_id="sess-pub",
        status="completed",
        termination_reason="end_turn",
        provider="anthropic",
        model=HAIKU,
        total_input_tokens=120_000,
        total_output_tokens=0,
        total_cost_usd=total_cost_usd,
    )


def _persisted_turns():
    return [
        AgentTurn(
            id=1, session_id=7, turn_number=1, model=HAIKU,
            input_tokens=60_000, stop_reason="tool_use", cost_usd=0.006,
        ),
        AgentTurn(
            id=2, session_id=7, turn_number=2, model=HAIKU,
            input_tokens=60_000, stop_reason="end_turn", cost_usd=0.006,
        ),
    ]


def _replay(session) -> list[Any]:
    with (
        patch(
            "intelligence.api.replay.AgentSessionRepo.read_by_public_id",
            return_value=session,
        ),
        patch(
            "intelligence.api.replay.AgentTurnRepo.read_by_session_id",
            return_value=_persisted_turns(),
        ),
        patch(
            "intelligence.api.replay.AgentToolCallRepo.read_by_turn_id",
            return_value=[],
        ),
        patch(
            "intelligence.api.replay.AgentApprovalRequestRepo.read_by_session_id",
            return_value=[],
        ),
    ):
        return asyncio.run(alist(tail_session("sess-pub")))


def test_replay_passes_persisted_turn_costs_and_session_cost():
    events = _replay(_terminal_session(total_cost_usd=0.012))

    turn_ends = _turn_ends(events)
    assert [t.cost_usd for t in turn_ends] == [
        pytest.approx(0.006),
        pytest.approx(0.006),
    ]
    assert _done(events).cost_usd == pytest.approx(0.012)


def test_replay_falls_back_to_repricing_totals_for_legacy_rows():
    events = _replay(_terminal_session(total_cost_usd=None))
    assert _done(events).cost_usd == pytest.approx(0.06)
