"""Shared reviewer-decision engine for Bill and Expense (U-755).

Two entry points, one per decision channel:

* ``apply_emailed`` — Path A. A Project Manager / Owner's emailed reply, applied
  by the agent (or the PM self-serve). The sender email must match the review
  recipient envelope, the reply must be provenance-bound to the review thread,
  and the caller must be allowed to act as that reviewer (U-459).
* ``apply_transactional`` — Path B. The iOS reviewer queue's edit-and-decide
  (U-541 Bill / U-551b Expense). The authenticated caller IS the reviewer — no
  delegation — and the call is idempotent on ``idempotency_key``.

Both write the optional line recode and the Review insert on ONE connection
(U-564). The entity services keep their public methods and delegate here with a
``ReviewerDecisionSpec`` built per call, so the collaborators the spec carries
(``get_connection``, ``SubCostCodeService``, ``assert_may_act_as``, the
logger) are read from the ENTITY service module at call time and the
long-standing patch points ``entities.{bill,expense}.business.service.*`` keep
taking effect.

Path A codes the ONE line of a single-line parent and refuses anything else
(zero or several lines), checked BEFORE the sub cost code lookup — for both
entities (U-760 retired Bill's old "code ``line_items[0]``, holding-check the
others" policy; a reviewer decision never stamps one cost code over a split).

The two entities still differ on Path A in two places, each kept as an explicit
switch rather than silently unified:

* ``emailed_recount_under_lock`` — Expense re-counts its lines under the lock
  (U-564) and refuses a line added since the fast path; Bill does not.
* ``emailed_fresh_status_service_for_readback`` — Bill reads the new status name
  back through a fresh ``ReviewStatusService``; Expense reuses the one that
  resolved the target status.

Also preserved as-is on Path A: an approval whose sub cost code row has
``id=None`` reaches ``int(None)`` (TypeError) — Path B refuses it as not found.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from entities.review.business.decision_errors import ReviewDecisionApplyError
from shared.api.errors import ErrorCode
from shared.authz import current_user_id
from shared.database import DatabaseConcurrencyError, DatabaseConstraintError
from shared.db_constraints import UNIQUE
from shared.lifecycle.completion_gate import HoldingCodeRefused
from shared.lifecycle.terminal_lock import StatusLockedError


@dataclass(frozen=True)
class ReviewerDecisionSpec:
    """Everything that differs between the Bill and the Expense decision paths."""

    # Naming: `parent` is the lowercase noun ("bill") used in error templates, the
    # U-459 log line and prose, and from which `fk_kwarg` / `payload_key` derive;
    # `entity_type` ("Bill") leads messages and is the provenance entity type.
    parent: str
    entity_type: str
    provenance_what: str

    # Entity hooks.
    read_parent: Callable[[str], Any]
    resolve_recipients: Callable[[Any, int], dict]
    line_service_factory: Callable[[], Any]
    read_lines: Callable[[Any, int], list]
    count_lines_for_update: Callable[[Any, int, Any], int]
    read_lines_in_transaction: Callable[[Any, int, Any], list]
    lock_current_review: Callable[[Any, int, Any], Any]
    apply_error: Callable[..., ReviewDecisionApplyError]

    # Collaborators read from the entity service module at call time.
    get_connection: Callable[[], Any]
    sub_cost_code_service: Callable[[], Any]
    assert_may_act_as: Callable[..., bool]
    logger: logging.Logger

    # Path A drift switches (see module docstring).
    emailed_recount_under_lock: bool
    emailed_fresh_status_service_for_readback: bool

    @property
    def fk_kwarg(self) -> str:
        """The Review FK column (`bill_id`) and the idempotency-ownership attribute."""
        return f"{self.parent}_id"

    @property
    def payload_key(self) -> str:
        """The echoed public id in the response payload (`bill_public_id`)."""
        return f"{self.parent}_public_id"


def _validate_decision(decision: str) -> None:
    if decision not in ("approved", "rejected"):
        raise ValueError(
            f"decision must be 'approved' or 'rejected'; got '{decision}'"
        )


def _read_parent_or_raise(spec: ReviewerDecisionSpec, public_id: str):
    parent = spec.read_parent(public_id)
    if parent is None or parent.id is None:
        raise ValueError(
            f"{spec.entity_type} with public_id '{public_id}' not found."
        )
    return parent


def _resolve_approval_scc(
    spec: ReviewerDecisionSpec,
    sub_cost_code_public_id: Optional[str],
    *,
    id_none_is_missing: bool,
) -> tuple[Any, int]:
    """Resolve the approval's sub cost code -> (scc, int id).

    Path A checks only the row (`id_none_is_missing=False`, so a row with id None
    reaches `int(None)`); Path B also refuses an id-less row as not found."""
    if not sub_cost_code_public_id:
        raise ValueError(
            "sub_cost_code_public_id is required when decision='approved'."
        )
    scc = spec.sub_cost_code_service().read_by_public_id(
        public_id=sub_cost_code_public_id
    )
    if scc is None or (id_none_is_missing and scc.id is None):
        raise ValueError(
            f"SubCostCode with public_id '{sub_cost_code_public_id}' not found."
        )
    return scc, int(scc.id)


def _holding_code_refusal(spec: ReviewerDecisionSpec) -> HoldingCodeRefused:
    return HoldingCodeRefused(
        f"This {spec.parent} cannot be approved while coded to the suspense "
        "sub cost code. Recode those lines first."
    )


def _refuse_unless_one_line(
    spec: ReviewerDecisionSpec, public_id: str, line_count: int
) -> None:
    """Emailed path: code the ONE line; refuse anything else.

    Shared by the fast path and the under-lock recheck (U-564), so both refusals
    read the same."""
    if line_count == 1:
        return
    if line_count == 0:
        raise ValueError(
            f"{spec.entity_type} {public_id} has no line items; a reviewer "
            "decision cannot be auto-applied."
        )
    raise ValueError(
        f"{spec.entity_type} {public_id} has {line_count} line items; "
        "a reviewer decision cannot be auto-applied to a multi-line "
        f"{spec.parent} (would stamp one cost code over a manual split)."
    )


def _no_approved_status() -> ValueError:
    return ValueError(
        "No terminal non-declined ReviewStatus configured "
        "(expected one with IsFinal=true AND IsDeclined=false)."
    )


def _no_declined_status() -> ValueError:
    return ValueError(
        "No declined ReviewStatus configured (expected one with IsDeclined=true)."
    )


def _all_recipients(spec: ReviewerDecisionSpec, parent_id: int) -> list:
    """The review recipient envelope, TO then CC."""
    from entities.review.business.recipient_service import ReviewRecipientService

    envelope = spec.resolve_recipients(ReviewRecipientService(), parent_id)
    return envelope["to"] + envelope["cc"]


def _decision_payload(
    spec: ReviewerDecisionSpec,
    public_id: str,
    *,
    decision_applied: str,
    review_status: Optional[str],
    reviewer_user_id,
) -> dict:
    return {
        "decision_applied": decision_applied,
        "review_status": review_status,
        "reviewer_user_id": reviewer_user_id,
        "is_draft": True,
        spec.payload_key: public_id,
    }


def _create_review_kwargs(spec: ReviewerDecisionSpec, parent_id: int) -> dict:
    fks = {"bill_id": None, "expense_id": None, "bill_credit_id": None, "invoice_id": None}
    fks[spec.fk_kwarg] = parent_id
    return fks


# ---------------------------------------------------------------------------
# Path A — emailed decision
# ---------------------------------------------------------------------------


def apply_emailed(
    spec: ReviewerDecisionSpec,
    *,
    public_id: str,
    decision: str,
    reviewer_email: str,
    sub_cost_code_public_id: Optional[str] = None,
    description: Optional[str] = None,
    raw_reply_text: Optional[str] = None,
    reviewer_email_message_public_id: Optional[str] = None,
) -> dict:
    """Apply a PM / Owner's emailed review decision (see the entity docstrings)."""
    from entities.review_status.business.service import ReviewStatusService
    from entities.review.persistence.repo import ReviewRepository

    _validate_decision(decision)

    parent = _read_parent_or_raise(spec, public_id)

    # Draft-only guard — once Completed, the human has the final word.
    if not bool(parent.is_draft):
        raise ValueError(
            f"{spec.entity_type} {public_id} is no longer a draft "
            "(Complete already pressed); reviewer decisions cannot be "
            "applied. The human must edit directly."
        )

    # Authorization: reviewer_email must match a PM/Owner recipient.
    all_recipients = _all_recipients(spec, parent.id)
    normalized_email = (reviewer_email or "").strip().lower()
    match = next(
        (
            r for r in all_recipients
            if r.email and r.email.strip().lower() == normalized_email
        ),
        None,
    )
    if match is None:
        raise ValueError(
            f"Sender '{reviewer_email}' is not an authorized reviewer for "
            f"this {spec.parent} (must be Project Manager or Owner on the project)."
        )
    reviewer_user_id = match.user_id

    from shared.authz.provenance import bind_reviewer_reply_to_review_thread

    bound_reply = bind_reviewer_reply_to_review_thread(
        entity_type=spec.entity_type,
        entity_public_id=public_id,
        reviewer_email=reviewer_email,
        reviewer_email_message_public_id=reviewer_email_message_public_id,
        what=spec.provenance_what,
    )

    # U-459 — bind the asserted reviewer to the authenticated caller. Everything
    # above authorizes the EMAIL; this authorizes the CALLER. Delegation is
    # legitimate (it is how the agent applies an emailed reply), so the rule is
    # "act as yourself unless you are a delegated actor".
    delegated = spec.assert_may_act_as(
        asserted_user_id=reviewer_user_id,
        what="apply a reviewer decision",
    )
    spec.logger.info(
        "U-459: reviewer decision on %s applied %s (reviewer user_id=%s)",
        spec.parent,
        "BY THE AGENT ON THEIR BEHALF" if delegated else "BY THE REVIEWER THEMSELVES",
        reviewer_user_id,
    )

    line_service = spec.line_service_factory()
    scc_id: Optional[int] = None
    target_line = None
    if decision == "approved":
        line_items = spec.read_lines(line_service, parent.id)
        _refuse_unless_one_line(spec, public_id, len(line_items))
        target_line = line_items[0]
        scc, scc_id = _resolve_approval_scc(
            spec, sub_cost_code_public_id, id_none_is_missing=False
        )
        if getattr(scc, "is_holding_code", False):
            raise _holding_code_refusal(spec)

    # A PM approval IS the approval: jump straight to the terminal non-declined
    # status (or to declined); every authorized reply is authoritative and the
    # latest wins. Review rows are insert-only — a new audit row, never an update.
    comments = (raw_reply_text or "").strip() or None
    review_status_service = ReviewStatusService()
    review_statuses = review_status_service.read_all()
    if decision == "approved":
        target = next(
            (s for s in review_statuses if s.is_final and not s.is_declined),
            None,
        )
        if target is None:
            raise _no_approved_status()
    else:  # rejected
        target = next(
            (s for s in review_statuses if s.is_declined),
            None,
        )
        if target is None:
            raise _no_declined_status()

    email_message_id: Optional[int] = bound_reply.id
    review_repo = ReviewRepository()
    # Authenticated caller (agent vs PM self-serve) — user_id stays the reviewer.
    actor_user_id = current_user_id.get()

    with spec.get_connection() as conn:
        spec.lock_current_review(review_repo, parent.id, conn)
        if decision == "approved":
            if spec.emailed_recount_under_lock:
                # Re-checked under the lock: a line added since the fast path above.
                line_count = spec.count_lines_for_update(line_service, parent.id, conn)
                _refuse_unless_one_line(spec, public_id, line_count)
            line_service.update_coding_in_transaction(
                line_item=target_line,
                row_version=target_line.row_version,
                sub_cost_code_id=scc_id,
                description=description,
                conn=conn,
            )
        new_review = review_repo.create(
            review_status_id=target.id,
            user_id=reviewer_user_id,
            comments=comments,
            **_create_review_kwargs(spec, parent.id),
            email_message_id=email_message_id,
            created_by_user_id=actor_user_id,
            conn=conn,
        )

    # Look up the new status name for the response payload.
    if new_review.review_status_id:
        readback = (
            ReviewStatusService()
            if spec.emailed_fresh_status_service_for_readback
            else review_status_service
        )
        rs = readback.read_by_id(id=new_review.review_status_id)
    else:
        rs = None
    return _decision_payload(
        spec,
        public_id,
        decision_applied=decision,
        review_status=rs.name if rs else None,
        reviewer_user_id=reviewer_user_id,
    )


