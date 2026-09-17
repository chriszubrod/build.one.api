"""U-469 — the Expense agent surfaces learn the six-state vocabulary.

Bill got this in U-446d. Expense's list endpoint gained `?status=` in U-467
and the terminal lock in U-468; both are deployed. The agent `_SearchArgs`
and `expense_specialist` prompt still spoke only `is_draft`, which since
U-446 is a computed column over `status` (`status != 'completed'`) and so
collapses five states into one boolean.

A filter the API would honour, advertised nowhere, is the inverse of the
U-446d hazard: the model cannot ask for `in_review` expenses even though
the sproc would answer truthfully.
"""

import asyncio
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.lifecycle import LIFECYCLE_STATUSES

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ("draft", "submitted", "in_review", "approved", "declined", "completed")


def _text(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def test_the_canonical_vocabulary_is_exactly_these_six():
    assert tuple(LIFECYCLE_STATUSES) == CANONICAL


def test_search_expenses_accepts_a_status_filter():
    from entities.expense.intelligence.tools import _SearchArgs

    assert "status" in _SearchArgs.model_fields


def _search_url(args: dict) -> str:
    """Drive the real `_search_expenses` handler and return the URL it called.

    Sync + `asyncio.run`, matching `tests/test_u446d_agent_lifecycle_vocabulary.py`
    — this repo has NO async pytest plugin, so an `async def` test is silently
    SKIPPED rather than run.
    """
    from entities.expense.intelligence.tools import _search_expenses

    ctx = MagicMock()
    ctx.call_api = AsyncMock(return_value={"data": []})
    asyncio.run(_search_expenses(args, ctx))
    return ctx.call_api.call_args.args[1]


def test_status_is_forwarded_to_the_api_as_a_query_param():
    assert "status=in_review" in _search_url({"status": "in_review"})


def test_every_canonical_status_is_accepted_and_forwarded():
    """All six, not just the convenient ones — a model that learns the
    vocabulary from the description must be able to use all of it."""
    for value in LIFECYCLE_STATUSES:
        assert f"status={value}" in _search_url({"status": value})


def test_an_omitted_status_sends_no_status_param():
    """An absent filter must not become `status=None`, which the endpoint would
    treat as a real value and match nothing."""
    assert "status=" not in _search_url({"query": "RCT-1"})


def test_status_and_is_draft_compose_rather_than_replace():
    url = _search_url({"status": "declined", "is_draft": True})
    assert "status=declined" in url and "is_draft=true" in url


def test_the_status_field_enumerates_all_six_values_in_order():
    from entities.expense.intelligence.tools import _SearchArgs

    desc = _SearchArgs.model_fields["status"].description or ""
    enumerated = ", ".join(CANONICAL[:-1]) + " or " + CANONICAL[-1]
    assert enumerated in desc, (
        f"the status description must list the vocabulary as {enumerated!r}"
    )


def test_is_draft_no_longer_advertises_itself_as_a_drafts_filter():
    """THE defect. The old text was "true returns only draft expenses" —
    which tells the model that `is_draft=true` means draft."""
    from entities.expense.intelligence.tools import _SearchArgs

    desc = (_SearchArgs.model_fields["is_draft"].description or "").lower()
    assert "only draft" not in desc
    assert "completed" in desc
    assert "status" in desc, "it must point the model at the precise filter"


def test_search_query_does_not_steer_to_is_draft():
    """`query` used to say "Combine with vendor_id and is_draft to narrow",
    which steers the model at the coarse boolean for a text search."""
    from entities.expense.intelligence.tools import _SearchArgs

    desc = (_SearchArgs.model_fields["query"].description or "").lower()
    assert "is_draft" not in desc


def test_the_search_tool_description_steers_state_questions_to_status():
    from entities.expense.intelligence.tools import search_expenses

    desc = (search_expenses.description or "").lower()
    for value in CANONICAL:
        assert value in desc, f"{value!r} missing from the search_expenses description"


def test_complete_expense_no_longer_describes_a_writable_is_draft():
    """`is_draft` is computed and cannot be written at all (U-446 / U-458),
    so the old "do NOT just flip is_draft via update_expense" advice
    described a route that no longer exists — and left the agent thinking
    one did."""
    from entities.expense.intelligence.tools import complete_expense

    desc = complete_expense.description or ""
    assert "IsDraft=false" not in desc
    assert "completed" in desc
    assert "status_locked" in desc, "terminality is the thing to warn about now"


def test_the_endpoint_rejects_an_unknown_status_rather_than_returning_nothing():
    """A filter that silently matches nothing is the worst outcome: the model
    asks for `status='pending'`, gets an empty page, and reports "there are no
    expenses pending" — confidently and wrongly. U-467's router validates
    against `LIFECYCLE_STATUSES` and answers 422 with the vocabulary in the
    message, so a model that guesses can correct itself on the next call.
    """
    src = _text("entities/expense/api/router.py")
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "status not in LIFECYCLE_STATUSES" in executable
    assert "HTTP_422_UNPROCESSABLE_CONTENT" in executable
    assert "Expected one of:" in executable, (
        "the error must name the valid values — that is what makes it "
        "self-correcting rather than merely a rejection"
    )


def test_the_tool_advertises_exactly_what_the_endpoint_accepts():
    from entities.expense.intelligence.tools import _SearchArgs

    desc = _SearchArgs.model_fields["status"].description or ""
    for value in LIFECYCLE_STATUSES:
        assert value in desc, (
            f"{value!r} is a live lifecycle status the agent is never told about"
        )
    advertised = re.findall(
        r"\b(draft|submitted|in_review|approved|declined|completed|pending|open|void)\b",
        desc,
    )
    assert set(advertised) <= set(LIFECYCLE_STATUSES), (
        f"the description names values the endpoint would 422: "
        f"{set(advertised) - set(LIFECYCLE_STATUSES)}"
    )


def test_a_contradictory_status_and_is_draft_pair_is_refused():
    """`is_draft` IS `status != 'completed'`, and the sproc ANDs them — so a
    contradictory pair returns an empty page and `count: 0`, which an agent
    reads as "there are none". Loop every status: a mutant that special-cases
    only `completed` + `approved` stays green on a two-status sample."""
    from entities.expense.intelligence.tools import _SearchArgs

    with pytest.raises(Exception) as excinfo:
        _SearchArgs(status="completed", is_draft=True)
    assert "contradict" in str(excinfo.value)

    for status in LIFECYCLE_STATUSES:
        if status == "completed":
            continue
        with pytest.raises(Exception) as excinfo:
            _SearchArgs(status=status, is_draft=False)
        assert "contradict" in str(excinfo.value), status


def test_an_agreeing_status_and_is_draft_pair_is_allowed_through():
    """The guard must not reject a redundant-but-correct pair — installed
    callers send `is_draft` habitually. Every unfinished status agrees with
    `is_draft=True`; `completed` agrees with `is_draft=False`."""
    from entities.expense.intelligence.tools import _SearchArgs

    _SearchArgs(status="completed", is_draft=False)
    for status in LIFECYCLE_STATUSES:
        if status == "completed":
            continue
        _SearchArgs(status=status, is_draft=True)
    _SearchArgs(status="declined")
    _SearchArgs(is_draft=True)


def test_the_expense_specialist_prompt_defines_all_six_states():
    body = _text("intelligence/agents/expense_specialist/prompt.md")
    for value in CANONICAL:
        assert f"`{value}`" in body, f"{value!r} is not defined in the prompt"


def test_the_expense_specialist_prompt_names_the_six_states_as_a_table():
    body = _text("intelligence/agents/expense_specialist/prompt.md")
    assert "# Lifecycle vocabulary" in body
    section = body.split("# Lifecycle vocabulary", 1)[1]
    section = section.split("\n# ", 1)[0]
    for value in CANONICAL:
        assert f"| `{value}` |" in section, (
            f"{value!r} has no row in the vocabulary table"
        )


def test_the_expense_specialist_routing_rule_no_longer_hardcodes_is_draft_true():
    body = _text("intelligence/agents/expense_specialist/prompt.md")
    assert "Filter by draft state" not in body
    assert "**Filter by lifecycle state**" in body


def test_the_prompt_tells_the_agent_that_status_takes_one_value():
    """`status` is scalar equality in SQL. "Waiting on a reviewer" spans
    `submitted` AND `in_review`, so one search silently drops a group."""
    body = _text("intelligence/agents/expense_specialist/prompt.md")
    assert "takes exactly ONE value" in body
    assert "two searches" in body.lower()


def test_the_expense_specialist_prompt_corrects_the_is_draft_misreading():
    """Counted, not merely present.

    The prompt has to say this in BOTH places a reader arrives from — the
    lifecycle section and the `complete_expense` section — because an agent
    that reads only the completion instructions still needs to know there
    is no `is_draft` to flip. A bare `in body` check stays green when one
    of the two is reverted, which is exactly how a half-revert would ship.
    """
    body = _text("intelligence/agents/expense_specialist/prompt.md")
    assert body.count("computed column over") >= 2, (
        "the computed-column explanation must appear in both the lifecycle "
        "section and the complete_expense section"
    )
    assert "status != 'completed'" in body
    assert "review_status_kind" in body, (
        "the agent must be told to branch on the flag-derived kind, not on the "
        "admin-editable review_status Name"
    )
    assert "Lifecycle state is NOT editable here" in body, (
        "the update_expense bullet must say lifecycle is not editable"
    )


def test_create_expense_cannot_mint_an_already_completed_expense():
    """THE P0.

    `CreateExpense` writes
    `COALESCE(@Status, CASE WHEN @IsDraft = 0 THEN 'completed' ELSE 'draft' END)`,
    so `is_draft=false` on CREATE produces an expense ALREADY in the terminal
    state: no SharePoint upload, no Excel sync, and `complete_expense` then
    refuses it as already complete. The row is terminal, locked by U-468, and
    permanently unsynced — unreachable through the API. The field is simply
    not offered to agents (U-458); the router defaults to draft.
    """
    from entities.expense.intelligence.tools import CreateExpenseArgs

    assert "is_draft" not in CreateExpenseArgs.model_fields
    assert "status" not in CreateExpenseArgs.model_fields, (
        "nor may `status` be offered — it is the same hazard by its real name"
    )


def test_update_expense_does_not_offer_a_lifecycle_field():
    """`is_draft` was removed from UpdateExpenseArgs in U-458. Advertising it
    taught the agent to make a guaranteed-failing call, and since U-446 the
    column is unwritable anyway. Completing is the complete_expense tool.
    """
    from entities.expense.intelligence.tools import UpdateExpenseArgs, update_expense

    assert "is_draft" not in UpdateExpenseArgs.model_fields
    assert "status" not in UpdateExpenseArgs.model_fields
    desc = update_expense.description or ""
    assert "draft state" not in desc, (
        "the tool description must stop listing lifecycle as an editable field"
    )
    assert "Lifecycle state is NOT one of them" in desc
    # The SAME claim lives in the specialist prompt, and fixing only the tool
    # left the prompt still telling the agent it could edit "draft state" —
    # found by Codex on Bill. Both surfaces or neither.
    prompt = _text("intelligence/agents/expense_specialist/prompt.md")
    assert "number, memo, draft state)" not in prompt


@pytest.mark.parametrize("tool_name", ["delegate_to_bill", "delegate_to_expense"])
def test_the_orchestrator_delegation_teaches_the_lifecycle_per_entity(tool_name):
    """Sliced PER DELEGATION BLOCK, not over the whole file.

    U-446d's `test_the_orchestrator_routes_reviewer_questions_to_the_bill_specialist`
    asserts `"lifecycle status" in body` against the ENTIRE module, so once Bill
    carried the vocabulary that test stayed green no matter what
    `delegate_to_expense` said — and it did stay green through U-469's first
    build, while the Expense block still advertised "by vendor, reference, or
    filter" and claimed a QBO push that is disabled. The orchestrator is the
    ROUTER: if its description for an entity never mentions lifecycle status,
    a "what is waiting on a reviewer" question may never reach the specialist
    that can answer it, which is the capability U-469 exists to add.
    """
    from tests.sproc_text import REPO_ROOT

    src = (REPO_ROOT / "intelligence/agents/buildone/__init__.py").read_text()
    start = src.index(f'name="{tool_name}"')
    block = src[start:src.index("_register_tool", start + 1)] if "_register_tool" in src[start + 1:] else src[start:]

    assert "lifecycle" in block, f"{tool_name} never mentions lifecycle status"
    for value in LIFECYCLE_STATUSES:
        assert value in block, f"{tool_name} omits the {value!r} state"
    assert "waiting on a" in block, (
        f"{tool_name} lacks the reviewer-routing hint, so those questions may "
        f"not reach the specialist"
    )
