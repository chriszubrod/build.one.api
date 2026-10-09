"""U-642b — Sonnet 5.5 replaces Sonnet 4.6 as the cascade's last Anthropic rung.

Sonnet 5.5 rejects `thinking: disabled`; its structured off-switch is
`between_tools`, which takes no other field and needs effort <= high.
"""

import importlib

import pytest

from intelligence.cascade.core import DEFAULT_LADDER
from intelligence.transport.anthropic import _thinking_off_type
from tests.loop_test_helpers import (
    HAIKU,
    HINT,
    LEGACY_SONNET,
    LOOKUP_TOOL,
    SAMPLING,
    SONNET_55,
    build,
    cost,
)


@pytest.mark.parametrize(
    "extra_body, expect_thinking",
    [(SAMPLING, False), ({**SAMPLING, **HINT}, True)],
    ids=["no_hint", "hint"],
)
def test_sonnet_55_omits_prohibited_request_fields(extra_body, expect_thinking):
    body = build(SONNET_55, extra_body, system="sys", tools=[LOOKUP_TOOL])
    for field in ("tool_choice", "temperature", "top_p", "top_k"):
        assert field not in body
    assert body["tools"] == [{**LOOKUP_TOOL, "cache_control": {"type": "ephemeral"}}]
    assert ("thinking" in body) is expect_thinking
    if expect_thinking:
        assert body["thinking"] == {"type": "between_tools"}


def test_hint_on_sonnet_55_sends_between_tools_and_drops_sampling():
    body = build(SONNET_55, {**SAMPLING, **HINT})
    assert body["thinking"] == {"type": "between_tools"}
    assert body["output_config"] == {"effort": "low"}
    assert "temperature" not in body
    assert "top_p" not in body
    assert "top_k" not in body


@pytest.mark.parametrize("model, off_type", [(HAIKU, "disabled"), (SONNET_55, "between_tools")])
@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_hint_clamps_effort_to_high_for_every_off_switch(model, off_type, effort):
    body = build(model, {"thinking": "off", "reasoning_effort": effort})
    assert body["output_config"] == {"effort": "high"}
    assert body["thinking"] == {"type": off_type}


@pytest.mark.parametrize("effort", ["medium", "xhigh"])
def test_no_hint_on_sonnet_55_adds_no_thinking_key_and_maps_effort(effort):
    body = build(SONNET_55, {"reasoning_effort": effort})
    assert "thinking" not in body
    assert body["output_config"] == {"effort": effort}


@pytest.mark.parametrize(
    "model, expected",
    [
        (HAIKU, "disabled"),
        (SONNET_55, "between_tools"),
        (LEGACY_SONNET, None),
        ("claude-opus-5-5", None),
        ("claude-fable-5-1", None),
    ],
)
def test_thinking_off_type_per_family(model, expected):
    assert _thinking_off_type(model) == expected


@pytest.mark.parametrize(
    "usage, expected",
    [
        ({"input_tokens": 1_000_000}, 2.00),
        ({"output_tokens": 1_000_000}, 10.00),
        ({"cache_read_input_tokens": 1_000_000}, 0.20),
        ({"cache_creation_input_tokens": 1_000_000}, 2.50),
    ],
)
@pytest.mark.parametrize("provider", ["anthropic", "cascade"])
def test_pricing_sonnet_55_rates_at_1m_tokens(provider, usage, expected):
    assert cost(SONNET_55, provider=provider, **usage) == pytest.approx(expected)


def test_default_ladder_anthropic_rungs_are_haiku_55_then_sonnet_55():
    anthropic_models = [r.model for r in DEFAULT_LADDER if r.provider == "anthropic"]
    assert anthropic_models == [HAIKU, SONNET_55]


def test_no_default_ladder_rung_names_sonnet_46():
    assert all(r.model != LEGACY_SONNET for r in DEFAULT_LADDER)


# Every agent in the fleet. The cascade picks the rung per call and ignores the
# pin for model selection, but the pin is persisted as the session's model label
# and used as the fallback turn label, so it must name a model that exists on
# the agent's effective ladder.
AGENT_NAMES = [
    "bill_credit_specialist",
    "bill_specialist",
    "buildone",
    "contract_labor_specialist",
    "cost_code_specialist",
    "customer_specialist",
    "email_triage_specialist",
    "expense_specialist",
    "invoice_specialist",
    "project_specialist",
    "sub_cost_code_specialist",
    "time_tracking_specialist",
    "vendor_specialist",
]


@pytest.mark.parametrize("name", AGENT_NAMES)
def test_cascade_agent_pin_names_a_model_on_its_ladder(name):
    agent = getattr(importlib.import_module(f"intelligence.agents.{name}.definition"), name)
    ladder = agent.ladder or DEFAULT_LADDER
    assert agent.provider == "cascade"
    assert agent.model in {r.model for r in ladder}


# The ten agents this unit moved off Sonnet 4.6 must pin Sonnet 5.5 exactly:
# the invariant above would also accept any other ladder model.
SONNET_AGENT_NAMES = [n for n in AGENT_NAMES if n not in ("contract_labor_specialist", "email_triage_specialist", "time_tracking_specialist")]
assert len(SONNET_AGENT_NAMES) == 10


@pytest.mark.parametrize("name", SONNET_AGENT_NAMES)
def test_agent_model_pin_is_sonnet_55(name):
    agent = getattr(importlib.import_module(f"intelligence.agents.{name}.definition"), name)
    assert agent.model == SONNET_55