# ---------------------------------------------------------------------------
# Path B — transactional edit-and-decide (iOS reviewer queue)
# ---------------------------------------------------------------------------


def _payload_from_review(
    spec: ReviewerDecisionSpec,
    public_id: str,
    review,
    *,
    replayed: bool,
    review_status_name: Optional[str] = None,
) -> dict:
    from entities.review_status.business.service import ReviewStatusService

    if review_status_name is None:
        review_status_name = getattr(review, "status_name", None)
        if review_status_name is None and review.review_status_id:
            rs = ReviewStatusService().read_by_id(id=review.review_status_id)
            review_status_name = rs.name if rs else None
    applied = (
        "rejected"
        if (review.review_kind or "").lower() == "declined"
        else "approved"
    )
    return {
        **_decision_payload(
            spec,
            public_id,
            decision_applied=applied,
            review_status=review_status_name,
            reviewer_user_id=review.user_id,
        ),
        "review_public_id": str(review.public_id),
        "replayed": replayed,
    }


def _assert_expected_review_current(
    spec: ReviewerDecisionSpec, expected_review_public_id: Optional[str], current
) -> None:
    if expected_review_public_id is None:
        if current is not None:
            raise spec.apply_error(ErrorCode.REVIEW_STATE_STALE)
        return
    if current is None or str(current.public_id).upper() != str(
        expected_review_public_id
    ).upper():
        raise spec.apply_error(ErrorCode.REVIEW_STATE_STALE)


