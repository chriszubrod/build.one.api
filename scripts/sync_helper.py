# Python Standard Library Imports
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository
from integrations.intuit.qbo.company_info.persistence.repo import QboCompanyInfoRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceRepository
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository
from integrations.intuit.qbo.term.persistence.repo import QboTermRepository
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository
from integrations.sync.business.model import Sync
from integrations.sync.business.service import SyncService
from integrations.sync.persistence.repo import _parse_sync_last_sync

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


def _watermark_hold_bound_seconds() -> int:
    """
    Read at call time so tests can monkeypatch QBO_WATERMARK_HOLD_BOUND_SECONDS.

    Default 7200s (2 hours) balances letting genuinely transient holds self-clear
    (e.g. purchase held ~47min on 2026-08-12 and self-cleared) against bounding
    worst-case silent-loss exposure when a hold would otherwise never advance.
    """
    default = 7200
    raw = os.environ.get("QBO_WATERMARK_HOLD_BOUND_SECONDS")
    if raw is None or raw == "":
        return default
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "Invalid QBO_WATERMARK_HOLD_BOUND_SECONDS=%r; using default %s",
            raw,
            default,
        )
        return default
    if parsed < 0:
        logger.warning(
            "Negative QBO_WATERMARK_HOLD_BOUND_SECONDS=%s; using default %s",
            parsed,
            default,
        )
        return default
    return parsed


def _held_duration(sync_record: Sync, now: datetime) -> Optional[timedelta]:
    """
    How long the watermark has been held since its last successful advance.

    Anchors on modified_datetime, falling back to created_datetime when the row
    has never successfully advanced. Returns None when the anchor is missing or
    unparseable — callers must treat that as "cannot determine; do not force-advance".
    """
    anchor_raw = sync_record.modified_datetime or sync_record.created_datetime
    anchor = _parse_sync_last_sync(anchor_raw)
    if anchor is None:
        return None
    return now - anchor


@dataclass(frozen=True)
class _QboSyncEntityMeta:
    # Capitalized display form for qbo.ReconciliationIssue.EntityType, matching every other
    # writer of that column's convention ("Bill", "VendorCredit", ...).
    label: str
    # Staging repo exposing read_by_id(id) -> Optional[QboX] with a .qbo_id field, used to
    # resolve a projection failure's internal staging PK back to the real QBO id. None when the
    # entity's repo doesn't expose read_by_id (reimburse_charge — only read_by_realm_id /
    # read_by_qbo_id_and_realm_id) — resolution falls back to no-id for it.
    staging_repo: Optional[type] = None


# Every scripts/sync_qbo_*.py sets its lowercase `entity = '...'` verbatim as WatermarkRun's 4th
# constructor arg. One registry per entity (mirrors integrations/intuit/qbo/base/field_ownership.py's
# _REGISTRY pattern) rather than parallel dicts, so a future entity can't drift out of sync between
# its display label and its staging-repo lookup.
_QBO_SYNC_ENTITY_META: Dict[str, _QboSyncEntityMeta] = {
    "bill": _QboSyncEntityMeta("Bill", QboBillRepository),
    "purchase": _QboSyncEntityMeta("Purchase", QboPurchaseRepository),
    "invoice": _QboSyncEntityMeta("Invoice", QboInvoiceRepository),
    "vendorcredit": _QboSyncEntityMeta("VendorCredit", QboVendorCreditRepository),
    "vendor": _QboSyncEntityMeta("Vendor", QboVendorRepository),
    "customer": _QboSyncEntityMeta("Customer", QboCustomerRepository),
    "item": _QboSyncEntityMeta("Item", QboItemRepository),
    "account": _QboSyncEntityMeta("Account", QboAccountRepository),
    "term": _QboSyncEntityMeta("Term", QboTermRepository),
    "company_info": _QboSyncEntityMeta("CompanyInfo", QboCompanyInfoRepository),
    "reimburse_charge": _QboSyncEntityMeta("ReimburseCharge"),
}


