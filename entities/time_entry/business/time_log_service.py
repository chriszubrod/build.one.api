# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal
from datetime import datetime

# Third-party Imports

# Local Imports
from entities.time_entry.business.model import TimeLog, TimeEntry
from entities.time_entry.persistence.repo import TimeEntryRepository
from entities.time_entry.persistence.time_log_repo import TimeLogRepository
from entities.time_entry.persistence.time_entry_status_repo import TimeEntryStatusRepository
from entities.time_entry.business.actor_scope import actor_scope as _actor_scope
from shared.database import DatabaseError

logger = logging.getLogger(__name__)


class TimeLogService:
    """
    Service for TimeLog entity business operations.
    Lightweight child entity — direct CRUD, no ProcessEngine routing.

    Phase 3 row-scoping: forwards the actor's UserId + IsSystemAdmin +
    CanViewTeam flags to the repo, which scopes via the parent
    TimeEntry.UserId (widened to UserProject-overlap rows for
    can_view_team actors).
    """

    def __init__(self, repo: Optional[TimeLogRepository] = None):
        """Initialize the TimeLogService."""
        self.repo = repo or TimeLogRepository()

    def create(
        self,
        *,
        time_entry_public_id: str,
        clock_in: str,
        clock_out: Optional[str] = None,
        log_type: str = "work",
        latitude: Optional[Decimal] = None,
        longitude: Optional[Decimal] = None,
        project_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> TimeLog:
        """
        Create a new time log for a time entry.
        Only allowed when the parent time entry is in 'draft' status.
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()

        # Validate parent exists AND is accessible to the actor
        time_entry = TimeEntryRepository().read_by_public_id(
            public_id=time_entry_public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )
        if not time_entry:
            raise ValueError(f"TimeEntry with public_id '{time_entry_public_id}' not found.")

        # Validate BEFORE the parent check: a reopen is a committed status write,
        # and a request that is going to be refused must not leave one behind.
        if log_type not in ("work", "break"):
            raise ValueError(f"Invalid log_type '{log_type}'. Must be 'work' or 'break'.")
        # On a draft day a bad timestamp or a replayed create still fails where
        # it always did (the sproc, the unique index). On a submitted day either
        # would fail AFTER the reopen had committed, so neither may reopen.
        malformed = self._parse_timestamp(clock_in) is None \
            or (clock_out is not None and self._parse_timestamp(clock_out) is None)
        reopen_as = self._ensure_parent_writable(
            time_entry, actor_user_id,
            allow_reopen=not malformed and not self._is_replay(time_entry.id, clock_in),
            why_not=(" The clock-in or clock-out is not a valid timestamp." if malformed else
                     " Another log on the day holds this clock-in."),
        )

        duration = self._calculate_duration(clock_in, clock_out)

        return self._write(lambda: self.repo.create(
            time_entry_id=time_entry.id,
            reopen_as_user_id=reopen_as,
            reopen_note=self.REOPEN_NOTE if reopen_as is not None else None,
            clock_in=clock_in,
            clock_out=clock_out,
            log_type=log_type,
            duration=duration,
            latitude=latitude,
            longitude=longitude,
            project_id=project_id,
            note=note,
            created_by_user_id=actor_user_id,
        ))

    def read_by_time_entry_public_id(self, time_entry_public_id: str) -> list[TimeLog]:
        """
        Read all time logs for a time entry by the parent's public ID.
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        time_entry = TimeEntryRepository().read_by_public_id(
            public_id=time_entry_public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )
        if not time_entry:
            raise ValueError(f"TimeEntry with public_id '{time_entry_public_id}' not found.")
        return self.repo.read_by_time_entry_id(
            time_entry_id=time_entry.id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def read_by_public_id(self, public_id: str) -> Optional[TimeLog]:
        """
        Read a time log by public ID, scoped to the actor.
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()
        return self.repo.read_by_public_id(
            public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )

    def update_by_public_id(
        self,
        public_id: str,
        *,
        row_version: str,
        clock_in: Optional[str] = None,
        clock_out: Optional[str] = None,
        log_type: Optional[str] = None,
        latitude: Optional[Decimal] = None,
        longitude: Optional[Decimal] = None,
        project_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> Optional[TimeLog]:
        """
        Update a time log by public ID, scoped to the actor.
        Only allowed when the parent time entry is in 'draft' status.
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()

        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )
        if not existing:
            raise ValueError(f"TimeLog with public_id '{public_id}' not found.")

        if log_type is not None and log_type not in ("work", "break"):
            raise ValueError(f"Invalid log_type '{log_type}'. Must be 'work' or 'break'.")
        # On a draft day these two are the sproc's business (its RowVersion
        # predicate, a plain project update). On a submitted day they decide
        # whether the OWNER's write may reopen it at all — see
        # TimeEntryService.reopen_for_owner_write: a stale row would fail after
        # the reopen had already committed, and a project move leaves the old
        # project's aggregated line standing (U-597).
        stale = existing.row_version != row_version
        # A project move or a work↔break change empties this log's aggregation
        # bucket; the aggregator now retires the stale line on resubmit, but a
        # retired line a bill already points at refuses the resubmit — so these
        # stay with review on a submitted day rather than reopening it.
        rebucketing = (project_id is not None and project_id != existing.project_id) \
            or (log_type is not None and log_type != existing.log_type)
        malformed = (clock_in is not None and self._parse_timestamp(clock_in) is None) \
            or (clock_out is not None and self._parse_timestamp(clock_out) is None)
        colliding = clock_in is not None and clock_in != existing.clock_in and not malformed \
            and self._is_replay(existing.time_entry_id, clock_in, exclude_log_id=existing.id)
        reopen_as = self._ensure_parent_writable(
            self._parent_of(existing), actor_user_id,
            allow_reopen=not stale and not rebucketing and not malformed and not colliding,
            why_not=(" The log changed on the server since this edit was made; refresh and retry." if stale else
                     " The clock-in or clock-out is not a valid timestamp." if malformed else
                     " Another log on the day holds this clock-in." if colliding else
                     " Moving a log to another project, or between work and break, on a submitted day goes through review."),
        )

        if clock_in is not None:
            existing.clock_in = clock_in
        if clock_out is not None:
            existing.clock_out = clock_out
        if log_type is not None:
            existing.log_type = log_type
        if latitude is not None:
            existing.latitude = latitude
        if longitude is not None:
            existing.longitude = longitude
        if project_id is not None:
            existing.project_id = project_id
        if note is not None:
            existing.note = note

        existing.duration = self._calculate_duration(existing.clock_in, existing.clock_out)
        existing.row_version = row_version

        return self._write(lambda: self.repo.update_by_id(
            existing,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
            reopen_as_user_id=reopen_as,
            reopen_note=self.REOPEN_NOTE if reopen_as is not None else None,
        ))

    def delete_by_public_id(self, public_id: str) -> Optional[TimeLog]:
        """
        Delete a time log by public ID, scoped to the actor.
        Only allowed when the parent time entry is in 'draft' status.
        """
        actor_user_id, actor_is_system_admin, actor_can_view_team = _actor_scope()

        existing = self.repo.read_by_public_id(
            public_id=public_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        )
        if not existing:
            raise ValueError(f"TimeLog with public_id '{public_id}' not found.")

        self._ensure_parent_writable(
            self._parent_of(existing), actor_user_id,
            allow_reopen=False,
            why_not=" Deleting a log on a submitted day goes through review.",
        )
        return self._write(lambda: self.repo.delete_by_id(
            id=existing.id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
            actor_can_view_team=actor_can_view_team,
        ))

    REOPEN_NOTE = "Reopened: the worker's device delivered a time log after submission."

    def _ensure_parent_writable(
        self,
        time_entry: TimeEntry,
        actor_user_id: Optional[int],
        *,
        allow_reopen: bool = True,
        why_not: str = "",
    ) -> Optional[int]:
        """
        A log write needs its parent in 'draft'. A 'submitted', not-yet-approved
        parent may be REOPENED when the writer is the worker who owns it — the
        device finishing a day auto-submit closed early (U-596); the policy and
        every refusal live in TimeEntryService.reopen_for_owner_write, which
        returns the owner id to hand to the write sproc (it reopens and writes
        in ONE transaction) or None for a plain draft write. Callers pass
        `allow_reopen=False` (with the reason) for writes that must not reopen
        a day without a human. Status reads there bypass row-scope — the
        parent/log read above already proved access to the actor.
        """
        from entities.time_entry.business.service import TimeEntryService
        return TimeEntryService().reopen_for_owner_write(
            time_entry=time_entry, actor_user_id=actor_user_id,
            allow_reopen=allow_reopen, why_not=why_not,
        )

    @staticmethod
    def _write(op):
        """Run a log write. The sproc re-checks the day's status under lock; if a
        transition slipped in between our read and the write it refuses with the
        locked-entry wording — surface that as the 400 the clients classify, not
        as a database failure (500)."""
        try:
            return op()
        except DatabaseError as error:
            if "not in 'draft'" in str(error):
                raise ValueError(str(error).split("Database operation failed: ", 1)[-1])
            raise

    _TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")

    @classmethod
    def _parse_timestamp(cls, value) -> Optional[datetime]:
        for fmt in cls._TIMESTAMP_FORMATS:
            try:
                return datetime.strptime(value, fmt)
            except (TypeError, ValueError):
                continue
        return None

    def _is_replay(self, time_entry_id: int, clock_in: str, *, exclude_log_id: Optional[int] = None) -> bool:
        """A write whose (entry, clock_in) already exists on ANOTHER log — the
        unique index's natural key: a create the device re-sends after a dropped
        response, or an update that moves a clock-in onto a sibling's. Only
        consulted on the reopen path."""
        incoming = self._parse_timestamp(clock_in)
        for log in self.repo.read_by_time_entry_id(time_entry_id=time_entry_id, actor_is_system_admin=True):
            if exclude_log_id is not None and log.id == exclude_log_id:
                continue
            if log.clock_in == clock_in or (incoming is not None and self._parse_timestamp(log.clock_in) == incoming):
                return True
        return False

    @staticmethod
    def _parent_of(log: TimeLog) -> TimeEntry:
        """The scoped log read already proved access; the parent read is by id."""
        parent = TimeEntryRepository().read_by_id(id=log.time_entry_id, actor_is_system_admin=True)
        if not parent:
            raise ValueError(f"TimeEntry {log.time_entry_id} not found for time log '{log.public_id}'.")
        return parent

    @staticmethod
    def _calculate_duration(clock_in: str, clock_out: Optional[str]) -> Optional[Decimal]:
        """
        Calculate duration in hours from clock_in and clock_out timestamps.
        Returns None if clock_out is not set.
        """
        if not clock_out:
            return None

        try:
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt_in = datetime.strptime(clock_in, fmt)
                    break
                except ValueError:
                    continue
            else:
                logger.warning(f"Could not parse clock_in: {clock_in}")
                return None

            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt_out = datetime.strptime(clock_out, fmt)
                    break
                except ValueError:
                    continue
            else:
                logger.warning(f"Could not parse clock_out: {clock_out}")
                return None

            delta = dt_out - dt_in
            hours = Decimal(str(delta.total_seconds())) / Decimal("3600")
            return hours.quantize(Decimal("0.01"))
        except Exception as e:
            logger.warning(f"Error calculating duration: {e}")
            return None
