"""U-459 — you may only act in your own name, unless you are the automation.

Two routes let a caller assert a DIFFERENT person's identity and write a Review
attributed to them:

    POST /bill/{id}/apply-reviewer-decision
    POST /contract-labor/apply-reviewer-decision

Both exist for the email-reply review workflow — a PM replies to a notification,
the specialist agent parses the reply and applies that person's decision. So
asserting someone else's identity is the POINT, not an oversight.

The oversight was that nothing checked WHO WAS ASKING. Both are gated on
`can_update` for their module; both authorized `reviewer_email` by matching it
against the document's PM/Owner recipients and never against the authenticated
caller. **Any user with edit rights could POST an approval in their Project
Manager's name.** Surfaced by Codex while reviewing U-458 (the forged approval is
exactly what a `require_approved` gate would accept), but it predates the gate
and is a live forgery path on its own.

The binding is free: both services already resolve `reviewer_user_id` from the
matched recipient. Nothing new is looked up on the happy path — the `is_agent`
fallback query runs ONLY for a caller who is neither a system context nor a
system admin AND is asserting someone else, i.e. only on a request already about
to be refused.

WHAT THIS DOES NOT DO: verify that the EMAIL genuinely came from the person whose
decision is applied. The agent asserts what it parsed; `reviewer_email_message_public_id`
carries a provenance chain that nothing enforces. Separate unit, booked. This one
closes the half that lets an ordinary authenticated user forge.
"""

import inspect
import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shared.api.errors import ErrorCode
from shared.authz.context import clear_authz_context, set_authz_context, system_authz
from shared.authz.delegation import IdentityAssertionRefused, assert_may_act_as


@pytest.fixture(autouse=True)
def _clean_context():
    clear_authz_context()
    yield
    clear_authz_context()


# ---------------------------------------------------------------------------
# 1 — the guard
# ---------------------------------------------------------------------------


def test_acting_as_yourself_is_always_allowed():
    set_authz_context(user_id=7, company_id=1, is_system_admin=False)
    assert assert_may_act_as(asserted_user_id=7, what="x") is False


def test_a_plain_user_cannot_act_as_someone_else():
    """THE defect. A user with `can_update` on Bills asserting their PM."""
    set_authz_context(user_id=7, company_id=1, is_system_admin=False)
    with pytest.raises(IdentityAssertionRefused) as e:
        assert_may_act_as(asserted_user_id=99, what="apply a reviewer decision")
    assert e.value.status_code == 403
    assert e.value.error_code == ErrorCode.IDENTITY_ASSERTION_REFUSED


def test_a_system_admin_may_delegate():
    set_authz_context(user_id=33, company_id=1, is_system_admin=True)
    assert assert_may_act_as(asserted_user_id=99, what="x") is True


def test_a_system_context_may_delegate():
    """Outbox workers, CLI sync, the drain endpoint — non-HTTP boundaries that
    legitimately act for the people whose work they are processing."""
    with system_authz():
        assert assert_may_act_as(asserted_user_id=99, what="x") is True


def test_an_agent_user_may_delegate_even_without_system_admin():
    """Belt-and-braces. Claude Agent (33) is recorded as IsSystemAdmin=1 so the
    check above already covers it — but this workflow is LIVE automation, and a
    guard that breaks the agent fleet because one flag was not what the notes
    said is worse than one extra query on a path that was going to raise."""
    set_authz_context(user_id=44, company_id=1, is_system_admin=False)
    with patch("entities.user.business.service.UserService") as Svc:
        Svc.return_value.read_by_id.return_value = SimpleNamespace(is_agent=True)
        assert assert_may_act_as(asserted_user_id=99, what="x") is True


def test_a_non_agent_user_is_still_refused_after_the_lookup():
    set_authz_context(user_id=44, company_id=1, is_system_admin=False)
    with patch("entities.user.business.service.UserService") as Svc:
        Svc.return_value.read_by_id.return_value = SimpleNamespace(is_agent=False)
        with pytest.raises(IdentityAssertionRefused):
            assert_may_act_as(asserted_user_id=99, what="x")


