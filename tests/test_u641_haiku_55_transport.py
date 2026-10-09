"""U-641 — Haiku 5.5 cascade rung; effort-only transport merge for 5.x models.

The 5.x model family (and opus-4-7/4-8, fable, mythos) rejects non-default
temperature/top_p/top_k with HTTP 400 and takes effort via
`output_config.effort`. Older models must keep today's sampling-param merge
and must never receive `output_config`.
"""

import pytest

from intelligence.cascade.core import DEFAULT_LADDER
from intelligence.messages.types import Message, Thinking, ToolResult, ToolUse
from intelligence.observability.pricing import compute_cost_usd
from intelligence.transport.anthropic import _effort_only_model, _gen_params
from intelligence.transport.base import Usage
from tests.loop_test_helpers import build as _build, user_text as _user_text


EFFORT_ONLY = "claude-haiku-5-5"
LEGACY = "claude-sonnet-4-6"
LEGACY_HAIKU = "claude-haiku-4-5-20251001"
SAMPLING = {"temperature": 0, "top_p": 0.5, "top_k": 10}
CASCADE_DEFAULTS = {"temperature": 0, "reasoning_effort": "minimal"}
CASCADE_DEFAULTS_WITH_SAMPLING = {**SAMPLING, "reasoning_effort": "minimal"}


@pytest.mark.parametrize(
    "model",
    [
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-fable-5-1",
        "claude-mythos-1",
    ],
)
def test_effort_only_model_true_for_5x_and_newer_families(model):
    assert _effort_only_model(model) is True


@pytest.mark.parametrize("model", [LEGACY_HAIKU, LEGACY, "claude-opus-4-6"])
def test_effort_only_model_false_for_legacy_models(model):
    assert _effort_only_model(model) is False


def test_effort_only_model_maps_cascade_defaults_to_low_and_drops_sampling():
    params = _gen_params(EFFORT_ONLY, CASCADE_DEFAULTS_WITH_SAMPLING)
    assert "temperature" not in params
    assert "top_p" not in params
    assert "top_k" not in params
    assert params["output_config"] == {"effort": "low"}


def test_legacy_model_keeps_temperature_and_gets_no_output_config():
    params = _gen_params(LEGACY, CASCADE_DEFAULTS)
    assert params["temperature"] == 0
    assert "output_config" not in params


@pytest.mark.parametrize("extra", [{"temperature": 0}, {"temperature": 0, "reasoning_effort": "bogus"}])
def test_effort_only_model_adds_no_output_config_when_effort_missing_or_unknown(extra):
    assert "output_config" not in _gen_params(EFFORT_ONLY, extra)


@pytest.mark.parametrize("model", [EFFORT_ONLY, LEGACY])
def test_stop_sequences_forwarded_for_both_families(model):
    extra = {"stop_sequences": ["END"], "temperature": 0, "reasoning_effort": "high"}
    assert _gen_params(model, extra)["stop_sequences"] == ["END"]


def test_default_ladder_anthropic_rungs_are_haiku_55_then_sonnet_46():
    anthropic_models = [r.model for r in DEFAULT_LADDER if r.provider == "anthropic"]
    assert anthropic_models == ["claude-haiku-5-5", "claude-sonnet-4-6"]


def test_build_body_haiku_55_without_hint_sends_no_thinking_key():
    body = _build(EFFORT_ONLY, CASCADE_DEFAULTS_WITH_SAMPLING)
    assert "thinking" not in body
    assert body["output_config"] == {"effort": "low"}
    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body
    assert body["stream"] is True
    assert body["model"] == EFFORT_ONLY


def test_build_body_haiku_55_thinking_off_hint_disables_thinking_and_drops_sampling():
    body = _build(EFFORT_ONLY, {"thinking": "off", "reasoning_effort": "minimal"})
    assert body["thinking"] == {"type": "disabled"}
    assert body["output_config"] == {"effort": "low"}
    assert "thinking" not in _gen_params(EFFORT_ONLY, {"thinking": "off"})


