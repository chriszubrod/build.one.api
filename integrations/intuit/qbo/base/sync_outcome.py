# Python Standard Library Imports
import logging
from dataclasses import dataclass, field
from typing import Optional

_module_logger = logging.getLogger(__name__)

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.errors import is_retryable_error


@dataclass
class SyncOutcome:
    """
    Shared failure vocabulary for QBO pull runs: staging (QBO → qbo.*) and
    module projection (qbo.* → dbo.*) both append into the same envelope so
    watermark commit logic has one place to decide hold vs advance.
    """

    fetched: int = 0
    staging_failed_ids: list[str] = field(default_factory=list)
    projection_failed_ids: list[str] = field(default_factory=list)
    skipped_ids: list[str] = field(default_factory=list)
    synced_count: int = 0

    def record_staging_failure(self, qbo_id, error=None) -> None:
        self.staging_failed_ids.append(str(qbo_id))

    def record_projection_failure(self, qbo_id, error=None) -> None:
        self.projection_failed_ids.append(str(qbo_id))

    def _record_skip(self, qbo_id, reason=None) -> None:
        """Permanent data issues (e.g. vendor not mapped); never triggers a hold."""
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

        Permanent data issues (plain ``ValueError``) will never self-resolve on
        retry, so they must not block the watermark. Transient errors MUST block
        the watermark so the record is re-fetched on the next run.
        """
        log = logger if logger is not None else _module_logger
        if is_retryable_error(error):
            self.record_projection_failure(qbo_id, error)
            log.error(
                "Failed to project %s %s (watermark holds for retry): %s",
                label,
                qbo_id,
                error,
            )
            return "failure"
        if isinstance(error, ValueError):
            self._record_skip(qbo_id, str(error))
            log.info(
                "Skipped %s %s (permanent data issue, watermark advances): %s",
                label,
                qbo_id,
                error,
            )
            return "skip"
        self.record_projection_failure(qbo_id, error)
        log.error(
            "Failed to project %s %s (watermark holds for retry): %s",
            label,
            qbo_id,
            error,
        )
        return "failure"

    def record_synced(self) -> None:
        self.synced_count += 1

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


def coerce_outcome(outcome: Optional["SyncOutcome"]) -> "SyncOutcome":
    return outcome if outcome is not None else SyncOutcome()
