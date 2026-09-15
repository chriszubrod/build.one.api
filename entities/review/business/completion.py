"""U-458 — binds the pure completion gate to the review machinery.

`shared/lifecycle/completion_gate.py` deliberately knows nothing about
`entities/` — that is what lets it be unit-tested without a database. This
module is the one place that supplies the three things it cannot resolve for
itself: the document's current review, whether the actor may approve, and how to
record an approval.

One adapter, four callers. The alternative — wiring the gate by hand in each of
the four `/complete/*` routes — is the shape that produced this workstream's
recurring "fixed in three places, missed the fourth" defects (U-455's batch
reader, U-457's alternate-lookup routes).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from shared.api.auth_user import resolve_user_id
from shared.lifecycle.completion_gate import (
    GATE_OFF,
    completion_gate_for,
    enforce_completion_gate,
)
from shared.rbac import has_module_permission

logger = logging.getLogger(__name__)


def gate_completion(
    *,
    parent_type: str,
    parent_public_id: str,
    module_name: str,
    current_user: dict,
    resolve_review: Callable[[], Any],
    exempt: bool = False,
) -> None:
    """Enforce `parent_type`'s completion gate, or raise `CompletionRefused` (422).

    Call it in a `/complete/*` route AFTER the 404 and the already-completed
    check, and BEFORE the job is enqueued — a refused completion must not leave
    a CompletionJob row behind.

    The early return on `off` is load-bearing, not an optimisation: it is what
    makes "ships off" mean *no behaviour change at all*, including no extra
    review lookup and no permission resolution on every completion in the system.
    """
    if completion_gate_for(parent_type) == GATE_OFF:
        return

    def _record_approval() -> None:
        # Imported here, not at module scope: the review router imports
        # ProcessEngine, which imports the service registry, which imports the
        # entity services this module is called FROM. A top-level import is a
        # cycle.
        from entities.review.api.router import _execute_create
        from entities.review.business.service import ReviewService

        payload = ReviewService().build_fast_path_approval_payload(
            parent_type=parent_type,
            parent_public_id=parent_public_id,
            user_id=resolve_user_id(current_user),
            comments="Approved at completion (approver fast-path).",
        )
        if payload is None:
            # Already approved — a concurrent or retried completion got here
            # first. Nothing to record; completion proceeds.
            return
        _execute_create(payload, current_user, "Failed to record approval")
        logger.info(
            "U-458 approver fast-path recorded an approval: %s %s by user %s",
            parent_type, parent_public_id, current_user.get("id"),
        )

    enforce_completion_gate(
        parent_type,
        resolve_review=resolve_review,
        record_approval=_record_approval,
        # BOTH permissions, deliberately (Codex P1, 2026-09-15).
        #
        # The design specifies the fast-path as `can_complete AND can_approve`
        # (§4.2), and `can_complete` is already enforced by the route's own
        # dependency. But the fast-path WRITES a Review row, and every route
        # that writes one today — /submit, /advance, /decline — requires
        # `can_submit`, a contract `tests/test_review_route_rbac.py` pins
        # explicitly. Authorising on `can_approve` alone would let the fast-path
        # record an approval that `POST /advance/review/*` would refuse from the
        # same actor.
        #
        # Requiring both is strictly conservative: the fast-path can never be
        # more permissive than either the design's rule or the live API's. When
        # LS-06c realigns the review routes onto `can_approve`, drop the second
        # clause — not before.
        actor_can_approve=(
            has_module_permission(current_user, module_name, "can_approve")
            and has_module_permission(current_user, module_name, "can_submit")
        ),
        exempt=exempt,
    )
