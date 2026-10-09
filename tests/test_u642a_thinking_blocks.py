"""U-642a — thinking blocks are captured byte-identical and replayed in wire order."""

import asyncio
import json
from typing import Any

import intelligence.transport.base as tb
from intelligence.cascade.core import Rung, StructuredTask, _complete_structured
from intelligence.messages.convert import _block_to_anthropic, to_anthropic_request, to_openai_request
from intelligence.messages.types import (
    Message,
    RedactedThinking,
    Text,
    Thinking,
    ToolResult as ToolResultBlock,
    ToolUse,
)
from intelligence.transport.anthropic import _sse_to_events
from intelligence.transport.base import Usage
from intelligence.transport.foundry import _filter_gen_params
from tests.loop_test_helpers import HAIKU, ScriptedTransport, agen, alist, drive, final_turn, user_text


# Raw wire literals, kept verbatim: they are the byte-identity evidence.
MESSAGE_START = ("message_start", {"message": {"model": HAIKU, "usage": {"input_tokens": 10, "output_tokens": 1}}})
TOOL_USE_END = [
    ("message_delta", {"delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 42}}),
    ("message_stop", {}),
]

TRANSCRIPT = [
    MESSAGE_START,
    ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "  Héllo "}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "wörld\n é "}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "signature_delta", "signature": "SIG=="}}),
    ("content_block_stop", {"index": 0}),
    ("content_block_start", {"index": 1, "content_block": {"type": "redacted_thinking", "data": "OPAQUE=="}}),
    ("content_block_stop", {"index": 1}),
    ("content_block_start", {"index": 2, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"index": 2, "delta": {"type": "text_delta", "text": "Let me look."}}),
    ("content_block_stop", {"index": 2}),
    ("content_block_start", {"index": 3, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {}}}),
    ("content_block_delta", {"index": 3, "delta": {"type": "input_json_delta", "partial_json": "{\"q\": \"x\"}"}}),
    ("content_block_stop", {"index": 3}),
    *TOOL_USE_END,
]

INTERLEAVED_TRANSCRIPT = [
    MESSAGE_START,
    ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": "pre", "signature": "S0"}}),
    ("content_block_start", {"index": 1, "content_block": {"type": "text", "text": ""}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "-a"}}),
    ("content_block_delta", {"index": 1, "delta": {"type": "text_delta", "text": "x"}}),
    ("content_block_start", {"index": 2, "content_block": {"type": "redacted_thinking", "data": "D1"}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "signature_delta", "signature": "S1"}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "-b"}}),
    ("content_block_delta", {"index": 0, "delta": {"type": "signature_delta", "signature": "S2"}}),
    ("content_block_stop", {"index": 1}),
    ("content_block_stop", {"index": 2}),
    ("content_block_start", {"index": 3, "content_block": {"type": "tool_use", "id": "toolu_3", "name": "lookup", "input": {}}}),
    ("content_block_delta", {"index": 3, "delta": {"type": "input_json_delta", "partial_json": "{\"q\": \"y\"}"}}),
    ("content_block_stop", {"index": 0}),
    ("content_block_stop", {"index": 3}),
    *TOOL_USE_END,
]


def _parse(transcript) -> list[Any]:
    return asyncio.run(alist(_sse_to_events(agen(transcript), HAIKU)))


def test_parser_captures_thinking_blocks_byte_identical_in_wire_order():
    assert _parse(TRANSCRIPT) == [
        tb.TurnStart(model=HAIKU),
        tb.ThinkingComplete(block=Thinking(thinking="  Héllo wörld\n é ", signature="SIG==")),
        tb.ThinkingComplete(block=RedactedThinking(data="OPAQUE==")),
        tb.TextDelta(text="Let me look."),
        tb.ToolUseStart(id="toolu_1", name="lookup"),
        tb.ToolUseComplete(id="toolu_1", name="lookup", input={"q": "x"}),
        tb.TurnEnd(stop_reason="tool_use"),
        tb.Done(usage=Usage(input_tokens=10, output_tokens=42)),
    ]


