# Python Standard Library Imports
import logging
from dataclasses import dataclass, field
from typing import Generic, Optional, TypeVar

_module_logger = logging.getLogger(__name__)

T = TypeVar("T")

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.errors import is_retryable_error


@dataclass
class SyncOutcome(Generic[T]):
    """
    Shared failure vocabulary for QBO pull runs: staging (QBO → qbo.*) and
    module projection (qbo.* → dbo.*) both append into the same envelope so
    watermark commit logic has one place to decide hold vs advance.
    """

    fetched: int = 0
    staging_failed_ids: list[str] = field(default_factory=list)
    projection_failed_ids: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)
    synced: list[T] = field(default_factory=list)
    projected_count: int = 0
    # Set True ONLY by a service's sync_from_qbo; WatermarkRun.commit requires this
    # provenance marker so a hand-built or laundered outcome can never advance a watermark.
    # Default False is the safe default — an outcome nobody stamped must be refused.
    from_service_pull: bool = False

    @classmethod
    def for_service_pull(cls) -> "SyncOutcome[T]":
        """The only sanctioned way to stamp pull provenance on a service-owned outcome."""
        return cls(from_service_pull=True)

    def record_staging_failure(self, qbo_id, error=None) -> None:
        self.staging_failed_ids.append(str(qbo_id))

    def record_projection_failure(self, qbo_id, error=None) -> None:
        self.projection_failed_ids.append(str(qbo_id))

    def _record_skip(self, qbo_id, reason=None) -> None:
        """Permanent projection skips; callers must use record_projection_error."""
        self.skipped_ids.append(str(qbo_id))

    def record_staging_skip(self, qbo_id, reason=None) -> None:
        """Permanent staging-tier skip (e.g. malformed QBO row with no Id)."""
        self.skipped_ids.append(str(qbo_id))

    def record_projection_error(
        self,
        qbo_id,
        error,
        *,
        label: str = "record",
        logger: Optional[logging.Logger] = None,
    ) -> str:
        """
        Single projection-loop entry point: hold vs skip for watermark commit.

        Policy (safe default is HOLD):
        1. Retryable errors (QBO ``is_retryable`` or transient DB) → failure / hold.
        2. Plain ``ValueError`` → skip (connectors' permanent-data convention).
        3. Everything else → failure / hold.

        Rule 1 runs before rule 2 so ``raise ValueError(...) from e`` with a
        retryable cause does not advance the watermark. Asymmetry: a wrong hold
        costs one redundant re-pull of an idempotent upsert; a wrong skip loses
        the record until someone edits it in QBO again, so unknown errors hold.

        Permanent data issues (plain ``ValueError``) are classified as skip — they
        do not hold the watermark when this run commits one. Transient errors are
        classified as failure / hold when a watermark is committed for this pull.
        """
        log = logger if logger is not None else _module_logger
        if is_retryable_error(error):
            self.record_projection_failure(qbo_id, error)
            log.error(
                "Failed to project %s %s (classified retryable — holds the watermark if this run commits one): %s",
                label,
                qbo_id,
                error,
            )
            return "failure"
        if isinstance(error, ValueError):
            self._record_skip(qbo_id, str(error))
            log.info(
                "Skipped %s %s (permanent data issue — does not hold the watermark): %s",
                label,
                qbo_id,
                error,
            )
            return "skip"
        self.record_projection_failure(qbo_id, error)
        log.error(
            "Failed to project %s %s (classified retryable — holds the watermark if this run commits one): %s",
            label,
            qbo_id,
            error,
        )
        return "failure"

    def record_synced(self, record: T) -> None:
        """Append one staging upsert success (the pulled qbo.* row)."""
        self.synced.append(record)

    @property
    def synced_count(self) -> int:
        return len(self.synced)

    def record_projected(self) -> None:
        """Count one module-projection success, qbo.* → dbo.*."""
        self.projected_count += 1

    @property
    def should_hold(self) -> bool:
        """
        True when any staging or projection failure occurred.

        Skipped ids are excluded by design — permanent data gaps will not
        self-resolve on retry, so advancing the watermark must not wait on them.
        """
        return bool(self.staging_failed_ids or self.projection_failed_ids)

    @property
    def failed_count(self) -> int:
        return len(self.staging_failed_ids) + len(self.projection_failed_ids)

    def summary(self) -> dict:
        return {
            "fetched": self.fetched,
            "synced": self.synced_count,
            "projected": self.projected_count,
            "failed_count": self.failed_count,
            "staging_failed_ids": list(self.staging_failed_ids),
            "projection_failed_ids": list(self.projection_failed_ids),
            "skipped_count": len(self.skipped_ids),
            "skipped_ids": list(self.skipped_ids),
        }

    def hold_reason(self) -> Optional[str]:
        if not self.should_hold:
            return None
        parts: list[str] = []
        if self.staging_failed_ids:
            parts.append(f"staging failed: {', '.join(self.staging_failed_ids)}")
        if self.projection_failed_ids:
            parts.append(f"projection failed: {', '.join(self.projection_failed_ids)}")
        return "; ".join(parts)