def test_an_unreadable_user_record_fails_CLOSED(caplog):
    """A database blip is not evidence of delegation rights."""
    set_authz_context(user_id=44, company_id=1, is_system_admin=False)
    with patch("entities.user.business.service.UserService") as Svc:
        Svc.return_value.read_by_id.side_effect = RuntimeError("db down")
        with caplog.at_level(logging.WARNING):
            with pytest.raises(IdentityAssertionRefused):
                assert_may_act_as(asserted_user_id=99, what="x")
    assert any("could not resolve is_agent" in r.getMessage() for r in caplog.records)


def test_no_caller_identity_at_all_is_refused():
    """An unauthenticated or context-less call must not be treated as
    delegation. Every HTTP request populates the ContextVars; anything that
    does not is a CLI or worker, which must declare `system_authz()`."""
    with pytest.raises(IdentityAssertionRefused):
        assert_may_act_as(asserted_user_id=99, what="x")


def test_the_happy_path_does_no_user_lookup():
    """The `is_agent` query must never run for a caller acting as themselves,
    nor for a system admin — otherwise every emailed decision pays for it."""
    set_authz_context(user_id=7, company_id=1, is_system_admin=False)
    with patch("entities.user.business.service.UserService") as Svc:
        assert_may_act_as(asserted_user_id=7, what="x")
        Svc.assert_not_called()

    set_authz_context(user_id=33, company_id=1, is_system_admin=True)
    with patch("entities.user.business.service.UserService") as Svc:
        assert_may_act_as(asserted_user_id=99, what="x")
        Svc.assert_not_called()


def test_string_and_int_user_ids_compare_equal():
    """`match.user_id` comes back from a sproc row; the ContextVar is an int.
    A type mismatch here would refuse every legitimate self-assertion — the
    loudest possible way to get this wrong."""
    set_authz_context(user_id=7, company_id=1, is_system_admin=False)
    assert assert_may_act_as(asserted_user_id="7", what="x") is False


def test_a_null_asserted_reviewer_is_refused_not_waved_through():
    """`reviewer_user_id` can be None if a recipient row has no user. Treating
    None as "matches everyone" would be a fail-open."""
    set_authz_context(user_id=7, company_id=1, is_system_admin=False)
    with pytest.raises(IdentityAssertionRefused):
        assert_may_act_as(asserted_user_id=None, what="x")


# ---------------------------------------------------------------------------
# 2 — both call sites are actually wired (the U-457 lesson)
# ---------------------------------------------------------------------------

CALL_SITES = [
    ("entities.bill.business.service", "BillService", "apply_reviewer_decision"),
    ("entities.contract_labor.business.service", "ContractLaborService", "_apply_decision_to_single_cl"),
]


@pytest.mark.parametrize("module,cls,method", CALL_SITES)
def test_the_service_calls_the_guard(module, cls, method):
    import importlib

    svc_cls = getattr(importlib.import_module(module), cls)
    src = inspect.getsource(getattr(svc_cls, method))
    executable = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "assert_may_act_as(" in executable, (
        f"{cls}.{method} does not bind the asserted reviewer to the caller — "
        "any can_update user can forge an approval in a PM's name"
    )


@pytest.mark.parametrize("module,cls,method", CALL_SITES)
def test_the_guard_runs_BEFORE_anything_is_written(module, cls, method):
    """A refused assertion must leave no Review row, no line-item edit and no
    status change. If the guard ran after the write, the forgery would land and
    only the response would say otherwise."""
    import importlib

    svc_cls = getattr(importlib.import_module(module), cls)
    src = inspect.getsource(getattr(svc_cls, method))
    guard_at = src.index("assert_may_act_as(")
    for writer in ("repo.create(", "_repo.create(", "update_by_", ".create("):
        at = src.find(writer)
        if at != -1:
            assert guard_at < at, (
                f"{cls}.{method} performs {writer!r} before checking the caller"
            )


@pytest.mark.parametrize("module,cls,method", CALL_SITES)
def test_the_guard_binds_the_RESOLVED_reviewer_not_the_raw_email(module, cls, method):
    """`reviewer_email` is attacker-supplied; `reviewer_user_id` is what the
    authorization step resolved it to. Binding the email string would compare
    two things the caller controls."""
    import importlib

    svc_cls = getattr(importlib.import_module(module), cls)
    src = inspect.getsource(getattr(svc_cls, method))
    call = src[src.index("assert_may_act_as("):]
    call = call[:call.index(")\n") + 1]
    assert "reviewer_user_id" in call, f"{cls}.{method} binds the wrong value"
    assert "reviewer_email" not in call


