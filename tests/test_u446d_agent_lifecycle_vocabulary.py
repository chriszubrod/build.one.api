"""U-446d — the agent surfaces learn the six-state vocabulary.

`is_draft` is a computed column over `status` (`status != 'completed'`, U-446),
so it collapses five states into one boolean. Every agent-facing description
still called it a "drafts" filter, which is not a wording nit — it is a false
statement that steers the model wrong on the most common question anyone asks
about a bill. Live counts (2026-09-14): 42 bills are unfinished — 33
`in_review`, 8 `submitted`, 1 `declined`, and **zero** `draft`, because creating
a bill writes a Submitted review row. An agent answering "show me the drafts"
with `is_draft=true` returns 42 rows, none of which are drafts.

SCOPE, and why it is narrower than the booked "reword every file mentioning
is_draft":

* Only entities whose list endpoint actually has `?status=` may advertise it.
  Bill (U-443/U-445, agent half U-446d) and Expense (U-467, agent half U-469)
  do. BillCredit and Invoice are LS-03b/c and NOT BUILT — advertising a
  `status` filter their endpoints would silently ignore is worse than not
  having one, because the model would believe it had narrowed the page and
  reason over an unnarrowed one. Pinned below for BillCredit.
* **`Vendor.is_draft` is an entirely unrelated field** — a real, writable
  boolean meaning "incomplete vendor record". It has nothing to do with the
  document lifecycle, and a blind rewording of every file matching `is_draft`
  would have corrupted it. Pinned below so nobody does that later.
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


# ---------------------------------------------------------------------------
# 1 — the vocabulary itself has ONE definition
# ---------------------------------------------------------------------------


def test_the_canonical_vocabulary_is_exactly_these_six():
    """Every description below quotes these. If the tuple changes and the
    prompts don't, the agents teach a vocabulary the API no longer speaks."""
    assert tuple(LIFECYCLE_STATUSES) == CANONICAL


# ---------------------------------------------------------------------------
# 2 — search_bills gained the filter, and is_draft stopped lying
# ---------------------------------------------------------------------------


def test_search_bills_accepts_a_status_filter():
    from entities.bill.intelligence.tools import _SearchArgs

    assert "status" in _SearchArgs.model_fields


def _search_url(args: dict) -> str:
    """Drive the real `_search_bills` handler and return the URL it called.

    Sync + `asyncio.run`, matching `tests/test_bill_lifecycle_attach.py` — this
    repo has NO async pytest plugin, so an `async def` test is silently SKIPPED
    rather than run. These three are the only behavioural tests in this file;
    as `@pytest.mark.asyncio` they reported a cheerful pass while never
    executing a line of the handler.
    """
    from entities.bill.intelligence.tools import _search_bills

    ctx = MagicMock()
    ctx.call_api = AsyncMock(return_value={"data": []})
    asyncio.run(_search_bills(args, ctx))
    return ctx.call_api.call_args.args[1]


def test_status_is_forwarded_to_the_api_as_a_query_param():
    assert "status=in_review" in _search_url({"status": "in_review"})


def test_every_canonical_status_is_accepted_and_forwarded():
    """All six, not just the convenient ones — a model that learns the
    vocabulary from the description must be able to use all of it."""
    for value in CANONICAL:
        assert f"status={value}" in _search_url({"status": value})


def test_an_omitted_status_sends_no_status_param():
    """An absent filter must not become `status=None`, which the endpoint would
    treat as a real value and match nothing."""
    assert "status=" not in _search_url({"query": "INV-1"})


def test_status_and_is_draft_compose_rather_than_replace():
    """`is_draft` is not removed — the endpoint still honours it and installed
    callers still send it."""
    url = _search_url({"status": "declined", "is_draft": True})
    assert "status=declined" in url and "is_draft=true" in url


def test_the_status_field_enumerates_all_six_values_in_order():
    """The description IS the interface for a model — it cannot guess the six
    values, and a wrong one silently matches nothing rather than erroring.

    Asserted as the ordered ENUMERATION, not as six substring searches. The
    prose around it independently uses words like "declined" and "approved"
    when explaining when to prefer this filter, so a per-word check stays green
    while a value is quietly dropped from the list the model actually reads.
    """
    from entities.bill.intelligence.tools import _SearchArgs

    desc = _SearchArgs.model_fields["status"].description or ""
    enumerated = ", ".join(CANONICAL[:-1]) + " or " + CANONICAL[-1]
    assert enumerated in desc, (
        f"the status description must list the vocabulary as {enumerated!r}"
    )


