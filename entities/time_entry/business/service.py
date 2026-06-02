# Python Standard Library Imports
import logging
from typing import Optional, Tuple

# Third-party Imports

# Local Imports
from entities.time_entry.business.model import TimeEntry
from entities.time_entry.persistence.repo import TimeEntryRepository
from entities.time_entry.persistence.time_entry_status_repo import TimeEntryStatusRepository
from entities.time_entry.persistence.time_log_repo import TimeLogRepository
from entities.user.business.service import UserService
from shared.authz import current_user_id, current_is_system_admin, current_can_view_team

logger = logging.getLogger(__name__)

# Time Tracking module name — matches the row in `dbo.Module`.
_TIME_TRACKING_MODULE = "Time Tracking"

# Valid status transitions
VALID_TRANSITIONS = {
    "draft": ["submitted"],
    "submitted": ["approved", "rejected"],
    "approved": ["billed"],
    "rejected": ["draft"],
    "billed": [],
}


def _actor_scope() -> Tuple[Optional[int], Optional[bool], bool]:
    """
    Read the current request actor from ContextVars.

    Returns `(user_id, is_system_admin, can_view_team)` where
    `can_view_team` is True if the actor's role grants `can_view_team` on the
    Time Tracking module — opens row visibility to TimeEntries on any project
    in the actor's UserProject set, in addition to their own rows.
    """
    return (
        current_user_id.get(),
        current_is_system_admin.get(),
        current_can_view_team(_TIME_TRACKING_MODULE),
    )


