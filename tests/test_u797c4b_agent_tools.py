"""U-797c4b item 5 — bill_credit_specialist reviewer-reply tool (fakes only)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

import entities.bill_credit.intelligence.tools as bc_tools
from intelligence.agents.bill_credit_specialist.definition import bill_credit_specialist
from intelligence.cascade import email_classification
from intelligence.tools.registry import get


def _ctx():
    ctx = MagicMock()
    ctx.call_api = AsyncMock(return_value={"ok": True})
    return ctx


def _args(**kw):
    args = dict(
        bill_credit_public_id="bc-1",
        decision="approved",
        reviewer_email="pm@example.test",
        sub_cost_code_public_id="scc-1",
        description="Freight",
        raw_reply_text="Approved",
        reviewer_email_message_public_id="m-1",
    )
    args.update(kw)
    return args


def test_tool_is_registered_under_its_own_name():
    tool = get("apply_bill_credit_reviewer_decision")
    assert tool is bc_tools.apply_bill_credit_reviewer_decision


def test_bill_tool_keeps_the_plain_name():
    # The Bill tool owns "apply_reviewer_decision"; the registry refuses duplicates.
    import entities.bill.intelligence.tools as bill_tools

    assert get("apply_reviewer_decision") is bill_tools.apply_reviewer_decision
    assert bill_tools.apply_reviewer_decision.name != bc_tools.apply_bill_credit_reviewer_decision.name


def test_specialist_lists_the_reply_tools():
    tools = set(bill_credit_specialist.tools)
    assert {"apply_bill_credit_reviewer_decision", "find_sub_cost_code_for_reply"} <= tools


def test_handler_posts_to_the_bill_credit_route_with_the_body():
    ctx = _ctx()
    asyncio.run(bc_tools._apply_bill_credit_reviewer_decision(_args(), ctx))
    method, path = ctx.call_api.call_args.args[:2]
    body = ctx.call_api.call_args.kwargs["body"]
    assert (method, path) == ("POST", "/api/v1/bill-credit/bc-1/apply-reviewer-decision")
    assert body["decision"] == "approved"
    assert body["sub_cost_code_public_id"] == "scc-1"
    assert body["reviewer_email_message_public_id"] == "m-1"
    assert "bill_credit_public_id" not in body


def test_args_require_the_provenance_message_id():
    with pytest.raises(ValidationError):
        bc_tools.ApplyBillCreditReviewerDecisionArgs(
            bill_credit_public_id="bc-1", decision="rejected", reviewer_email="pm@example.test",
        )


def test_triage_wording_covers_bill_credit():
    from pathlib import Path

    text = Path(email_classification.__file__).read_text()
    assert "reviewer_reply: an internal PM/Owner reply approving/rejecting a bill, expense or bill credit." in text
