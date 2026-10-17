"""Offline tests for U-654a bill coding StructuredTask."""
from __future__ import annotations

import json

import pytest

from intelligence.cascade import bill_coding as bill_coding_mod
from intelligence.cascade.bill_coding import (
    ALLOWED_PAYLOAD_KEYS,
    BILL_CODING_FOUNDRY_LADDER,
    BILL_CODING_SUGGESTION_TASK,
    BILL_TEXT_CHAR_BUDGET,
    CLAUDE_HAIKU_LADDER,
    SUSPENSE_SCC_NUMBER,
    ladder_by_name,
    normalize_scc_number,
    run_bill_coding_suggestion,
    truncate_bill_text,
    validate_bill_coding_result,
)
from intelligence.cascade.core import CascadeResult, Rung


def _sample_payload(**overrides):
    base = {
        "vendor_name": "Acme Supply",
        "bill_text": "Invoice for lumber",
        "history_evidence": [{"scc_number": "03.01", "scc_name": "Framing", "count": 4}],
        "candidate_sccs": [
            {"number": "03.01", "name": "Framing"},
            {"number": "04.02", "name": "Drywall"},
        ],
        "candidate_projects": None,
    }
    base.update(overrides)
    return base


def test_truncate_bill_text_at_budget():
    long = "x" * (BILL_TEXT_CHAR_BUDGET + 50)
    out = truncate_bill_text(long)
    assert len(out) <= BILL_TEXT_CHAR_BUDGET + len("\n…(truncated)")
    assert out.endswith("(truncated)")


def test_payload_user_message_only_allowed_keys():
    payload = _sample_payload()
    assert set(payload.keys()) == ALLOWED_PAYLOAD_KEYS
    msg = BILL_CODING_SUGGESTION_TASK.build_user_message(payload)
    parsed = json.loads(msg)
    assert set(parsed.keys()) == ALLOWED_PAYLOAD_KEYS
    with pytest.raises(ValueError, match="unexpected payload keys"):
        BILL_CODING_SUGGESTION_TASK.build_user_message({**payload, "memo": "secret"})


def test_validator_accepts_in_catalog():
    payload = _sample_payload()
    ok, reason = validate_bill_coding_result(
        {
            "scc_number": "03.01",
            "project_abbreviation": None,
            "confidence": 0.9,
            "rationale": "matches history",
        },
        payload=payload,
    )
    assert ok and reason == "ok"


def test_validator_rejects_suspense():
    payload = _sample_payload(
        candidate_sccs=[{"number": SUSPENSE_SCC_NUMBER, "name": "Suspense"}],
    )
    ok, reason = validate_bill_coding_result(
        {
            "scc_number": SUSPENSE_SCC_NUMBER,
            "project_abbreviation": None,
            "confidence": 0.5,
            "rationale": "x",
        },
        payload=payload,
    )
    assert not ok and "00.02" in reason


def test_validator_rejects_unknown_scc():
    payload = _sample_payload()
    ok, _ = validate_bill_coding_result(
        {
            "scc_number": "99.99",
            "project_abbreviation": None,
            "confidence": 0.5,
            "rationale": "x",
        },
        payload=payload,
    )
    assert not ok


def test_validator_project_in_list_or_null():
    payload = _sample_payload(
        candidate_projects=[{"abbreviation": "BR1", "name": "Bridge One"}],
    )
    ok, _ = validate_bill_coding_result(
        {
            "scc_number": "03.01",
            "project_abbreviation": "BR1",
            "confidence": 0.8,
            "rationale": "ok",
        },
        payload=payload,
    )
    assert ok
    ok2, _ = validate_bill_coding_result(
        {
            "scc_number": "03.01",
            "project_abbreviation": "NOPE",
            "confidence": 0.8,
            "rationale": "bad",
        },
        payload=payload,
    )
    assert not ok2
    bad_mode_a, _ = validate_bill_coding_result(
        {
            "scc_number": "03.01",
            "project_abbreviation": "BR1",
            "confidence": 0.8,
            "rationale": "bad",
        },
        payload=_sample_payload(candidate_projects=None),
    )
    assert not bad_mode_a


def test_validator_malformed_json_fields():
    ok, reason = validate_bill_coding_result({"confidence": 0.5}, payload=_sample_payload())
    assert not ok and "scc_number" in reason


def test_r1_p2_1_validator_strips_scc_number_before_catalog_check():
    payload = _sample_payload()
    ok, _ = validate_bill_coding_result(
        {
            "scc_number": " 03.01 ",
            "project_abbreviation": None,
            "confidence": 0.9,
            "rationale": "ok",
        },
        payload=payload,
    )
    assert ok


def test_r1_p2_6_task_validator_uses_payload_candidates_after_build_user_message():
    payload = _sample_payload(
        candidate_sccs=[{"number": "04.02", "name": "Drywall"}],
    )
    BILL_CODING_SUGGESTION_TASK.build_user_message(payload)
    ok, reason = BILL_CODING_SUGGESTION_TASK.validate(
        {
            "scc_number": "03.01",
            "project_abbreviation": None,
            "confidence": 0.9,
            "rationale": "not in list",
        }
    )
    assert not ok and "not in candidate_sccs" in reason
    ok2, _ = BILL_CODING_SUGGESTION_TASK.validate(
        {
            "scc_number": normalize_scc_number(SUSPENSE_SCC_NUMBER),
            "project_abbreviation": None,
            "confidence": 0.9,
            "rationale": "nope",
        }
    )
    assert not ok2


def test_r2_p3_validator_fails_closed_without_payload_context():
    bill_coding_mod._bill_coding_payload.set(None)
    ok, reason = BILL_CODING_SUGGESTION_TASK.validate(
        {
            "scc_number": "03.01",
            "project_abbreviation": None,
            "confidence": 0.9,
            "rationale": "unchecked",
        }
    )
    assert not ok and reason == "no_payload_context"


def test_ladder_by_name_known_and_unknown():
    assert ladder_by_name("foundry") == BILL_CODING_FOUNDRY_LADDER
    assert ladder_by_name("claude-haiku") == CLAUDE_HAIKU_LADDER
    with pytest.raises(ValueError, match="unknown ladder"):
        ladder_by_name("not-a-ladder")


def test_run_bill_coding_suggestion_ladder_override():
    import asyncio
    from unittest.mock import patch

    payload = _sample_payload()
    captured: dict[str, object] = {}

    async def fake_run_cascade(task, _payload, transport_for=None):
        captured["ladder"] = task.ladder
        return CascadeResult(
            accepted=True,
            needs_human=False,
            result={
                "scc_number": "03.01",
                "project_abbreviation": None,
                "confidence": 0.9,
                "rationale": "ok",
            },
            confidence=0.9,
            winning_rung=task.ladder[0],
            attempts=[],
        )

    with patch("intelligence.cascade.bill_coding.run_cascade", side_effect=fake_run_cascade):
        asyncio.run(run_bill_coding_suggestion(payload))
        assert captured["ladder"] == BILL_CODING_FOUNDRY_LADDER

        captured.clear()
        asyncio.run(run_bill_coding_suggestion(payload, ladder=CLAUDE_HAIKU_LADDER))
        assert captured["ladder"] == CLAUDE_HAIKU_LADDER