# ---------------------------------------------------------------------------
# 3 — end to end through the Bill service
# ---------------------------------------------------------------------------


def _bill_service_with_recipient(reviewer_user_id: int):
    """A BillService stubbed down to the authorization step."""
    from entities.bill.business.service import BillService

    svc = BillService(repo=MagicMock())
    svc.read_by_public_id = MagicMock(
        return_value=SimpleNamespace(id=5, public_id="pub-5", is_draft=True, status="draft")
    )
    return svc


def test_a_plain_user_forging_a_PM_approval_is_refused_end_to_end():
    """The actual attack, driven through the service."""
    from entities.review.business.recipient_model import ResolvedRecipient

    svc = _bill_service_with_recipient(reviewer_user_id=20)
    pm = ResolvedRecipient(
        user_id=20, firstname="Austin", lastname="P", email="pm@test.com",
        role_name="Project Manager", project_id=1,
    )
    set_authz_context(user_id=7, company_id=1, is_system_admin=False)  # NOT the PM

    with patch("entities.review.business.recipient_service.ReviewRecipientService") as Rec:
        Rec.return_value.resolve_for_bill.return_value = {"to": [pm], "cc": []}
        with pytest.raises(IdentityAssertionRefused):
            svc.apply_reviewer_decision(
                bill_public_id="pub-5",
                decision="approved",
                reviewer_email="pm@test.com",
                sub_cost_code_public_id="scc-1",
            )


def test_the_agent_applying_the_same_decision_is_allowed_end_to_end():
    """The complement — otherwise the guard is satisfied by breaking the
    workflow it was built to protect."""
    from entities.review.business.recipient_model import ResolvedRecipient

    svc = _bill_service_with_recipient(reviewer_user_id=20)
    pm = ResolvedRecipient(
        user_id=20, firstname="Austin", lastname="P", email="pm@test.com",
        role_name="Project Manager", project_id=1,
    )

    with patch("entities.review.business.recipient_service.ReviewRecipientService") as Rec:
        Rec.return_value.resolve_for_bill.return_value = {"to": [pm], "cc": []}
        with system_authz():
            # Fails LATER (on SubCostCode resolution) — the point is that it
            # gets past the identity guard, which a plain user does not.
            with pytest.raises(Exception) as e:
                svc.apply_reviewer_decision(
                    bill_public_id="pub-5",
                    decision="approved",
                    reviewer_email="pm@test.com",
                    sub_cost_code_public_id="scc-1",
                )
    assert not isinstance(e.value, IdentityAssertionRefused), (
        "the agent was refused — U-459 broke the live reviewer-reply automation"
    )


def test_the_PM_applying_their_OWN_decision_is_allowed():
    """A PM acting through the UI as themselves needs no delegation rights."""
    from entities.review.business.recipient_model import ResolvedRecipient

    svc = _bill_service_with_recipient(reviewer_user_id=20)
    pm = ResolvedRecipient(
        user_id=20, firstname="Austin", lastname="P", email="pm@test.com",
        role_name="Project Manager", project_id=1,
    )
    set_authz_context(user_id=20, company_id=1, is_system_admin=False)  # IS the PM

    with patch("entities.review.business.recipient_service.ReviewRecipientService") as Rec:
        Rec.return_value.resolve_for_bill.return_value = {"to": [pm], "cc": []}
        with pytest.raises(Exception) as e:
            svc.apply_reviewer_decision(
                bill_public_id="pub-5",
                decision="approved",
                reviewer_email="pm@test.com",
                sub_cost_code_public_id="scc-1",
            )
    assert not isinstance(e.value, IdentityAssertionRefused)


def test_an_unauthorized_email_still_fails_on_the_ORIGINAL_check():
    """U-459 adds a check; it must not replace the one that was already there.
    An email belonging to nobody on the bill is still refused, and with the
    original message."""
    svc = _bill_service_with_recipient(reviewer_user_id=20)
    with system_authz():
        with patch("entities.review.business.recipient_service.ReviewRecipientService") as Rec:
            Rec.return_value.resolve_for_bill.return_value = {"to": [], "cc": []}
            with pytest.raises(ValueError, match="not an authorized reviewer"):
                svc.apply_reviewer_decision(
                    bill_public_id="pub-5",
                    decision="approved",
                    reviewer_email="stranger@test.com",
                    sub_cost_code_public_id="scc-1",
                )


