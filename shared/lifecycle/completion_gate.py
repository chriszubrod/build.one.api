"""U-458 — the per-entity completion gate (U-357 LS-00c reader + LS-03a remainder).

Until now the lifecycle has been **descriptive**: every read reports `status` and
`review_status_kind`, and nothing stops a document being completed while a review
is still open. This module is the enforcing half.

**Ships `off` for all four entities.** An `off` gate performs **no database
work**: it returns before resolving the review and before resolving the actor's
permissions. It is not *literally* byte-identical — each completion now builds a
closure, calls the adapter and reads `os.environ` (Codex, 2026-09-15, correcting
an earlier overstatement here) — but it issues no query and takes no decision
that today's code does not. The flags are flipped per entity by
`/em` **without a deploy** (design §9 #1), which is the whole point of reading
them from the environment rather than baking a policy in.

Three values, per `LIFECYCLE_COMPLETION_GATE_{BILL,EXPENSE,BILL_CREDIT,INVOICE}`:

  `off`                 today's behaviour; completion never consults a review.
  `block_open_review`   refuse while the document sits in someone's queue
                        (`submitted`/`in_review`) or was `declined`. `none`
                        (never submitted — the AP fast path the web labels
                        "bypasses review") and `approved` proceed.
  `require_approved`    only `approved` proceeds, EXCEPT the approver fast-path:
                        an actor who may both complete and approve records the
                        Approved row and continues.

Recommended steady state (design §9 #1): Bill + BillCredit `require_approved`,
Invoice `block_open_review` until U-128 unparks `InvoiceEdit`, Expense `off`.
**Not yet chosen** — that is a runtime decision, deliberately not a code one.

Refusals are **422, emphatically not the 409 the design text specifies.** Same
reason U-446b gave for `status_locked`: installed iOS routes 409 into its
per-service CONFLICT path (reload-and-retry, built for optimistic-concurrency
collisions) while treating other 4xx as terminal, so a permanent refusal answered
with 409 makes a queued action loop or get discarded through the wrong path.
Bills are not on the iOS tab bar today, which makes 409 a latent trap rather than
a live one — the kind this workstream keeps finding after the fact. Deviation
approved by `/em` at Gate 1.

Keep this module pure: it imports nothing from `entities/`, so it unit-tests
without a database. The review lookup and the approval write are injected by the
caller as callables.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

from shared.api.errors import ApiError, ErrorCode
from shared.lifecycle.resolver import REVIEW_STATUS_KINDS
from shared.lifecycle.terminal_lock import is_exempt

logger = logging.getLogger(__name__)


# --- the three gate values -------------------------------------------------

GATE_OFF = "off"
GATE_BLOCK_OPEN_REVIEW = "block_open_review"
GATE_REQUIRE_APPROVED = "require_approved"

COMPLETION_GATES = (GATE_OFF, GATE_BLOCK_OPEN_REVIEW, GATE_REQUIRE_APPROVED)

# The review kinds that mean "a human still owes this document a decision".
OPEN_REVIEW_KINDS = ("submitted", "in_review")

# Parent type -> env var. Keys are `ParentType` values verbatim (lowercase,
# underscored) rather than a second spelling invented here — `entities/review`
# already speaks this vocabulary everywhere, and a module that quietly uses
# PascalCase would look right and match nothing. `tests/test_u458_*` asserts
# these keys ARE the ParentType members, so the two cannot drift.
ENV_VAR_BY_PARENT: dict[str, str] = {
    "bill": "LIFECYCLE_COMPLETION_GATE_BILL",
    "expense": "LIFECYCLE_COMPLETION_GATE_EXPENSE",
    "bill_credit": "LIFECYCLE_COMPLETION_GATE_BILL_CREDIT",
    "invoice": "LIFECYCLE_COMPLETION_GATE_INVOICE",
}


class CompletionRefused(ApiError):
    """The gate refused a completion. 422 + a machine-readable `error_code`."""

    def __init__(self, message: str, *, error_code: str):
        super().__init__(status_code=422, detail=message, error_code=error_code)


def completion_gate_for(parent_type: str) -> str:
    """The configured gate for one entity, or `off`.

    **An unrecognised value falls back to `off` and logs a WARNING** rather than
    to the most restrictive value. The two failure modes are not symmetric: a
    typo that wrongly enables `require_approved` stops AP from paying anything
    (a business stoppage, and one that looks like a bug in completion rather
    than a bad env var), while a typo that wrongly disables the gate leaves
    exactly the behaviour that shipped for years. Falling back to `off` also
    keeps this consistent with "ships off". The WARNING is the compensating
    control — `tests/test_u458_completion_gate.py` pins that it fires.
    """
    env_var = ENV_VAR_BY_PARENT.get(parent_type)
    if env_var is None:
        # Not a gated entity (ContractLabor, TimeEntry, …). Not an error: those
        # carry their gate in their own pipeline (design §4.2).
        return GATE_OFF

    raw = (os.environ.get(env_var) or "").strip().lower()
    if not raw:
        return GATE_OFF
    if raw in COMPLETION_GATES:
        return raw

    # Unreachable in a correctly-booted process: `assert_completion_gates_valid`
    # runs at startup and refuses to serve with a bad value, so nothing invalid
    # survives to request time. Kept as defence for direct callers (tests, CLI,
    # a worker that skipped startup) and deliberately falls back to `off` rather
    # than halting AP mid-request over a config fault.
    logger.warning(
        "Invalid %s=%r; falling back to %r. Valid values: %s",
        env_var, raw, GATE_OFF, ", ".join(COMPLETION_GATES),
    )
    return GATE_OFF


class InvalidCompletionGateConfig(RuntimeError):
    """A `LIFECYCLE_COMPLETION_GATE_*` env var holds a value we do not recognise."""


def assert_completion_gates_valid() -> None:
    """Validate all four flags at STARTUP; refuse to boot on a bad one.

    The earlier design logged a warning and fell back to `off` at request time.
    Codex rejected that (2026-09-15) on the grounds that a warning is neither
    blocking nor necessarily alerted, so a typo silently disables a money
    control — and it was right that "we logged it" is not a control.

    Validating at boot is strictly better than either alternative, because it
    separates the two failure modes instead of trading them off:

      * a typo can never reach production serving traffic — the container does
        not start, which is loud, immediate, and attributable to the deploy that
        caused it;
      * and no in-flight completion is ever refused because of a config fault,
        which is what request-time fail-closed would do (a single typo halting
        AP with an error that reads like a completion bug).

    Called from `app.py` at import time.
    """
    bad = {}
    for parent, env_var in ENV_VAR_BY_PARENT.items():
        raw = (os.environ.get(env_var) or "").strip().lower()
        if raw and raw not in COMPLETION_GATES:
            bad[env_var] = os.environ.get(env_var)
    if bad:
        detail = "; ".join(f"{k}={v!r}" for k, v in sorted(bad.items()))
        raise InvalidCompletionGateConfig(
            f"Invalid completion gate configuration: {detail}. "
            f"Each must be one of: {', '.join(COMPLETION_GATES)} (or unset, meaning "
            f"{GATE_OFF!r}). Refusing to start rather than serve traffic with a "
            "lifecycle gate in an unknown state."
        )

    _warn_if_require_approved_is_premature()


# Everything that can write an `approved` Review today WITHOUT `can_approve`.
# `require_approved` asks "is there an approved review?", not "did an approver
# approve it" — so the gate is only ever as strong as the weakest writer of that
# row. Codex established this over three review rounds and it is the correct
# frame: hardening the new fast-path while these stand does not make the gate
# enforcing.
#
# NONE of these were introduced by U-458; all pre-date it. They are listed here,
# at the moment an operator turns the flag on, because that is the only moment
# the distinction matters.
_APPROVAL_WRITERS_NOT_YET_GATED = (
    "POST /advance/review/* requires only `can_submit`, and advancing from the "
    "last intermediate status lands on the FINAL (approved) one — design "
    "\u00a74.2 specifies `can_approve` for that transition; unification is LS-06c",
    "POST /bill/{id}/apply-reviewer-decision requires only `can_update` and "
    "takes `reviewer_email` from the request body, writing an approval "
    "attributed to that person with nothing binding the authenticated caller "
    "to the asserted reviewer",
    "POST /expense/{id}/apply-reviewer-decision requires only `can_update` "
    "(never `can_approve`), takes `reviewer_email` from the request body, "
    "and any delegated actor — system context, system admin, or an `IsAgent` "
    "user — may write an approval attributed to a PM/Owner",
    "the gate is a PREFLIGHT, not a write boundary: completion is enqueued and "
    "finalized later without re-checking, and CompletionJob reclaim re-drives "
    "under system_authz — LS-02a moves enforcement into the write transaction",
)


def _warn_if_require_approved_is_premature() -> None:
    """Tell the operator what `require_approved` does NOT yet guarantee.

    This flag is designed to be flipped without a deploy, which means nobody
    reviews code at the moment it takes effect. A WARNING naming the unclosed
    approval writers is the only thing that reaches the person making that
    decision. Deliberately NOT a refusal: whether to run a partial control is
    an operational call, not one this module gets to make.
    """
    live = sorted(
        env_var for parent, env_var in ENV_VAR_BY_PARENT.items()
        if (os.environ.get(env_var) or "").strip().lower() == GATE_REQUIRE_APPROVED
    )
    if not live:
        return
    logger.warning(
        "%s set to %r. This gate checks that an APPROVED REVIEW EXISTS, not "
        "that an approver created it, and these paths can still produce one "
        "without `can_approve`: %s",
        ", ".join(live), GATE_REQUIRE_APPROVED,
        " | ".join(_APPROVAL_WRITERS_NOT_YET_GATED),
    )


def evaluate_completion(
    *,
    parent_type: str,
    review_kind: Optional[str],
    actor_can_approve: bool = False,
) -> bool:
    """Decide whether a completion may proceed under `parent_type`'s gate.

    Returns **True when the caller must record an Approved Review** before
    completing (the approver fast-path), False when it may simply proceed.
    Raises `CompletionRefused` otherwise.

    `review_kind` is the document's CURRENT kind — `none` when it has never been
    submitted. Pure: the caller resolves it.
    """
    gate = completion_gate_for(parent_type)
    if gate == GATE_OFF:
        return False

    # `None` means "no review row at all" -> never submitted. Anything else is a
    # value the database gave us, and a value we do not recognise must REFUSE
    # rather than degrade to `none` (Codex P2, 2026-09-15). Collapsing garbage
    # into "never submitted" is fail-OPEN: a malformed row on a document sitting
    # in someone's queue would complete straight through the gate.
    kind = "none" if review_kind is None else str(review_kind).strip().lower()
    if kind not in REVIEW_STATUS_KINDS:
        raise CompletionRefused(
            f"This {_label(parent_type)} carries an unrecognised review state "
            f"({review_kind!r}); refusing to complete it. This is a data fault, "
            "not a workflow state — check the review row.",
            error_code=ErrorCode.REVIEW_STATE_UNKNOWN,
        )

    if gate == GATE_BLOCK_OPEN_REVIEW:
        if kind in OPEN_REVIEW_KINDS:
            raise CompletionRefused(
                f"This {_label(parent_type)} is still in review ({kind}) and cannot be "
                "completed yet. Approve or decline it first.",
                error_code=ErrorCode.REVIEW_OPEN,
            )
        if kind == "declined":
            raise CompletionRefused(
                f"This {_label(parent_type)} was declined and cannot be completed. "
                "Resolve the decline and resubmit it for review.",
                error_code=ErrorCode.REVIEW_DECLINED,
            )
        # `none` (never submitted) and `approved` proceed.
        return False

    if gate == GATE_REQUIRE_APPROVED:
        if kind == "approved":
            return False
        if kind == "declined":
            # Refused HERE, before the fast-path, for two reasons. A decline is a
            # decision a colleague made, not an open question, so an approver
            # must go through resubmit rather than silently overturning it. And
            # the previous shape returned "fast-path" for declined, whereupon
            # `build_fast_path_approval_payload` raised `ReviewTransitionError`
            # — which nothing on this path translates, so the caller got a 500
            # instead of the terminal 422 this gate promises (Codex P1).
            raise CompletionRefused(
                f"This {_label(parent_type)} was declined and cannot be completed. "
                "Resolve the decline and resubmit it for review.",
                error_code=ErrorCode.REVIEW_DECLINED,
            )
        if actor_can_approve:
            # The approver fast-path: this actor could approve it in a separate
            # click, so requiring that click adds no control — it only adds a
            # step. Record the approval under THEM, then complete.
            return True
        raise CompletionRefused(
            f"This {_label(parent_type)} has not been approved "
            f"(current review state: {kind}) and cannot be completed.",
            error_code=ErrorCode.REVIEW_NOT_APPROVED,
        )

    # Unreachable: completion_gate_for only ever returns a member of
    # COMPLETION_GATES. Refusing to guess beats falling through to "allow".
    raise AssertionError(f"unhandled completion gate {gate!r}")


def enforce_completion_gate(
    parent_type: str,
    *,
    resolve_review: Callable[[], Any],
    record_approval: Optional[Callable[[], None]] = None,
    actor_can_approve: bool = False,
    exempt: bool = False,
) -> None:
    """Router-facing wrapper: resolve, decide, and run the fast-path write.

    Ordering is deliberate and load-bearing:

    1. **Read the gate first.** When it is `off` we return before calling
       `resolve_review`, so an `off` gate adds no database round trip to any
       completion. "Ships off" has to mean no cost, not just no refusal.
    2. **Exempt callers skip entirely.** `system_authz` covers the outbox
       workers, CLI sync and the drain endpoint — QBO-origin births would
       otherwise be refused for never having been reviewed by a human who was
       never involved. Note this is the opposite of the SQL rule "lock
       unconditionally, refuse conditionally": there, gating the LOCK on an
       exemption makes exempt writers invisible to other transactions. Here
       there is no lock to take and nothing to be invisible to — the gate is a
       pure policy read.
    3. **Then resolve and decide.**

    `record_approval` is required only when `require_approved` may fast-path;
    passing None there is a programming error and raises rather than silently
    completing an unapproved document.
    """
    if completion_gate_for(parent_type) == GATE_OFF:
        return
    if is_exempt(exempt):
        return

    review = resolve_review()
    # An ABSENT row means "never submitted". A row that EXISTS but whose kind is
    # missing is a data fault, and must not be laundered into the same answer —
    # `evaluate_completion` refuses anything it does not recognise, and that only
    # works if this layer keeps the two cases distinct (Codex P2).
    if review is None:
        kind = None
    else:
        kind = getattr(review, "review_kind", None)
        if kind is None:
            kind = "__missing__"  # unrecognised on purpose: forces the refusal

    needs_approval = evaluate_completion(
        parent_type=parent_type,
        review_kind=kind,
        actor_can_approve=actor_can_approve,
    )
    if not needs_approval:
        return

    if record_approval is None:
        raise AssertionError(
            f"{parent_type} completion needs a fast-path approval but no "
            "record_approval was supplied — refusing to complete an unapproved "
            "document"
        )
    record_approval()


def _label(parent_type: str) -> str:
    """`bill_credit` -> `bill credit`, for a message a human reads."""
    return parent_type.replace("_", " ")
