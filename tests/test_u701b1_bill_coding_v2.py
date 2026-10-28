"""U-701b1: V2 bill coding task parity with harness; V1 pinned."""
from __future__ import annotations

import importlib

import pytest

import scripts.u654a_text_backtest as u654a
from intelligence.cascade.bill_coding import (
    ALLOWED_PAYLOAD_KEYS,
    BILL_CODING_SUGGESTION_TASK,
    BILL_CODING_SUGGESTION_TASK_V2,
    BILL_TEXT_CHAR_BUDGET,
    FULL_CONTENT_CHAR_CAP,
    SIMILAR_BILLS_RULE_LINE,
    _SYSTEM_PROMPT,
    should_escalate,
)


def _sample_payload(*, similar_bills=None, bill_text="Invoice for lumber"):
    payload = {
        "vendor_name": "Acme",
        "bill_text": bill_text,
        "history_evidence": [],
        "candidate_sccs": [{"number": "43.10", "name": "Framing"}],
        "candidate_projects": None,
    }
    if similar_bills is not None:
        payload["similar_bills"] = similar_bills
    return payload


def test_v1_system_prompt_allowed_keys_and_budget_pinned():
    import hashlib

    # Pinned literal: an edit to the V1 prompt must fail here (comparing the task to
    # _SYSTEM_PROMPT alone would compare an object with itself).
    assert (
        hashlib.sha256(BILL_CODING_SUGGESTION_TASK.system_prompt.encode("utf-8")).hexdigest()
        == "3184ec31632cd22dd841f8737f01a4312d1b3d0808ae4dbd22fe27bd5702f5d4"
    )
    assert BILL_CODING_SUGGESTION_TASK.threshold == 0.5
    assert ALLOWED_PAYLOAD_KEYS == frozenset({
        "vendor_name",
        "bill_text",
        "history_evidence",
        "candidate_sccs",
        "candidate_projects",
    })
    assert BILL_TEXT_CHAR_BUDGET == 6000


def test_v1_rejects_leaked_memo_key():
    payload = _sample_payload()
    with pytest.raises(ValueError, match="unexpected payload keys"):
        BILL_CODING_SUGGESTION_TASK.build_user_message({**payload, "memo": "secret"})


def test_v2_rejects_leaked_description_key_like_v1():
    payload = _sample_payload(similar_bills=[])
    with pytest.raises(ValueError, match="unexpected payload keys"):
        BILL_CODING_SUGGESTION_TASK_V2.build_user_message(
            {**payload, "description": "leak"},
        )


def test_v2_system_prompt_and_harness_parity():
    harness_task = u654a.backtest_cascade_task(
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        similar_bills_evidence=True,
        line_items_rule=False,
    )
    assert harness_task.system_prompt == BILL_CODING_SUGGESTION_TASK_V2.system_prompt
    assert SIMILAR_BILLS_RULE_LINE in BILL_CODING_SUGGESTION_TASK_V2.system_prompt


@pytest.mark.parametrize("similar", ["missing", "empty", "neighbours"])
@pytest.mark.parametrize("bill_len", [100, FULL_CONTENT_CHAR_CAP + 500])
def test_v2_user_message_matches_harness(similar, bill_len):
    text = "x" * bill_len
    payload = _sample_payload(bill_text=text)
    if similar == "empty":
        payload["similar_bills"] = []
    elif similar == "neighbours":
        payload["similar_bills"] = [
            {"scc_number": "43.10", "match": 0.5, "amount_ratio": 1.0},
        ]
    harness_msg = u654a._backtest_build_user_message(
        payload,
        bill_text_mode=u654a.BILL_TEXT_MODE_FULL_CONTENT,
        include_similar_bills=True,
    )
    v2_msg = BILL_CODING_SUGGESTION_TASK_V2.build_user_message(payload)
    assert harness_msg == v2_msg


def test_should_escalate_matches_harness_table():
    cases = [
        (None, None, True),
        (1, None, True),
        (None, 0.8, True),
        (1, 0.69, True),
        (1, 0.70, False),
        (1, 0.71, False),
    ]
    for scc, conf, expected in cases:
        assert should_escalate(scc, conf, escalate_below=0.70) == expected
        assert u654a.should_escalate_text_answer(
            scc, conf, escalate_below=0.70,
        ) == expected


def test_mutation_none_threshold_branch_is_required():
    """If None accept_threshold were treated as 0.0, Haiku@default 0.5 would wrongly accept 0.49."""
    core = importlib.import_module("intelligence.cascade.core")
    haiku = core.Rung("anthropic", "claude-haiku-5-5")
    from intelligence.transport.base import Usage

    outcome = (
        {"scc_number": "43.10", "project_abbreviation": None, "confidence": 0.49},
        0.49,
        Usage(),
        None,
    )

    async def execute(_rung):
        return outcome

    def validate(parsed):
        return True, "ok"

    import asyncio

    result = asyncio.run(
        core.run_ladder((haiku,), execute, validate, 0.5, "mutation-check"),
    )
    assert not result.accepted
    assert result.needs_human


@pytest.mark.parametrize("length", [10, 59_999, 60_000, 60_001, 120_000])
@pytest.mark.parametrize("trailing", ["", "   \n"])
def test_harness_full_content_recap_is_output_neutral(length, trailing):
    """bill_text_for_user_message re-truncates at 60k; text the harness already capped
    and stripped (resolve_bill_text_from_raw_blob -> build_backtest_payload) must pass
    through unchanged, so committed U-696 / U-701b arms are unaffected."""
    from intelligence.cascade.bill_coding import FULL_CONTENT_CHAR_CAP, truncate_bill_text
    from scripts import u654a_text_backtest as h

    raw = ("x" * length) + trailing
    capped = truncate_bill_text(raw, budget=FULL_CONTENT_CHAR_CAP).strip()
    assert h.bill_text_for_user_message({"bill_text": capped}, h.BILL_TEXT_MODE_FULL_CONTENT) == capped