def apply_transactional(
    spec: ReviewerDecisionSpec,
    *,
    public_id: str,
    decision: str,
    sub_cost_code_public_id: Optional[str],
    description: Optional[str],
    idempotency_key: str,
    line_row_version: str,
    expected_review_public_id: Optional[str],
    caller_user_id: int,
) -> dict:
    """Transactional edit-and-decide. The authenticated caller IS the reviewer —
    no delegation. One transaction covers the optional line recode and the
    Review insert."""
    from entities.review.persistence.repo import ReviewRepository
    from entities.review_status.business.service import ReviewStatusService

    _validate_decision(decision)

    parent = _read_parent_or_raise(spec, public_id)

    if not any(r.user_id == caller_user_id for r in _all_recipients(spec, parent.id)):
        raise spec.apply_error(ErrorCode.NOT_A_REVIEWER, status_code=403)

    # U-632: resolve the SCC and refuse the suspense code (00.02) BEFORE any repo
    # read, lock or write (trunk order; the idempotent replay follows).
    scc_id: Optional[int] = None
    if decision == "approved":
        scc, scc_id = _resolve_approval_scc(
            spec, sub_cost_code_public_id, id_none_is_missing=True
        )
        if getattr(scc, "is_holding_code", False):
            raise _holding_code_refusal(spec)

    review_repo = ReviewRepository()

    def _replay(existing) -> dict:
        # The key must belong to THIS parent; a reuse across parents is a conflict.
        if getattr(existing, spec.fk_kwarg) != parent.id:
            raise spec.apply_error(ErrorCode.IDEMPOTENCY_KEY_CONFLICT)
        return _payload_from_review(spec, public_id, existing, replayed=True)

    def _require_sole_line(count: int) -> None:
        if count == 1:
            return
        if count == 0:
            raise ValueError(
                f"{spec.entity_type} {public_id} has no line items; a reviewer "
                "decision cannot be applied."
            )
        raise spec.apply_error(ErrorCode.MULTI_LINE_NOT_SUPPORTED, status_code=422)

    pre_tx_idem = review_repo.read_by_idempotency_key(idempotency_key)
    if pre_tx_idem is not None:
        return _replay(pre_tx_idem)

    line_service = spec.line_service_factory()
    # Fast path + IndexError guard; the multi-line refusal stays here so no
    # UPDLOCK is taken on the parent/lines before refusing (U-541).
    _require_sole_line(len(spec.read_lines(line_service, parent.id)))

    rs_service = ReviewStatusService()
    if decision == "approved":
        target = rs_service.get_approved_status()
        if target is None:
            raise _no_approved_status()
    else:
        declined_statuses = rs_service.get_declined_statuses()
        if not declined_statuses:
            raise _no_declined_status()
        target = declined_statuses[0]

    try:
        with spec.get_connection() as conn:
            current = spec.lock_current_review(review_repo, parent.id, conn)
            existing = review_repo.read_by_idempotency_key(idempotency_key, conn=conn)
            if existing is not None:
                return _replay(existing)
            _assert_expected_review_current(spec, expected_review_public_id, current)
            _require_sole_line(spec.count_lines_for_update(line_service, parent.id, conn))
            locked_lines = spec.read_lines_in_transaction(line_service, parent.id, conn)
            _require_sole_line(len(locked_lines))
            locked_line = locked_lines[0]
            if locked_line.row_version != line_row_version:
                raise spec.apply_error(ErrorCode.LINE_ROW_VERSION_STALE)
            if decision == "approved":
                try:
                    line_service.update_coding_in_transaction(
                        line_item=locked_line,
                        row_version=line_row_version,
                        sub_cost_code_id=scc_id,
                        description=description,
                        conn=conn,
                    )
                except DatabaseConcurrencyError as exc:
                    raise spec.apply_error(ErrorCode.LINE_ROW_VERSION_STALE) from exc
            new_review = review_repo.create(
                review_status_id=target.id,
                user_id=caller_user_id,
                comments=None,
                **_create_review_kwargs(spec, parent.id),
                email_message_id=None,
                created_by_user_id=caller_user_id,
                allow_terminal_parent=False,
                idempotency_key=idempotency_key,
                conn=conn,
            )
    except ReviewDecisionApplyError:
        raise
    except DatabaseConstraintError as dup:
        if dup.violation.kind == UNIQUE and idempotency_key:
            existing = review_repo.read_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return _replay(existing)
        raise
    except StatusLockedError as exc:
        raise spec.apply_error(
            ErrorCode.STATUS_LOCKED,
            status_code=422,
            detail=str(exc),
        ) from exc

    return _payload_from_review(
        spec, public_id, new_review, replayed=False, review_status_name=target.name
    )