def test_is_draft_no_longer_advertises_itself_as_a_drafts_filter():
    """THE defect. The old text was "true returns only draft (uncommitted)
    bills" — which tells the model that `is_draft=true` means draft."""
    from entities.bill.intelligence.tools import _SearchArgs

    desc = (_SearchArgs.model_fields["is_draft"].description or "").lower()
    assert "only draft" not in desc
    assert "completed" in desc
    assert "status" in desc, "it must point the model at the precise filter"


def test_the_search_tool_description_steers_state_questions_to_status():
    from entities.bill.intelligence.tools import search_bills

    desc = (search_bills.description or "").lower()
    for value in CANONICAL:
        assert value in desc, f"{value!r} missing from the search_bills description"


def test_complete_bill_no_longer_describes_a_writable_is_draft():
    """`is_draft` is computed and cannot be written at all (U-446), so the old
    "do NOT just flip is_draft via update_bill" advice described a route that
    no longer exists — and left the agent thinking one did."""
    from entities.bill.intelligence.tools import complete_bill

    desc = complete_bill.description or ""
    assert "IsDraft=false" not in desc
    assert "completed" in desc
    assert "status_locked" in desc, "terminality is the thing to warn about now"


# ---------------------------------------------------------------------------
# 3 — the prompts
# ---------------------------------------------------------------------------


def test_the_bill_specialist_prompt_defines_all_six_states():
    body = _text("intelligence/agents/bill_specialist/prompt.md")
    for value in CANONICAL:
        assert f"`{value}`" in body, f"{value!r} is not defined in the prompt"


def test_the_bill_specialist_prompt_corrects_the_is_draft_misreading():
    """Counted, not merely present.

    The prompt has to say this in BOTH places a reader arrives from — the
    lifecycle section and the `complete_bill` section — because an agent that
    reads only the completion instructions still needs to know there is no
    `is_draft` to flip. A bare `in body` check stays green when one of the two
    is reverted, which is exactly how a half-revert would ship.
    """
    body = _text("intelligence/agents/bill_specialist/prompt.md")
    assert body.count("computed column over") >= 2, (
        "the computed-column explanation must appear in both the lifecycle "
        "section and the complete_bill section"
    )
    assert "status != 'completed'" in body
    assert "review_status_kind" in body, (
        "the agent must be told to branch on the flag-derived kind, not on the "
        "admin-editable review_status Name"
    )


def test_the_bill_specialist_prompt_names_the_six_states_as_a_table():
    """The vocabulary needs a definition the model can read once, not six
    values scattered through prose."""
    body = _text("intelligence/agents/bill_specialist/prompt.md")
    assert "# Lifecycle vocabulary" in body
    section = body.split("# Lifecycle vocabulary", 1)[1]
    section = section.split("\n# ", 1)[0]
    for value in CANONICAL:
        assert f"| `{value}` |" in section, (
            f"{value!r} has no row in the vocabulary table"
        )


def test_the_bill_specialist_routing_rule_no_longer_hardcodes_is_draft_true():
    """The old rule was: 'Filter by draft state ("draft bills") → search_bills
    with is_draft=true' — the single most direct instruction to get it wrong."""
    body = _text("intelligence/agents/bill_specialist/prompt.md")
    assert '("draft bills", "uncommitted bills") → `search_bills` with `is_draft=true`' not in body
    assert "**Filter by lifecycle state**" in body


def test_the_orchestrator_routes_reviewer_questions_to_the_bill_specialist():
    body = _text("intelligence/agents/buildone/__init__.py")
    assert "lifecycle status" in body
    for value in CANONICAL:
        assert value in body


# ---------------------------------------------------------------------------
# 4 — what was deliberately NOT touched
# ---------------------------------------------------------------------------


def test_vendor_is_draft_is_left_alone_because_it_is_a_different_field():
    """`Vendor.is_draft` is a real, writable boolean meaning "incomplete vendor
    record". It is unrelated to the document lifecycle, and a blind rewording
    of every file matching `is_draft` would have corrupted it."""
    from entities.vendor.intelligence.tools import CreateVendorArgs

    desc = (CreateVendorArgs.model_fields["is_draft"].description or "").lower()
    assert "vendor" in desc
    for value in ("in_review", "submitted", "declined"):
        assert value not in desc, (
            "the document lifecycle vocabulary leaked into Vendor, which does "
            "not have one"
        )


