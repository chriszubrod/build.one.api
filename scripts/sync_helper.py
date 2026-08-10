# Python Standard Library Imports
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.sync.business.model import Sync
from integrations.sync.business.service import SyncService

logger = logging.getLogger(__name__)


def assert_cli_system_admin() -> None:
    """
    CLI sync scripts span all users by design; declare system intent so the
    per-row access guards in shared/access.py bypass for these reads.
    Mirrors what `_require_drain_secret` does for HTTP-triggered drains.

    Call this as the first statement under `if __name__ == "__main__":` in
    every sync script. Safe to call when the script is imported (it just
    sets a ContextVar) but should only be reached when the script is the
    program entry point.
    """
    from shared.authz.context import set_authz_context
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)


def _normalize_last_sync(last_sync: Optional[str]) -> Optional[str]:
    if not last_sync:
        return None
    # QBO accepts 'YYYY-MM-DDTHH:MM:SSZ' or '...+00:00'. Ensure Z form for safety.
    if last_sync.endswith("Z"):
        return last_sync
    if last_sync.endswith("+00:00"):
        return last_sync[:-6] + "Z"
    # If naive or other offset, coerce to Z (assumes stored in UTC)
    if "T" in last_sync:
        return last_sync.split(".")[0] + "Z"
    return last_sync


def _normalize_watermark_value(value) -> Optional[str]:
    """
    Normalize Sync.last_sync_datetime for QBO query filters.

    ReadSyncs returns LastSyncDatetime as a Python datetime; CreateSync/Update
    OUTPUT returns VARCHAR. Accept both without breaking _normalize_last_sync.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str):
        return _normalize_last_sync(value)
    return _normalize_last_sync(str(value))


def _watermark_overlap_seconds() -> int:
    """
    Read at call time so tests can monkeypatch QBO_SYNC_WATERMARK_OVERLAP_SECONDS.

    Default 60s covers host vs QBO LastUpdatedTime clock skew; query_start already
    bounds the run duration. Re-pulling the overlap is not free at fan-out (SharePoint,
    Box doc push, workbook lock) — transactional pulls run every 15m, so a larger overlap
    re-scans a meaningful fraction of each interval.
    """
    default = 60
    raw = os.environ.get("QBO_SYNC_WATERMARK_OVERLAP_SECONDS")
    if raw is None or raw == "":
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "Invalid QBO_SYNC_WATERMARK_OVERLAP_SECONDS=%r; using default %s",
            raw,
            default,
        )
        return default
    if parsed < 0:
        logger.warning(
            "Negative QBO_SYNC_WATERMARK_OVERLAP_SECONDS=%s; using default %s",
            parsed,
            default,
        )
        return default
    return parsed


class WatermarkRun:
    """
    Unified watermark open/commit for QBO pull scripts.

    Captures query_start before any I/O so the committed watermark can never
    land after QBO edits that occurred during a long inline fan-out (attachments,
    Excel, SharePoint). watermark_value is query_start minus a small overlap;
    upserts are idempotent but fan-out side effects are not.
    """

    def __init__(self, sync_service: SyncService, provider: str, env: str, entity: str):
        self.sync_service = sync_service
        self.provider = provider
        self.env = env
        self.entity = entity
        self.query_start: Optional[datetime] = None
        self.sync_record: Optional[Sync] = None

    def open(self) -> "WatermarkRun":
        self.query_start = datetime.now(timezone.utc)

        candidates = self.sync_service.read_candidates_for(
            self.provider, self.env, self.entity
        )
        if len(candidates) > 1:
            duplicate_ids = ", ".join(str(c.id) for c in candidates)
            logger.warning(
                "Multiple Sync rows for %s/%s/%s (ids: %s); using freshest per repo rule",
                self.provider,
                self.env,
                self.entity,
                duplicate_ids,
            )

        sync_record = self.sync_service.pick_canonical(candidates)
        if not sync_record:
            try:
                sync_record = self.sync_service.create(
                    provider=self.provider,
                    env=self.env,
                    entity=self.entity,
                    last_sync_datetime=None,
                )
                logger.info(
                    "Created new sync record for %s/%s/%s",
                    self.provider,
                    self.env,
                    self.entity,
                )
            except Exception:
                sync_record = self.sync_service.pick_canonical(
                    self.sync_service.read_candidates_for(
                        self.provider, self.env, self.entity
                    )
                )
                if not sync_record:
                    raise

        self.sync_record = sync_record
        return self

    @property
    def last_sync_time(self) -> Optional[str]:
        if not self.sync_record:
            return None
        return _normalize_watermark_value(self.sync_record.last_sync_datetime)

    @property
    def watermark_value(self) -> str:
        if self.query_start is None:
            raise RuntimeError("WatermarkRun.open() must be called before watermark_value")
        overlap = timedelta(seconds=_watermark_overlap_seconds())
        stamp = self.query_start - overlap
        return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _persist_watermark(self, sync_record: Sync, value: str) -> Optional[Sync]:
        updated_sync = Sync(
            id=sync_record.id,
            public_id=sync_record.public_id,
            row_version=sync_record.row_version,
            created_datetime=sync_record.created_datetime,
            modified_datetime=sync_record.modified_datetime,
            provider=sync_record.provider,
            env=sync_record.env,
            entity=sync_record.entity,
            last_sync_datetime=value,
        )
        return self.sync_service.update_by_public_id(sync_record.public_id, updated_sync)

    def _write(self, value: str) -> Sync:
        if not self.sync_record:
            raise RuntimeError("WatermarkRun.open() must be called before commit")
        persisted = self._persist_watermark(self.sync_record, value)
        if persisted:
            self.sync_record = persisted
            return persisted

        current = self.sync_service.pick_canonical(
            self.sync_service.read_candidates_for(
                self.provider, self.env, self.entity
            )
        )
        if current is None:
            msg = (
                f"Failed to persist sync watermark for {self.provider}/{self.env}/{self.entity}: "
                f"last_sync_datetime={value!r}"
            )
            logger.error(msg)
            raise RuntimeError(msg)

        if self.sync_service.watermark_is_at_or_ahead(current, value):
            logger.info(
                "Concurrent sync run advanced watermark for %s/%s/%s to %s (intended %s); adopting",
                self.provider,
                self.env,
                self.entity,
                current.last_sync_datetime,
                value,
            )
            self.sync_record = current
            return current

        persisted = self._persist_watermark(current, value)
        if persisted:
            self.sync_record = persisted
            return persisted

        msg = (
            f"Failed to persist sync watermark for {self.provider}/{self.env}/{self.entity}: "
            f"last_sync_datetime={value!r}"
        )
        logger.error(msg)
        raise RuntimeError(msg)

    def commit(
        self,
        outcome: SyncOutcome,
        *,
        end_date: Optional[str] = None,
        skip: bool = False,
    ) -> Sync:
        if not self.sync_record:
            raise RuntimeError("WatermarkRun.open() must be called before commit")

        if skip:
            logger.info("Skipping sync record update")
            return self.sync_record

        if outcome.should_hold:
            reason = outcome.hold_reason()
            logger.warning(
                "Holding sync watermark (%s); window will be re-pulled next run",
                reason,
            )
            return self.sync_record

        if end_date:
            return self._write(f"{end_date}T23:59:59")

        return self._write(self.watermark_value)
