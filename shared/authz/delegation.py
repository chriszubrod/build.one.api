"""U-459 — who may act in someone else's name.

Two routes let a caller assert a DIFFERENT person's identity and write a record
attributed to them:

    POST /bill/{id}/apply-reviewer-decision
    POST /contract-labor/apply-reviewer-decision

Both exist for the email-reply review workflow: a PM or Owner replies to a
review notification, the `bill_specialist` / `contract_labor_specialist` agent
parses the reply, and applies that person's decision on their behalf. Asserting
someone else's identity is the POINT of these routes, not an oversight.

What WAS an oversight is that nothing checked who was asking. Both routes are
gated on `can_update` for their module, and both authorized `reviewer_email`
only by matching it against the document's PM/Owner recipients — never against
the authenticated caller. So any user with edit rights could POST an approval
attributed to their Project Manager. Under the U-458 `require_approved` gate
that forged approval is precisely what unlocks completion, which is how this
surfaced (Codex, round 3) — but the forgery path is real on its own and predates
the gate entirely.

The rule here: **you may only assert your own identity, unless you are a
delegated actor.** Delegated actors are the agent fleet and non-HTTP system
contexts — the callers this workflow was built for.

Deliberately NOT in scope: verifying that the EMAIL really came from the person
whose decision is being applied. The agent asserts what it parsed from a reply,
and `reviewer_email_message_public_id` carries the provenance chain, but nothing
enforces it. That is a separate unit — this one binds the API caller, which is
the half that lets an ordinary authenticated user forge.
"""

from __future__ import annotations

import logging
from typing import Optional

from shared.api.errors import ApiError, ErrorCode
from shared.authz.context import current_is_system_admin, current_user_id
from shared.lifecycle.terminal_lock import is_system_caller

logger = logging.getLogger(__name__)


class IdentityAssertionRefused(ApiError):
    """A caller tried to act in someone else's name without delegation rights."""

    def __init__(self, message: str):
        super().__init__(
            status_code=403, detail=message, error_code=ErrorCode.IDENTITY_ASSERTION_REFUSED
        )


def _is_delegated_actor(caller_id: Optional[int]) -> bool:
    """Whether this caller may act in someone else's name.

    Ordered cheapest-first, and the DB lookup is LAST on purpose: it runs only
    when the caller is neither a system context nor a system admin — i.e. only
    on a request that is otherwise about to be REFUSED. Normal traffic never
    pays for it.

    The `is_agent` fallback is belt-and-braces. Claude Agent (user 33) is
    recorded as `IsSystemAdmin = 1`, so it is already covered by the check
    above — but this workflow is live automation, and a guard that breaks the
    agent fleet because one flag was not what the notes said is worse than one
    extra query on a path that was going to raise anyway.
    """
    if is_system_caller():
        return True
    if current_is_system_admin.get():
        return True
    if caller_id is None:
        return False
    try:
        from entities.user.business.service import UserService

        user = UserService().read_by_id(id=caller_id)
        return bool(getattr(user, "is_agent", False))
    except Exception as error:  # pragma: no cover - defensive
        # Fail CLOSED. An unreadable user record is not evidence of delegation
        # rights, and this path only runs for a caller already asserting
        # someone else's identity.
        logger.warning(
            "U-459: could not resolve is_agent for user_id=%s while checking a "
            "delegated identity assertion; refusing: %s", caller_id, error,
        )
        return False


def assert_may_act_as(
    *,
    asserted_user_id: Optional[int],
    what: str,
    caller_user_id: Optional[int] = None,
) -> bool:
    """Refuse unless the caller IS `asserted_user_id`, or may act for others.

    `asserted_user_id` is the identity the record will be attributed to — the
    caller has already resolved it (both call sites match `reviewer_email`
    against the document's authorized recipients and keep `match.user_id`), so
    this adds no lookup on the happy path.

    Returns True when the action is DELEGATED (the caller is acting for someone
    else), False when the caller is acting as themselves. Call sites use that to
    log the two identities distinctly, so the audit trail can tell "the PM
    approved this" from "the agent applied the PM's emailed approval".

    Raises `IdentityAssertionRefused` (403) otherwise.
    """
    # Defaults to the per-request ContextVar, so this is callable from the
    # SERVICE layer without threading `current_user` through two signatures.
    # The routers do not have to change at all, which keeps the guard next to
    # the `reviewer_user_id` it binds rather than a layer away from it.
    caller_id = caller_user_id if caller_user_id is not None else current_user_id.get()

    if asserted_user_id is not None and caller_id is not None and int(asserted_user_id) == int(caller_id):
        return False  # acting as themselves — always fine

    if _is_delegated_actor(caller_id):
        logger.info(
            "U-459: delegated identity assertion — caller user_id=%s acting as "
            "user_id=%s for %s",
            caller_id, asserted_user_id, what,
        )
        return True

    raise IdentityAssertionRefused(
        f"You may not {what} on another person's behalf. This request asserts a "
        "different reviewer than the authenticated user, which only the review "
        "automation may do."
    )
