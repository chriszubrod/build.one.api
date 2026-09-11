# Python Standard Library Imports
from dataclasses import replace
from typing import Optional

# Third-party Imports

# Local Imports
from entities.review_status.business.model import ReviewStatus
from entities.review_status.persistence.repo import ReviewStatusRepository
from shared.api.responses import REVIEW_STATUS_SHAPE_PREFIX
from shared.authz import current_user_id


class ReviewStatusShapeError(ValueError):
    """A create/update/delete that would leave the ReviewStatus set malformed.

    The four statuses are the alphabet `review_status_kind` is written in. Two
    of the three kinds key on flags (IsDeclined, IsFinal) and — since U-444 —
    so does the third (IsInitial). Nothing enforced that alphabet before this:
    the service passed straight through to the repo, so a single admin edit
    could auto-approve every submission or strand 758 Reviews.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"{REVIEW_STATUS_SHAPE_PREFIX}{reason}")


class ReviewStatusService:
    """
    Service for ReviewStatus entity business operations.
    """

    def __init__(self, repo: Optional[ReviewStatusRepository] = None):
        """Initialize the ReviewStatusService."""
        self.repo = repo or ReviewStatusRepository()


    # ------------------------------------------------------------------
    # Shape rails (U-444)
    # ------------------------------------------------------------------

    def _resulting_set(
        self, *, changed: Optional[ReviewStatus], removed_id: Optional[int] = None
    ) -> list[ReviewStatus]:
        """The full set as it WOULD be after the proposed write.

        Validating the resulting set — rather than the current one — is what
        makes "exactly one active X" checkable at all: a create that adds a
        second final row is only visible once you look at the set including it.

        Race note (accepted, U-444): this is a read-modify-write, so two
        concurrent admins could each validate against a set that predates the
        other. `dbo.ReviewStatus` holds 4 rows, is admin-only, and is edited
        approximately never; an applock here would cost more than it buys.
        """
        rows = [s for s in self.repo.read_all() if s.id != removed_id]
        if changed is not None:
            rows = [s for s in rows if s.id != changed.id]
            # Mirrors what the sprocs do in the same transaction: setting a
            # singleton role MOVES it. Without this the rails are unsatisfiable —
            # setting the new holder reads as two, clearing the old reads as
            # zero, so neither half of the move can ever be made. All three
            # roles behave identically (Codex P1: the first cut did this only
            # for is_initial, leaving 'Approved' and 'Declined' unreplaceable).
            if changed.is_initial:
                rows = [replace(s, is_initial=False) if s.is_initial else s for s in rows]
            if changed.is_final:
                rows = [
                    replace(s, is_final=False) if (s.is_final and s.is_active) else s
                    for s in rows
                ]
            if changed.is_declined:
                rows = [
                    replace(s, is_declined=False) if (s.is_declined and s.is_active) else s
                    for s in rows
                ]
            rows = rows + [changed]
        return rows

    @staticmethod
    def _assert_shape(rows: list[ReviewStatus]) -> None:
        """Reject any set that `review_status_kind` could not be derived from.

        Order matters only for message quality — a row that is both final and
        declined is reported as that, not as "two declined rows"."""
        for s in rows:
            if s.is_final and s.is_declined:
                raise ReviewStatusShapeError(
                    f"'{s.name}' cannot be both final and declined."
                )
            if s.is_initial and (s.is_final or s.is_declined):
                raise ReviewStatusShapeError(
                    f"'{s.name}' is the initial status, so it cannot also be "
                    "final or declined — /submit would immediately resolve it."
                )

        active = [s for s in rows if s.is_active]

        initial = [s for s in active if s.is_initial]
        if len(initial) != 1:
            raise ReviewStatusShapeError(
                f"exactly one active status must be the initial one; found {len(initial)}. "
                "It is the status every submission is created at."
            )

        final = [s for s in active if s.is_final and not s.is_declined]
        if len(final) != 1:
            raise ReviewStatusShapeError(
                f"exactly one active status must be final; found {len(final)}."
            )

        declined = [s for s in active if s.is_declined]
        if len(declined) != 1:
            # Previously a RUNTIME failure: ReviewService's decline path errors
            # on 0 or >1 (review/business/service.py). Moving it here turns a
            # broken decline at the worst moment into a rejected admin edit.
            raise ReviewStatusShapeError(
                f"exactly one active status must be the declined one; found {len(declined)}."
            )

    def _assert_not_referenced(self, status: ReviewStatus, *, action: str) -> None:
        """Refuse to strand live documents on a status that leaves the pipeline.

        DELETE is already blocked by FK; this is really the deactivate rail.
        """
        count = self.repo.count_references(status.id)
        if count:
            raise ReviewStatusShapeError(
                f"cannot {action} '{status.name}' — {count} review(s) still reference it."
            )

    def create(
        self,
        *,
        tenant_id: int = None,
        name: Optional[str],
        description: Optional[str] = None,
        sort_order: int = 0,
        is_final: bool = False,
        is_declined: bool = False,
        is_active: bool = True,
        is_initial: bool = False,
        color: Optional[str] = None,
    ) -> ReviewStatus:
        """
        Create a new review status.
        """
        self._assert_shape(
            self._resulting_set(
                changed=ReviewStatus(
                    id=None, public_id=None, row_version=None,
                    created_datetime=None, modified_datetime=None,
                    name=name, description=description, sort_order=sort_order,
                    is_final=is_final, is_declined=is_declined,
                    is_active=is_active, color=color, is_initial=is_initial,
                )
            )
        )
        return self.repo.create(
            name=name,
            description=description,
            sort_order=sort_order,
            is_final=is_final,
            is_declined=is_declined,
            is_active=is_active,
            is_initial=is_initial,
            color=color,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[ReviewStatus]:
        """
        Read all review statuses, ordered by SortOrder.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[ReviewStatus]:
        """
        Read a review status by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[ReviewStatus]:
        """
        Read a review status by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def get_next_status(self, current_sort_order: int) -> Optional[ReviewStatus]:
        """
        Get the next active, non-declined review status after the given sort order.
        """
        return self.repo.read_next(current_sort_order)

    def get_first_status(self) -> Optional[ReviewStatus]:
        """
        Get the first active, non-declined review status (initial submission status).
        """
        return self.repo.read_first()

    def get_declined_statuses(self) -> list[ReviewStatus]:
        """
        Get all active declined statuses.
        """
        all_statuses = self.repo.read_all()
        return [s for s in all_statuses if s.is_declined and s.is_active]

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        description: str = None,
        sort_order: int = None,
        is_final: bool = None,
        is_declined: bool = None,
        is_active: bool = None,
        is_initial: bool = None,
        color: str = None,
    ) -> Optional[ReviewStatus]:
        """
        Update a review status by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            was_active = existing.is_active
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if description is not None:
                existing.description = description
            if sort_order is not None:
                existing.sort_order = sort_order
            if is_final is not None:
                existing.is_final = is_final
            if is_declined is not None:
                existing.is_declined = is_declined
            if is_active is not None:
                existing.is_active = is_active
            if is_initial is not None:
                existing.is_initial = is_initial
            if color is not None:
                existing.color = color

            self._assert_shape(self._resulting_set(changed=existing))
            # Only a transition INTO inactive strands anything — re-activating,
            # or editing a row that is already inactive, is always safe.
            if was_active and not existing.is_active:
                self._assert_not_referenced(existing, action="deactivate")
            try:
                return self.repo.update_by_id(existing)
            except Exception as error:
                # The sproc re-runs the strand check inside its own transaction
                # (the rail above reads on a separate connection, so a Review
                # inserted in between is invisible to it). When that backstop
                # fires it comes back as a RAISERROR, which `map_database_error`
                # wraps — losing the prefix `raise_workflow_error` keys on, so
                # a genuine race would surface as a generic 400 instead of the
                # 422 the identical rejection gets one line earlier. Restore it,
                # so the client cannot tell which layer caught it.
                if REVIEW_STATUS_SHAPE_PREFIX in str(error):
                    raise ReviewStatusShapeError(
                        str(error).split(REVIEW_STATUS_SHAPE_PREFIX, 1)[1].strip()
                    ) from error
                raise
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[ReviewStatus]:
        """
        Delete a review status by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            # The FK refuses this too, but as a 547 carrying a constraint name.
            # Checking first turns it into a sentence an admin can act on.
            self._assert_not_referenced(existing, action="delete")
            self._assert_shape(self._resulting_set(changed=None, removed_id=existing.id))
            return self.repo.delete_by_id(existing.id)
        return None