def test_bill_credit_search_tool_does_not_advertise_a_status_filter_yet():
    """BillCredit's list endpoint has no `?status=` (LS-03b is not built). A
    filter the API silently ignores is worse than an absent one: the model
    would believe it had narrowed the result set and then reason over a full
    page. Expense's agent half shipped in U-469; this pin is BillCredit only."""
    from entities.bill_credit.intelligence.tools import _SearchArgs

    assert "status" not in _SearchArgs.model_fields


def test_bill_and_expense_expose_the_status_query_param_today():
    """The guard behind the scope decision above. If another entity's router
    gains `?status=`, this test fails and its agent surfaces should be brought
    onto the vocabulary in the same unit. Bill (U-445 / U-446d) and Expense
    (U-467 / U-469) are both live."""
    with_status = []
    for entity in ("bill", "expense", "bill_credit", "invoice"):
        router = REPO_ROOT / f"entities/{entity}/api/router.py"
        if not router.exists():
            continue
        src = router.read_text()
        executable = "\n".join(l.split("#")[0] for l in src.splitlines())
        if re.search(r"^\s+status: Optional\[str\] = Query\(", executable, re.M):
            with_status.append(entity)
    assert with_status == ["bill", "expense"], (
        f"entities exposing ?status= changed to {with_status} — bring their "
        "agent prompts and tools onto the six-state vocabulary too (Bill "
        "agent half is U-446d; Expense agent half is U-469)"
    )


# ---------------------------------------------------------------------------
# 5 — the tool and the endpoint must agree, forever
# ---------------------------------------------------------------------------


def test_the_endpoint_rejects_an_unknown_status_rather_than_returning_nothing():
    """The failure mode that matters for an agent.

    A filter that silently matches nothing is the worst outcome: the model asks
    for `status='pending'`, gets an empty page, and reports "there are no bills
    pending" — confidently and wrongly. The router validates against
    `LIFECYCLE_STATUSES` and answers 422 with the vocabulary in the message, so
    a model that guesses can correct itself on the next call.
    """
    src = _text("entities/bill/api/router.py")
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "status not in LIFECYCLE_STATUSES" in executable
    assert "HTTP_422_UNPROCESSABLE_CONTENT" in executable
    assert "Expected one of:" in executable, (
        "the error must name the valid values — that is what makes it "
        "self-correcting rather than merely a rejection"
    )


def test_the_tool_advertises_exactly_what_the_endpoint_accepts():
    """Ties the agent-facing vocabulary to `LIFECYCLE_STATUSES` itself, not to
    a copy of it in this test. Adding a seventh status without updating the
    tool description would leave the model unable to ask for it; removing one
    without updating the description would have the model asking for a value
    the router now 422s."""
    from entities.bill.intelligence.tools import _SearchArgs

    desc = _SearchArgs.model_fields["status"].description or ""
    for value in LIFECYCLE_STATUSES:
        assert value in desc, (
            f"{value!r} is a live lifecycle status the agent is never told about"
        )
    # And nothing the endpoint would reject.
    advertised = re.findall(r"\b(draft|submitted|in_review|approved|declined|completed|pending|open|void)\b", desc)
    assert set(advertised) <= set(LIFECYCLE_STATUSES), (
        f"the description names values the endpoint would 422: "
        f"{set(advertised) - set(LIFECYCLE_STATUSES)}"
    )


# ---------------------------------------------------------------------------
# 6 — the write paths an agent must not have (Codex P0/P1)
# ---------------------------------------------------------------------------


def test_create_bill_cannot_mint_an_already_completed_bill():
    """THE P0.

    `CreateBill` writes
    `COALESCE(@Status, CASE WHEN @IsDraft = 0 THEN 'completed' ELSE 'draft' END)`,
    so `is_draft=false` on CREATE produces a bill ALREADY in the terminal
    state: no SharePoint upload, no Excel sync, no QBO push, and
    `complete_bill` then refuses it as already complete. The row is terminal,
    locked by U-446b, and permanently unsynced — unreachable through the API.
    The field is simply not offered to agents; the router defaults to draft.
    """
    from entities.bill.intelligence.tools import CreateBillArgs

    assert "is_draft" not in CreateBillArgs.model_fields
    assert "status" not in CreateBillArgs.model_fields, (
        "nor may `status` be offered — it is the same hazard by its real name"
    )