def test_interleaved_blocks_buffer_per_index_and_complete_in_stop_order():
    assert _parse(INTERLEAVED_TRANSCRIPT) == [
        tb.TurnStart(model=HAIKU),
        tb.TextDelta(text="x"),
        tb.ThinkingComplete(block=RedactedThinking(data="D1")),
        tb.ToolUseStart(id="toolu_3", name="lookup"),
        tb.ThinkingComplete(block=Thinking(thinking="pre-a-b", signature="S0S1S2")),
        tb.ToolUseComplete(id="toolu_3", name="lookup", input={"q": "y"}),
        tb.TurnEnd(stop_reason="tool_use"),
        tb.Done(usage=Usage(input_tokens=10, output_tokens=42)),
    ]


def test_tool_input_streamed_in_several_json_chunks_is_joined_in_order():
    assert _parse([
        MESSAGE_START,
        ("content_block_start", {"index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {}}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"q\": "}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": "\"x\", "}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": "\"n\": 2}"}}),
        ("content_block_stop", {"index": 0}),
        *TOOL_USE_END,
    ]) == [
        tb.TurnStart(model=HAIKU),
        tb.ToolUseStart(id="toolu_1", name="lookup"),
        tb.ToolUseComplete(id="toolu_1", name="lookup", input={"q": "x", "n": 2}),
        tb.TurnEnd(stop_reason="tool_use"),
        tb.Done(usage=Usage(input_tokens=10, output_tokens=42)),
    ]


def test_error_event_yields_transport_error_and_nothing_else():
    assert _parse([
        MESSAGE_START,
        ("error", {"error": {"type": "overloaded_error", "message": "busy"}}),
    ]) == [
        tb.TurnStart(model=HAIKU),
        tb.TransportError(message="busy", code="overloaded_error"),
    ]


def test_cache_usage_from_message_start_survives_message_delta():
    assert _parse([
        ("message_start", {"message": {"model": HAIKU, "usage": {
            "input_tokens": 100,
            "output_tokens": 1,
            "cache_creation_input_tokens": 7,
            "cache_read_input_tokens": 9,
        }}}),
        ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "hi"}}),
        ("content_block_stop", {"index": 0}),
        ("message_delta", {"delta": {}, "usage": {"output_tokens": 33}}),
        ("message_stop", {}),
    ]) == [
        tb.TurnStart(model=HAIKU),
        tb.TextDelta(text="hi"),
        tb.TurnEnd(stop_reason=None),
        tb.Done(usage=Usage(
            input_tokens=100,
            output_tokens=33,
            cache_creation_input_tokens=7,
            cache_read_input_tokens=9,
        )),
    ]


def test_early_eof_without_message_stop_emits_no_turn_end_or_done():
    assert _parse([
        MESSAGE_START,
        ("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "partial"}}),
    ]) == [
        tb.TurnStart(model=HAIKU),
        tb.TextDelta(text="partial"),
    ]


def test_thinking_block_is_replayed_before_tool_use_on_the_next_request():
    transport = ScriptedTransport([
        [
            tb.TurnStart(model=HAIKU),
            tb.ThinkingComplete(block=Thinking(thinking="", signature="sig-1")),
            tb.ToolUseComplete(id="toolu_1", name="lookup", input={"q": "x"}),
            tb.TurnEnd(stop_reason="tool_use"),
            tb.Done(usage=Usage(input_tokens=5, output_tokens=5)),
        ],
        final_turn(HAIKU, 5),
    ])
    events = drive(transport)

    assert transport.calls == 2
    second_request = transport.seen_messages[1]
    assert [m.role for m in second_request] == ["user", "assistant", "user"]
    assert second_request[1].content == [
        Thinking(thinking="", signature="sig-1"),
        ToolUse(id="toolu_1", name="lookup", input={"q": "x"}),
    ]
    tool_result = second_request[2].content[0]
    assert isinstance(tool_result, ToolResultBlock)
    assert tool_result.tool_use_id == "toolu_1"

    wire_assistant = to_anthropic_request(second_request, model=HAIKU)["messages"][1]
    assert wire_assistant["content"] == [
        {"type": "thinking", "thinking": "", "signature": "sig-1"},
        {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"q": "x"}},
    ]
    assert not any("Thinking" in type(e).__name__ for e in events)


