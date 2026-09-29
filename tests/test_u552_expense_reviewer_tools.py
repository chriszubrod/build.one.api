"""U-552 — expense_specialist reviewer-reply tools (find + apply).

Pure-logic tests: mock ToolContext.call_api; no live DB.
"""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from intelligence.tools.base import ToolResult
from tests.test_u459_reviewer_impersonation import CALL_SITES as U459_SERVICE_CALL_SITES

# Agent-tool HTTP adapters that POST to apply-reviewer-decision. Listed here
# so a new adapter cannot ship without registration (U-457 / U-524 lesson).
AGENT_APPLY_REVIEWER_DECISION_TOOL_HANDLERS = [
    ("entities.bill.intelligence.tools", "_apply_reviewer_decision"),
    ("entities.expense.intelligence.tools", "_apply_expense_reviewer_decision"),
]


@pytest.mark.parametrize("module,handler", AGENT_APPLY_REVIEWER_DECISION_TOOL_HANDLERS)
def test_agent_apply_reviewer_decision_handler_is_registered(module, handler):
    import importlib

    mod = importlib.import_module(module)
    assert hasattr(mod, handler), (
        f"{module}.{handler} missing — add it to "
        "AGENT_APPLY_REVIEWER_DECISION_TOOL_HANDLERS"
    )


def test_expense_agent_handler_is_in_the_u459_extension_registry():
    assert (
        "entities.expense.intelligence.tools",
        "_apply_expense_reviewer_decision",
    ) in AGENT_APPLY_REVIEWER_DECISION_TOOL_HANDLERS


def test_expense_service_apply_reviewer_decision_remains_in_u459_call_sites():
    assert (
        "entities.expense.business.service",
        "ExpenseService",
        "apply_reviewer_decision",
    ) in U459_SERVICE_CALL_SITES


def test_expense_agent_handler_does_not_reimplement_assert_may_act_as():
    """Delegation binding stays in ExpenseService.apply_reviewer_decision (U-459)."""
    from entities.expense.intelligence import tools as mod

    src = inspect.getsource(mod._apply_expense_reviewer_decision)
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "assert_may_act_as" not in executable
    assert "ExpenseService" not in executable
    assert "apply-reviewer-decision" in executable


def test_find_expense_by_conversation_id_calls_the_lookup_route():
    from entities.expense.intelligence.tools import _find_expense_by_conversation_id

    ctx = SimpleNamespace(
        call_api=AsyncMock(
            return_value=ToolResult(content='{"public_id":"exp-1"}')
        )
    )

    async def _run():
        await _find_expense_by_conversation_id(
            {
                "conversation_id": "conv-abc",
                "reference_number_hint": "RCT-99",
                "project_hint": "Buffalo",
            },
            ctx,
        )

    asyncio.run(_run())
    ctx.call_api.assert_awaited_once()
    method, path = ctx.call_api.await_args.args[:2]
    assert method == "GET"
    assert path.startswith("/api/v1/get/expense/find-by-conversation-id?")
    assert "conversation_id=conv-abc" in path
    assert "reference_number_hint=RCT-99" in path
    assert "project_hint=Buffalo" in path


def test_find_expense_by_conversation_id_percent_encodes_adversarial_conversation_id():
    """Pins urlencode(quote_via=quote) so reserved chars stay one query value."""
    from entities.expense.intelligence.tools import _find_expense_by_conversation_id

    adversarial = "a&b=c/d?e"
    ctx = SimpleNamespace(
        call_api=AsyncMock(return_value=ToolResult(content="null"))
    )

    async def _run():
        await _find_expense_by_conversation_id(
            {"conversation_id": adversarial},
            ctx,
        )

    asyncio.run(_run())
    method, path = ctx.call_api.await_args.args[:2]
    assert method == "GET"
    parsed = urlparse(path)
    assert parsed.path == "/api/v1/get/expense/find-by-conversation-id"
    qs = parse_qs(parsed.query, keep_blank_values=True)
    assert list(qs.keys()) == ["conversation_id"]
    assert qs["conversation_id"] == [adversarial]