def test_the_create_sproc_still_has_the_branch_this_guard_exists_for():
    """If the sproc ever stops minting a completed row from `@IsDraft = 0`,
    the guard above becomes unnecessary and this test says so out loud rather
    than leaving a mysterious omission."""
    from tests.sproc_text import REPO_ROOT as SPROC_ROOT, sproc_body

    body = sproc_body(SPROC_ROOT / "entities/bill/sql/dbo.bill_create_source_email.sql", "CreateBill")
    executable = "\n".join(l.split("--")[0] for l in body.splitlines())
    assert "CASE WHEN @IsDraft = 0 THEN 'completed'" in executable


def test_update_bill_does_not_offer_a_lifecycle_field():
    """`BillService.update_by_public_id` raises when `is_draft` differs from
    the stored value and the caller is not the completion pipeline, and since
    U-446 the column is unwritable anyway. Advertising it taught the agent to
    make a guaranteed-failing call."""
    from entities.bill.intelligence.tools import UpdateBillArgs, update_bill

    assert "is_draft" not in UpdateBillArgs.model_fields
    assert "status" not in UpdateBillArgs.model_fields
    assert "draft state" not in (update_bill.description or ""), (
        "the tool description must stop listing lifecycle as an editable field"
    )
    # The SAME claim lives in the specialist prompt, and fixing only the tool
    # left the prompt still telling the agent it could edit "draft state" —
    # found by Codex. Both surfaces or neither.
    prompt = _text("intelligence/agents/bill_specialist/prompt.md")
    assert "number, memo, draft state)" not in prompt


def test_a_contradictory_status_and_is_draft_pair_is_refused():
    """`is_draft` IS `status != 'completed'`, and the sproc ANDs them — so a
    contradictory pair returns an empty page and `count: 0`, which an agent
    reads as "there are none". Confidently wrong with no error to notice is
    the worst outcome available, so it is refused up front."""
    from entities.bill.intelligence.tools import _SearchArgs

    for bad in ({"status": "completed", "is_draft": True},
                {"status": "in_review", "is_draft": False},
                {"status": "draft", "is_draft": False}):
        with pytest.raises(Exception) as excinfo:
            _SearchArgs(**bad)
        assert "contradict" in str(excinfo.value)


def test_an_agreeing_status_and_is_draft_pair_is_allowed_through():
    """The guard must not reject a redundant-but-correct pair — installed
    callers send `is_draft` habitually."""
    from entities.bill.intelligence.tools import _SearchArgs

    for ok in ({"status": "completed", "is_draft": False},
               {"status": "in_review", "is_draft": True},
               {"status": "declined"},
               {"is_draft": True}):
        _SearchArgs(**ok)


def test_the_prompt_tells_the_agent_that_status_takes_one_value():
    """`status` is scalar equality in SQL. "Waiting on a reviewer" spans
    `submitted` AND `in_review`, so one search silently drops a group — today
    the difference between 8 bills and 33."""
    body = _text("intelligence/agents/bill_specialist/prompt.md")
    assert "takes exactly ONE value" in body
    assert "two searches" in body.lower()


def test_the_prompt_does_not_claim_creation_always_auto_submits():
    """Auto-submit is conditional: it needs a known user, a resolvable project
    on the first line, and no opt-out. A bill created without those lands in
    real `draft`, so "always zero drafts" is an overstatement that would have
    the agent skip a state that can exist."""
    body = _text("intelligence/agents/bill_specialist/prompt.md")
    lowered = body.lower()
    # Assert the ABSENCE of the false claim, not the presence of a hedge word.
    # The surrounding paragraph legitimately contains "usually" and "not
    # always" while explaining the condition, so a presence check stays green
    # even after the claim itself is reverted.
    assert "always empty" not in lowered
    assert "zero are actually `draft`, because creating a bill" not in lowered
    # And the actual precondition has to be spelled out, or the agent cannot
    # reason about when a real `draft` will exist.
    assert "opt out" in lowered or "opted out" in lowered
    assert "a project can be resolved" in lowered


def test_no_agent_surface_claims_sharepoint_and_excel_go_via_the_outbox():
    """They are triggered by the completion job itself; only the QBO push is a
    plain outbox enqueue. The agent does not need the queuing topology, and
    stating it wrongly is worse than not stating it."""
    from entities.bill.intelligence.tools import complete_bill

    body = _text("intelligence/agents/bill_specialist/prompt.md")
    combined = (complete_bill.description or "") + body
    assert "Excel workbook sync + QBO push via the outbox" not in combined
    assert "SharePoint upload + Excel workbook sync + QBO push via the outbox" not in combined