class TimeEntryService:
    """
    Service for TimeEntry entity business operations.

    Phase 3 row-scoping: read/update/delete paths read the calling
    user's id + system-admin flag from ContextVars and forward them
    to the repository. Sprocs filter to rows whose UserId matches
    (system admins bypass).

    The submit/approve/reject transition methods bypass UserId scope
    via `actor_is_system_admin=True`. The API surface gates these on
    the Time Tracking module's can_submit / can_approve permissions
    (RBAC), so the service can trust that the caller is authorized
    to act on entries they don't own.
    """

    def __init__(self, repo: Optional[TimeEntryRepository] = None):
        """Initialize the TimeEntryService."""
        self.repo = repo or TimeEntryRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        user_public_id: str,
        work_date: str,
        note: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> TimeEntry:
        """
        Create a new time entry and set initial status to 'draft'.
        """
        # Validate and resolve user
        user = UserService().read_by_public_id(public_id=user_public_id)
        if not user:
            raise ValueError(f"User with public_id '{user_public_id}' not found.")
        user_id = user.id

        # Resolve actor for CreatedByUserId attribution. Caller may pass
        # explicitly (e.g. clerk-on-behalf-of-worker); else fall back to the
        # current request's actor; else null lets the sproc DEFAULT (17) fire
        # for system/scheduler context.
        actor_user_id = created_by_user_id if created_by_user_id is not None else current_user_id.get()

        # Create the time entry
        time_entry = self.repo.create(
            user_id=user_id,
            work_date=work_date,
            note=note,
            created_by_user_id=actor_user_id,
        )

        # Create initial 'draft' status
        status_user_id = actor_user_id or user_id
        TimeEntryStatusRepository().create(
            time_entry_id=time_entry.id,
            status="draft",
            user_id=status_user_id,
        )

        return time_entry

    def read_all(self) -> list[TimeEntry]:
        """
        Read all time entries the current actor can access.
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        return self.repo.read_all(
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def read_by_id(self, id: int) -> Optional[TimeEntry]:
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        return self.repo.read_by_id(
            id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def read_by_public_id(self, public_id: str) -> Optional[TimeEntry]:
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        return self.repo.read_by_public_id(
            public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def read_by_user_id(self, user_id: int) -> list[TimeEntry]:
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        return self.repo.read_by_user_id(
            user_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def read_by_project_id(self, project_id: int) -> list[TimeEntry]:
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        return self.repo.read_by_project_id(
            project_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sort_by: str = "WorkDate",
        sort_direction: str = "DESC",
    ) -> list[TimeEntry]:
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        return self.repo.read_paginated(
            page_number=page_number,
            page_size=page_size,
            search_term=search_term,
            user_id=user_id,
            project_id=project_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            sort_by=sort_by,
            sort_direction=sort_direction,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> int:
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        return self.repo.count(
            search_term=search_term,
            user_id=user_id,
            project_id=project_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_public_id: Optional[str] = None,
        work_date: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Optional[TimeEntry]:
        """
        Update a time entry by public ID. Only allowed when status is 'draft'.
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()

        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        # Status read uses admin bypass — scope is already enforced via
        # the read above. We just need the status itself.
        current_status = TimeEntryStatusRepository().read_current(
            time_entry_id=existing.id,
            actor_is_system_admin=True,
        )
        if current_status and current_status.status != "draft":
            raise ValueError(f"Cannot update time entry in '{current_status.status}' status. Only 'draft' entries can be edited.")

        # Phase 5 edit-lock — even if current_status == 'draft' (e.g. after a
        # reject), a previously-aggregated row may already be billed/invoiced.
        # In that case the aggregated row is frozen and further TimeEntry edits
        # would silently diverge from what was billed. Block.
        if self.repo.is_downstream_locked(time_entry_id=existing.id):
            raise ValueError(
                "Cannot edit this time entry — it has already been billed or invoiced. "
                "An admin must reverse the downstream Bill/Invoice before edits resume."
            )

        if user_public_id is not None:
            user = UserService().read_by_public_id(public_id=user_public_id)
            if not user:
                raise ValueError(f"User with public_id '{user_public_id}' not found.")
            existing.user_id = user.id

        if work_date is not None:
            existing.work_date = work_date
        if note is not None:
            existing.note = note

        existing.row_version = row_version

        return self.repo.update_by_id(
            existing,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def delete_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
    ) -> Optional[TimeEntry]:
        """
        Delete a time entry by public ID. Only allowed when status is 'draft'.
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()

        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        current_status = TimeEntryStatusRepository().read_current(
            time_entry_id=existing.id,
            actor_is_system_admin=True,
        )
        if current_status and current_status.status != "draft":
            raise ValueError(f"Cannot delete time entry in '{current_status.status}' status. Only 'draft' entries can be deleted.")

        return self.repo.delete_by_id(
            id=existing.id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def submit(self, public_id: str, *, user_id: int) -> TimeEntry:
        """
        Submit a time entry for review. Transitions from 'draft' to 'submitted'.
        Caller must be the entry owner (enforced by row-scope at read).
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        self._validate_transition(existing.id, "submitted")

        # Verify at least one time log exists. Bypass row-scope here —
        # the parent read already proved access.
        logs = TimeLogRepository().read_by_time_entry_id(
            time_entry_id=existing.id,
            actor_is_system_admin=True,
        )
        if not logs:
            raise ValueError("Cannot submit time entry without any time logs.")

        TimeEntryStatusRepository().create(
            time_entry_id=existing.id,
            status="submitted",
            user_id=user_id,
        )

        # Sidecar: aggregate this entry into ContractLabor or EmployeeLabor for
        # billing. Best-effort — aggregation failure (e.g. worker has no
        # EmployeeId/VendorId linkage) logs + flags but does NOT roll back the
        # submitted-status transition. The worker still submits cleanly; the
        # office sees a flagged row on the bills page.
        try:
            results = self.repo.aggregate_for_billing(time_entry_id=existing.id)
            for r in results:
                if r.get("rate_source") == "none":
                    logger.warning(
                        "time_entry.aggregate.rate_missing",
                        extra={
                            "time_entry_public_id": existing.public_id,
                            "target_table": r.get("target_table"),
                            "project_id": r.get("project_id"),
                            "note": r.get("note"),
                        },
                    )
        except Exception:
            logger.exception(
                "time_entry.aggregate.failed",
                extra={"time_entry_public_id": existing.public_id},
            )

        # Sidecar: enqueue a time_tracking_specialist review pass. Best-effort
        # — outbox failure must NOT roll back the status transition. The
        # scheduler tick will pick up the queued row and run the agent; if
        # this enqueue fails, the reviewer simply loses the agent flag on
        # this entry and reviews by hand.
        try:
            from intelligence.outbox.business.service import (
                TimeTrackingOutboxService,
            )
            TimeTrackingOutboxService().enqueue_review_submitted_time_entry(
                time_entry_public_id=existing.public_id,
            )
        except Exception:
            logger.exception(
                "time_tracking.outbox.enqueue.failed",
                extra={"time_entry_public_id": existing.public_id},
            )

        return existing

    def approve(self, public_id: str, *, user_id: int, note: Optional[str] = None) -> TimeEntry:
        """
        Approve a submitted time entry. Transitions from 'submitted' to 'approved'.
        The API surface gates this on Time Tracking can_approve — reviewers act
        on entries they don't own, so this method bypasses UserId row-scope.
        """
        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_is_system_admin=True,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        self._validate_transition(existing.id, "approved")

        TimeEntryStatusRepository().create(
            time_entry_id=existing.id,
            status="approved",
            user_id=user_id,
            note=note,
        )

        return existing

    def reject(self, public_id: str, *, user_id: int, note: Optional[str] = None) -> TimeEntry:
        """
        Reject a submitted time entry. Transitions from 'submitted' back to 'draft'.
        The API surface gates this on Time Tracking can_approve — reviewers act
        on entries they don't own, so this method bypasses UserId row-scope.
        """
        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_is_system_admin=True,
        )
        if not existing:
            raise ValueError(f"TimeEntry with public_id '{public_id}' not found.")

        self._validate_transition(existing.id, "rejected")

        # Phase 5 edit-lock — block reject if the aggregated row is already
        # billed/invoiced. Rejecting would push the entry back to 'draft' so
        # the worker could edit it, but the downstream bill/invoice is frozen
        # and any new edits would desync. Reviewer must reverse downstream
        # before reject.
        if self.repo.is_downstream_locked(time_entry_id=existing.id):
            raise ValueError(
                "Cannot reject this time entry — it has already been billed or invoiced. "
                "Reverse the downstream Bill/Invoice first."
            )

        status_repo = TimeEntryStatusRepository()

        status_repo.create(
            time_entry_id=existing.id,
            status="rejected",
            user_id=user_id,
            note=note,
        )

        # Auto-transition back to draft so the worker can re-edit
        status_repo.create(
            time_entry_id=existing.id,
            status="draft",
            user_id=user_id,
        )

        return existing

    # ------------------------------------------------------------------ #
    # Agent review flag stamping
    # ------------------------------------------------------------------ #

    # Allowed ReviewPriority bucket vocabulary. Order is not significant.
    _ALLOWED_PRIORITIES = ("high", "medium", "low", "clean")

    def stamp_review(
        self,
        *,
        public_id: str,
        priority: str,
        reasons: list,
    ) -> dict:
        """
        Stamp ReviewPriority + ReviewReasons on a TimeEntry. Used by the
        time_tracking_specialist agent after it runs validate + applies
        its priority mapping. Does NOT transition CurrentStatus.

        `priority` ∈ {'high', 'medium', 'low', 'clean'}.
        `reasons` is a list of short reason-code strings; validated against
        entities.time_entry.business.validation.ALL_REASON_CODES — unknown
        codes raise ValueError.

        Returns the stamped state for confirmation:
            {priority, reasons, affected_row_count}
        """
        # Lazy import to keep the validation module out of the time_entry
        # service import graph by default.
        from entities.time_entry.business.validation import ALL_REASON_CODES

        if priority not in self._ALLOWED_PRIORITIES:
            raise ValueError(
                f"Invalid ReviewPriority {priority!r}; "
                f"must be one of {self._ALLOWED_PRIORITIES}."
            )

        if not isinstance(reasons, list) or not all(isinstance(r, str) for r in reasons):
            raise ValueError("reasons must be a list of strings.")

        unknown = [r for r in reasons if r not in ALL_REASON_CODES]
        if unknown:
            raise ValueError(
                f"Unknown reason code(s): {unknown}. "
                f"Allowed codes: {sorted(ALL_REASON_CODES)}."
            )

        import json
        reasons_json = json.dumps(reasons)

        affected = self.repo.stamp_review(
            public_id=public_id,
            priority=priority,
            reasons_json=reasons_json,
        )
        if affected == 0:
            raise ValueError(
                f"TimeEntry with public_id '{public_id}' not found."
            )

        return {
            "time_entry_public_id": public_id,
            "priority": priority,
            "reasons": reasons,
            "affected_row_count": affected,
        }

    def _validate_transition(self, time_entry_id: int, target_status: str) -> None:
        """
        Validate that a status transition is allowed. Status reads bypass
        row-scope — the caller already proved access (or is acting in a
        privileged transition like approve/reject).
        """
        current = TimeEntryStatusRepository().read_current(
            time_entry_id=time_entry_id,
            actor_is_system_admin=True,
        )
        current_status = current.status if current else None

        if current_status is None:
            raise ValueError("Time entry has no status history.")

        allowed = VALID_TRANSITIONS.get(current_status, [])
        if target_status not in allowed:
            raise ValueError(
                f"Cannot transition from '{current_status}' to '{target_status}'. "
                f"Allowed transitions: {allowed}"
            )