@pytest.mark.parametrize("model", [LEGACY, LEGACY_HAIKU])
def test_build_body_legacy_models_keep_sampling_and_get_no_thinking_or_effort(model):
    body = _build(model, SAMPLING)
    assert body["temperature"] == 0
    assert body["top_p"] == 0.5
    assert body["top_k"] == 10
    assert "thinking" not in body
    assert "output_config" not in body
    assert body["stream"] is True


@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_build_body_haiku_55_clamps_effort_to_high_with_thinking_disabled(effort):
    body = _build(EFFORT_ONLY, {"thinking": "off", "reasoning_effort": effort})
    assert body["output_config"] == {"effort": "high"}
    assert body["thinking"] == {"type": "disabled"}


def test_build_body_sonnet_55_keeps_xhigh_effort_and_adds_no_thinking():
    body = _build("claude-sonnet-5-5", {"reasoning_effort": "xhigh"})
    assert body["output_config"] == {"effort": "xhigh"}
    assert "thinking" not in body


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", LEGACY])
def test_build_body_thinking_off_hint_dropped_for_models_that_reject_disabled(model):
    body = _build(model, {"thinking": "off", "reasoning_effort": "minimal"})
    assert "thinking" not in body


def test_thinking_off_hint_is_dropped_for_opus_55_but_effort_still_maps():
    body = _build("claude-opus-5-5", {"thinking": "off", "reasoning_effort": "minimal"})
    assert "thinking" not in body
    assert body["output_config"] == {"effort": "low"}


def test_tool_loop_haiku_55_replays_thinking_block_before_tool_use():
    thinking = Thinking(thinking="", signature="sig-1")
    second = _build(
        EFFORT_ONLY, {"reasoning_effort": "minimal"},
        messages=[
            _user_text("look it up"),
            Message(role="assistant", content=[
                thinking,
                ToolUse(id="toolu_1", name="lookup", input={"q": "x"}),
            ]),
            Message(role="user", content=[ToolResult(tool_use_id="toolu_1", content="42")]),
        ],
    )

    assistant_turn = next(m for m in second["messages"] if m["role"] == "assistant")
    assert assistant_turn["content"] == [
        {"type": "thinking", "thinking": "", "signature": "sig-1"},
        {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"q": "x"}},
    ]


def _haiku_cost(**usage):
    return compute_cost_usd(provider="anthropic", model=EFFORT_ONLY, usage=Usage(**usage))


def test_pricing_haiku_55_base_tier_boundary_is_per_request():
    assert _haiku_cost(input_tokens=100_000) == pytest.approx(0.01)
    # 0.0500005 sits on the 6-decimal rounding boundary, so allow the rounding step.
    assert _haiku_cost(input_tokens=100_001) == pytest.approx(0.0500005, abs=1e-6)


@pytest.mark.parametrize(
    "usage, expected",
    [
        ({"input_tokens": 50_000, "cache_read_input_tokens": 60_000}, 0.028),
        ({"input_tokens": 50_000, "cache_creation_input_tokens": 60_000}, 0.0625),
    ],
)
def test_pricing_haiku_55_cache_tokens_count_toward_threshold(usage, expected):
    assert _haiku_cost(**usage) == pytest.approx(expected)


def test_pricing_haiku_55_output_base_tier_and_input_long_tier():
    assert _haiku_cost(output_tokens=1_000_000) == pytest.approx(0.50)
    assert _haiku_cost(input_tokens=1_000_000) == pytest.approx(0.50)


def test_pricing_haiku_55_long_tier_prices_output_at_long_rate():
    # long_output must apply above the threshold, not just long_input.
    assert _haiku_cost(input_tokens=200_000, output_tokens=1_000_000) == pytest.approx(2.60)


def test_pricing_legacy_sonnet_46_unchanged_at_1m_input():
    assert compute_cost_usd(
        provider="anthropic", model=LEGACY, usage=Usage(input_tokens=1_000_000),
    ) == pytest.approx(3.00)