def _resolve_staging_qbo_id(entity: str, staging_pk) -> Optional[str]:
    """
    Best-effort resolve a projection failure's internal staging PK back to the real QBO id, via
    the entity's own staging repo (10 of 11 sync entities expose read_by_id(id).qbo_id;
    reimburse_charge does not — see _QBO_SYNC_ENTITY_META). Failure-isolated: any lookup problem
    returns None rather than raising — this is a readability nice-to-have for a reconciliation
    issue, not load-bearing for the force-advance itself.
    """
    meta = _QBO_SYNC_ENTITY_META.get(entity)
    if meta is None or meta.staging_repo is None:
        return None
    try:
        row = meta.staging_repo().read_by_id(staging_pk)
        return row.qbo_id if row else None
    except Exception:
        logger.exception(
            "Could not resolve real QBO id for %s staging id=%s; recording without it",
            entity, staging_pk,
        )
        return None


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

    def _record_bound_forced_advance(self, outcome: SyncOutcome, held: timedelta) -> None:
        try:
            from integrations.intuit.qbo.auth.business.service import QboAuthService
            from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
            from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue

            try:
                all_auths = QboAuthService().read_all()
                if not all_auths:
                    raise ValueError("No QBO authentication found")
                realm_id = all_auths[0].realm_id
            except Exception:
                logger.exception(
                    "Could not resolve QBO realm_id for watermark_hold_bound_exceeded issues; "
                    "skipping reconciliation writes (watermark will still force-advance)"
                )
                return

            held_label = str(held)
            meta = _QBO_SYNC_ENTITY_META.get(self.entity)
            entity_type = meta.label if meta else self.entity
            repo = ReconciliationIssueRepository()

            def _record(qbo_id: Optional[str], details: str) -> None:
                record_mapping_issue(
                    repo, drift_type="watermark_hold_bound_exceeded", entity_type=entity_type,
                    entity_public_id=None, qbo_id=qbo_id, realm_id=realm_id, details=details,
                    severity="critical",
                )

            # staging_failed_ids really do carry the real QBO id (the staging loop iterates the
            # raw external-API response, where .id IS the QBO id) — record them at face value.
            for qbo_id in outcome.staging_failed_ids:
                _record(qbo_id, (
                    f"QBO sync watermark hold bound exceeded for entity {self.entity}: staging "
                    f"upsert qbo_id={qbo_id} held for {held_label}; watermark force-advanced past "
                    f"it. Follow up via the QBO reconcile qbo_missing_locally detector."
                ))

            # projection_failed_ids do NOT carry the real QBO id — every sync_qbo_*.py projection
            # loop calls record_projection_error(<local_obj>.id, ...) where <local_obj> is the
            # internal qbo.<Entity> staging-table row, so .id is the staging PK, not .qbo_id (see
            # scripts/sync_qbo_bill.py's own comment: "failed_bill_ids: qbo.Bill staging PKs;
            # staging_failed_qbo_ids: QBO API Ids"). Resolve the real id via the staging repo
            # (works for 10 of 11 entities; reimburse_charge and any lookup failure fall back to
            # None, labeled honestly in details) rather than recording the staging PK as if it
            # were a QBO id — qbo.ReconciliationIssue.QboId is documented as "QBO entity id".
            for staging_pk in outcome.projection_failed_ids:
                resolved_qbo_id = _resolve_staging_qbo_id(self.entity, staging_pk)
                if resolved_qbo_id is not None:
                    _record(resolved_qbo_id, (
                        f"QBO sync watermark hold bound exceeded for entity {self.entity}: "
                        f"projection failed for qbo_id={resolved_qbo_id} (internal staging "
                        f"id={staging_pk}) held for {held_label}; watermark force-advanced past "
                        f"it. Follow up via the QBO reconcile qbo_missing_locally detector."
                    ))
                else:
                    _record(None, (
                        f"QBO sync watermark hold bound exceeded for entity {self.entity}: "
                        f"projection failed for internal qbo.{self.entity} staging id="
                        f"{staging_pk} (could not resolve the real QBO id) held for "
                        f"{held_label}; watermark force-advanced past it. Look up the staging "
                        f"row by this id to find the real QBO id, then follow up via the QBO "
                        f"reconcile qbo_missing_locally detector."
                    ))
        except Exception:
            logger.exception(
                "Failed to record watermark_hold_bound_exceeded reconciliation issues; "
                "watermark force-advance proceeds regardless"
            )

    def commit_push(self, *, skip: bool = False) -> Sync:
        """
        Persist the push-direction watermark.

        Push runs have no pull SyncOutcome, so provenance stamping does not apply.
        Uses the same write path as commit for end_date-less advances.
        """
        if not self.sync_record:
            raise RuntimeError("WatermarkRun.open() must be called before commit_push")

        if skip:
            logger.info("Skipping sync record update")
            return self.sync_record

        return self._write(self.watermark_value)

    def commit(
        self,
        outcome: SyncOutcome,
        *,
        end_date: Optional[str] = None,
        skip: bool = False,
    ) -> Sync:
        if not self.sync_record:
            raise RuntimeError("WatermarkRun.open() must be called before commit")

        if not outcome.from_service_pull:
            raise RuntimeError(
                "WatermarkRun.commit requires the SyncOutcome returned by the service's sync_from_qbo "
                "(U-220): an outcome that was hand-built, rebound, or laundered through a helper does not "
                "carry the run's staging/projection failures, so committing it would advance the watermark "
                "past records that never persisted. Use commit_push() for a non-pull watermark."
            )

        if skip:
            logger.info("Skipping sync record update")
            return self.sync_record

        if outcome.should_hold:
            # Anchor on query_start (captured once at open(), already this class's "now")
            # rather than a fresh datetime.now() read — avoids a second wall-clock read per
            # run and reuses the exact injection point tests already control.
            held = _held_duration(self.sync_record, self.query_start)
            bound = timedelta(seconds=_watermark_hold_bound_seconds())
            if held is not None and held >= bound:
                self._record_bound_forced_advance(outcome, held)
                # NOT all "qbo ids": projection_failed_ids are internal staging PKs, only
                # staging_failed_ids are real QBO ids (see _record_bound_forced_advance, which
                # resolves and records the two separately). Logged apart here for the same
                # reason — an on-call engineer greping this log must not treat a staging PK as
                # a searchable QBO id.
                logger.error(
                    "qbo.sync.watermark.bound_forced_advance entity=%s held_for=%s "
                    "blocking_staging_ids=%s blocking_qbo_ids=%s",
                    self.entity,
                    held,
                    outcome.projection_failed_ids,
                    outcome.staging_failed_ids,
                )
                if end_date:
                    return self._write(f"{end_date}T23:59:59")
                return self._write(self.watermark_value)

            reason = outcome.hold_reason()
            logger.warning(
                "Holding sync watermark (%s); window will be re-pulled next run",
                reason,
            )
            return self.sync_record

        if end_date:
            return self._write(f"{end_date}T23:59:59")

        return self._write(self.watermark_value)
