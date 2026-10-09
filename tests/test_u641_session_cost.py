"""U-641 — a session's cost is the sum of its per-turn costs, replayed as persisted."""

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest

import intelligence.transport.base as tb
from intelligence.api.replay import tail_session
from intelligence.loop.events import Done, TurnEnd
from intelligence.loop.runner import run
from intelligence.persistence.session_repo import AgentSession, AgentTurn
from intelligence.tools.base import Tool, ToolContext, ToolResult
from intelligence.transport.base import Usage


HAIKU = "claude-haiku-5-5"


class ScriptedTransport:
    def __init__(self, turns: list[list[Any]]):
        self._turns = turns
        self.calls = 0

    async def stream(
        self,
        messages,
        model,
        system=None,
        max_tokens=4096,
        tools=None,
        extra_body=None,
    ) -> AsyncIterator[Any]:
        script = self._turns[self.calls]
        self.calls += 1
        for event in script:
            yield event


def _tool_turn(model: str, input_tokens: int) -> list[Any]:
    return [
        tb.TurnStart(model=model),
        tb.ToolUseComplete(id="toolu_1", name="lookup", input={"q": "x"}),
        tb.TurnEnd(stop_reason="tool_use"),
        tb.Done(usage=Usage(input_tokens=input_tokens)),
    ]


def _final_turn(model: str, input_tokens: int) -> list[Any]:
    return [
        tb.TurnStart(model=model),
        tb.TextDelta(text="all done"),
        tb.TurnEnd(stop_reason="end_turn"),
        tb.Done(usage=Usage(input_tokens=input_tokens)),
    ]


def _error_turn(model: str) -> list[Any]:
    return [
        tb.TurnStart(model=model),
        tb.TransportError(message="upstream exploded", code="upstream"),
    ]


async def _lookup(args, ctx):
    return ToolResult(content="42")


LOOKUP = Tool(
    name="lookup",
    description="look a value up",
    input_schema={"type": "object", "properties": {}},
    handler=_lookup,
)


async def _alist(agen) -> list[Any]:
    return [ev async for ev in agen]


def _drive(transport: ScriptedTransport) -> list[Any]:
    return asyncio.run(_alist(run(
        transport=transport,
        model=HAIKU,
        user_message="go",
        tools=[LOOKUP],
        ctx=ToolContext(),
        provider="anthropic",
    )))


def _turn_ends(events: list[Any]) -> list[TurnEnd]:
    return [e for e in events if isinstance(e, TurnEnd)]


def _done(events: list[Any]) -> Done:
    return next(e for e in events if isinstance(e, Done))


def test_session_cost_is_sum_of_per_request_turn_costs():
    transport = ScriptedTransport([
        _tool_turn(HAIKU, 60_000),
        _final_turn(HAIKU, 60_000),
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
        _tool_turn("gpt-5.4-nano", 60_000),
        _final_turn(HAIKU, 60_000),
    ])
    done = _done(_drive(transport))
    assert done.cost_usd == pytest.approx(0.018)


def test_unknown_turn_pricing_makes_session_cost_unknown():
    transport = ScriptedTransport([
        _tool_turn("no-such-model", 60_000),
        _final_turn(HAIKU, 60_000),
    ])
    events = _drive(transport)

    turn_ends = _turn_ends(events)
    assert turn_ends[0].cost_usd is None
    assert turn_ends[1].cost_usd == pytest.approx(0.006)
    assert _done(events).cost_usd is None


def test_error_exit_reports_cost_of_completed_turns_only():
    transport = ScriptedTransport([
        _tool_turn(HAIKU, 60_000),
        _error_turn(HAIKU),
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
        return asyncio.run(_alist(tail_session("sess-pub")))


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