# ---------------------------------------------------------------------------
# 4 — the audit trail distinguishes the two
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module,cls,method", CALL_SITES)
def test_the_call_site_logs_which_kind_of_assertion_it_was(module, cls, method):
    """"The PM approved this" and "the agent applied the PM's emailed approval"
    produce the SAME Review row, attributed to the PM. The log line is the only
    place that distinction survives — which matters the first time someone
    disputes an approval."""
    import importlib

    svc_cls = getattr(importlib.import_module(module), cls)
    src = inspect.getsource(getattr(svc_cls, method))
    assert "delegated" in src, f"{cls}.{method} discards the delegation flag"
    assert "U-459" in src and "logger.info" in src, (
        f"{cls}.{method} does not record whether the decision was delegated"
    )


def test_a_delegated_assertion_is_logged_with_both_identities(caplog):
    set_authz_context(user_id=33, company_id=1, is_system_admin=True)
    with caplog.at_level(logging.INFO):
        assert_may_act_as(asserted_user_id=99, what="apply a reviewer decision")
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "33" in msg and "99" in msg, (
        "a delegated assertion must record BOTH the caller and the person they "
        "acted for — one identity alone cannot be audited"
    )


# ---------------------------------------------------------------------------
# 5 — the agent identities this guard depends on (Codex, and the Gate-1 risk)
# ---------------------------------------------------------------------------
#
# The guard only works if the agents it is meant to permit are actually flagged.
# I named this as the risk at Gate 1 and it was REAL: the specialist agents
# authenticate as their OWN users (`credentials_key`), not as `claude_agent`,
# and `seed.bill_agent.sql` created its user with only Firstname/Lastname —
# no `IsAgent`, no `IsSystemAdmin`. The Bill reviewer-reply automation would have
# started 403-ing on deploy.
#
# `seed.contract_labor_agent.sql` already set the flag, which is why only the
# Bill path was exposed — and why a per-agent assertion beats eyeballing one seed.

AGENTS_THAT_APPLY_REVIEWER_DECISIONS = [
    # (agent definition module, credentials_key, seed file)
    ("intelligence/agents/bill_specialist/definition.py", "bill_agent",
     "intelligence/persistence/sql/seed.bill_agent.sql"),
    ("intelligence/agents/contract_labor_specialist/definition.py", "contract_labor_agent",
     "intelligence/persistence/sql/seed.contract_labor_agent.sql"),
]


@pytest.mark.parametrize("definition,key,seed", AGENTS_THAT_APPLY_REVIEWER_DECISIONS)
def test_the_agent_identity_is_flagged_IsAgent_in_its_seed(definition, key, seed):
    """Without this the guard refuses the automation it exists to permit."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / seed).read_text()
    executable = "\n".join(l.split("--")[0] for l in text.splitlines())
    assert "IsAgent" in executable, (
        f"{seed} never sets IsAgent — {key} would be refused by U-459's "
        "delegation guard and its reviewer-reply automation would 403"
    )


@pytest.mark.parametrize("definition,key,seed", AGENTS_THAT_APPLY_REVIEWER_DECISIONS)
def test_the_seed_heals_an_EXISTING_unflagged_row(definition, key, seed):
    """Setting the flag only inside the `IF @UserId IS NULL` insert branch fixes
    fresh installs and leaves production broken — the prod rows already exist.
    The seed must carry an unconditional UPDATE."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    executable = "\n".join(
        l.split("--")[0] for l in (root / seed).read_text().splitlines()
    )
    assert re.search(r"UPDATE\s+dbo\.\[User\]", executable, re.I), (
        f"{seed} has no UPDATE — an existing {key} row created without IsAgent "
        "stays unflagged no matter how often the seed is re-run"
    )
    assert "IsAgent = 1" in executable


@pytest.mark.parametrize("definition,key,seed", AGENTS_THAT_APPLY_REVIEWER_DECISIONS)
def test_the_definition_really_uses_that_credentials_key(definition, key, seed):
    """Pins the mapping the two tests above depend on. If a specialist is
    repointed at a different identity, the seed assertions silently stop
    covering the identity actually in use."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert f'credentials_key="{key}"' in (root / definition).read_text(), (
        f"{definition} no longer authenticates as {key} — re-check which seed "
        "provisions the identity that calls apply-reviewer-decision"
    )