def test_text_is_flushed_before_thinking_and_tool_use_keeps_arrival_order():
    transport = ScriptedTransport([
        [
            tb.TurnStart(model=HAIKU),
            tb.TextDelta(text="a"),
            tb.ThinkingComplete(block=Thinking(thinking="t", signature="s")),
            tb.TextDelta(text="b"),
            tb.ToolUseStart(id="toolu_1", name="lookup"),
            tb.ToolUseComplete(id="toolu_1", name="lookup", input={"q": "x"}),
            tb.TurnEnd(stop_reason="tool_use"),
            tb.Done(usage=Usage(input_tokens=5, output_tokens=5)),
        ],
        final_turn(HAIKU, 5),
    ])
    drive(transport)

    assert transport.seen_messages[1][1].content == [
        Text(text="a"),
        Thinking(thinking="t", signature="s"),
        Text(text="b"),
        ToolUse(id="toolu_1", name="lookup", input={"q": "x"}),
    ]


def test_anthropic_converter_round_trips_thinking_parts_to_exact_wire_dicts():
    wire = [
        {"type": "thinking", "thinking": "t ", "signature": "s=="},
        {"type": "redacted_thinking", "data": "d=="},
    ]
    message = Message.model_validate({"role": "assistant", "content": wire})

    assert [_block_to_anthropic(b) for b in message.content] == wire


def test_openai_converter_drops_thinking_and_skips_assistant_turn_left_empty():
    messages = [
        user_text("hi"),
        Message(role="assistant", content=[
            Thinking(thinking="t", signature="s"),
            Text(text="kept"),
        ]),
        Message(role="assistant", content=[RedactedThinking(data="d")]),
        Message(role="assistant", content=[
            Thinking(thinking="t2", signature="s2"),
            ToolUse(id="toolu_9", name="lookup", input={"q": "x"}),
        ]),
        Message(role="user", content=[ToolResultBlock(tool_use_id="toolu_9", content="42")]),
    ]

    body = to_openai_request(messages, model="gpt-5.4-mini")

    assert body["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "kept"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "toolu_9",
                "type": "function",
                "function": {"name": "lookup", "arguments": json.dumps({"q": "x"})},
            }],
        },
        {"role": "tool", "tool_call_id": "toolu_9", "content": "42"},
    ]


def test_structured_task_parses_json_from_text_only_when_thinking_streams():
    task = StructuredTask(
        name="t",
        system_prompt="s",
        threshold=0.5,
        build_user_message=str,
        validate=lambda d: (True, ""),
    )
    transport = ScriptedTransport([[
        tb.ThinkingComplete(block=Thinking(thinking='{"label": "wrong", "confidence": 0.1}', signature="sig")),
        tb.TextDelta(text='{"label": "right", "confidence": 0.9}'),
        tb.Done(usage=Usage(output_tokens=7)),
    ]])

    parsed, confidence, usage, error = asyncio.run(_complete_structured(
        transport, Rung("anthropic", HAIKU), "s", "u", gen_params=task.gen_params,
    ))

    assert parsed == {"label": "right", "confidence": 0.9}
    assert confidence == 0.9
    assert error is None
    assert transport.seen_extra_body[0]["thinking"] == "off"


def test_thinking_hint_never_reaches_the_foundry_wire_body():
    for model, kept in (
        ("gpt-5.4-nano", {"reasoning_effort": "minimal"}),
        ("DeepSeek-V4-Flash", {"temperature": 0}),
    ):
        filtered = _filter_gen_params(
            model, {"temperature": 0, "reasoning_effort": "minimal", "thinking": "off"},
        )

        assert "thinking" not in filtered
        assert filtered == kept