def test_apply_without_sub_cost_code_surfaces_server_refusal():
    from entities.expense.intelligence.tools import _apply_expense_reviewer_decision

    ctx = SimpleNamespace(
        call_api=AsyncMock(
            return_value=ToolResult(
                content="sub_cost_code_public_id is required when decision is approved",
                is_error=True,
            )
        )
    )

    async def _run():
        return await _apply_expense_reviewer_decision(
            {
                "expense_public_id": "exp-pub-1",
                "decision": "approved",
                "reviewer_email": "pm@test.com",
                "sub_cost_code_public_id": None,
                "raw_reply_text": "Approved — fuel",
            },
            ctx,
        )

    result = asyncio.run(_run())
    assert result.is_error
    assert "sub_cost_code" in str(result.content).lower()

    ctx.call_api.assert_awaited_once()
    method, path = ctx.call_api.await_args.args[:2]
    assert method == "POST"
    assert path == "/api/v1/expense/exp-pub-1/apply-reviewer-decision"
    body = ctx.call_api.await_args.kwargs["body"]
    assert body == {
        "decision": "approved",
        "reviewer_email": "pm@test.com",
        "sub_cost_code_public_id": None,
        "description": None,
        "raw_reply_text": "Approved — fuel",
        "reviewer_email_message_public_id": None,
    }


def test_apply_on_non_draft_expense_is_terminal_not_retried():
    from entities.expense.intelligence.tools import apply_expense_reviewer_decision

    msg = (
        "Expense exp-pub-1 is no longer a draft "
        "(Complete already pressed); reviewer decisions cannot be applied."
    )
    ctx = SimpleNamespace(
        call_api=AsyncMock(return_value=ToolResult(content=msg, is_error=True))
    )

    async def _run():
        return await apply_expense_reviewer_decision.handler(
            {
                "expense_public_id": "exp-pub-1",
                "decision": "approved",
                "reviewer_email": "pm@test.com",
                "sub_cost_code_public_id": "scc-pub",
                "reviewer_email_message_public_id": "msg-pub-9",
            },
            ctx,
        )

    result = asyncio.run(_run())
    assert result.is_error
    assert "no longer a draft" in str(result.content)
    assert "do NOT retry" in apply_expense_reviewer_decision.description

    method, path = ctx.call_api.await_args.args[:2]
    assert method == "POST"
    assert path == "/api/v1/expense/exp-pub-1/apply-reviewer-decision"
    body = ctx.call_api.await_args.kwargs["body"]
    assert body["sub_cost_code_public_id"] == "scc-pub"
    assert body["reviewer_email_message_public_id"] == "msg-pub-9"


def test_expense_specialist_allowlists_reviewer_reply_tools():
    from intelligence.agents.expense_specialist.definition import expense_specialist

    assert "apply_expense_reviewer_decision" in expense_specialist.tools
    assert "find_sub_cost_code_for_reply" in expense_specialist.tools


def test_find_expense_by_conversation_id_is_deliberately_NOT_allowlisted():
    """The lookup tool exists but its ROUTE does not — keep it unregistered.

    U-552 built `find_expense_by_conversation_id` correctly, but
    `GET /api/v1/get/expense/find-by-conversation-id` is not implemented:
    Bill has that route (`entities/bill/api/router.py`, backed by
    `BillRepository.find_for_reviewer_reply`) and Expense has neither the route
    nor the repo method. Registering the tool would arm a guaranteed 404 inside
    an agent that writes approvals on money documents, and the failure would
    read as a data problem rather than a missing endpoint.

    ⛔ This test pins the ABSENCE on purpose. If you are here because it failed,
    you either built the route — in which case register the tool and delete this
    test — or you registered the tool without the route, which is the mistake
    this test exists to stop. Do not "fix" it by loosening the assertion.
    """
    from intelligence.agents.expense_specialist.definition import expense_specialist
    from entities.expense.intelligence import tools as expense_tools

    # The tool is DEFINED ...
    assert getattr(expense_tools, "find_expense_by_conversation_id", None) is not None
    # ... and deliberately NOT reachable by the agent.
    assert "find_expense_by_conversation_id" not in expense_specialist.tools
