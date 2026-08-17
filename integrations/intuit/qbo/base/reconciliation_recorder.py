"""Failure-isolated reconciliation-issue recorder for QBO mapping drift."""
import logging
from typing import Optional

from integrations.intuit.qbo.base.drift_types import KNOWN_DRIFT_TYPES
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)

# Measured prod column widths (qbo.ReconciliationIssue, 2026-08):
# DriftType NVARCHAR(32), EntityType NVARCHAR(32), Severity NVARCHAR(16), Action NVARCHAR(16)
_FIELD_LIMITS = {
    "drift_type": 32,
    "entity_type": 32,
    "severity": 16,
    "action": 16,
}
_ACTION_DEFAULT = "manual_review"


def _clamp_field(field_name: str, value: object, max_len: int) -> str:
    coerced = "" if value is None else str(value)
    if len(coerced) <= max_len:
        return coerced
    logger.error(
        f"Reconciliation issue {field_name} exceeds {max_len} chars ({len(coerced)}): "
        f"{coerced!r}; truncating deterministically"
    )
    return coerced[:max_len]


def record_mapping_issue(
    repo: ReconciliationIssueRepository,
    *,
    drift_type: str,
    entity_type: str,
    entity_public_id: Optional[str],
    qbo_id: Optional[str],
    realm_id: str,
    details: str,
    severity: str = "critical",
    action: str = _ACTION_DEFAULT,
) -> None:
    """
    Insert a qbo.ReconciliationIssue for a manual-review mapping drift.

    Failure-isolated: a failed insert is logged loud but never breaks the sync.
    """
    try:
        original_drift_type = drift_type
        drift_type = _clamp_field("drift_type", drift_type, _FIELD_LIMITS["drift_type"])
        if original_drift_type not in KNOWN_DRIFT_TYPES:
            logger.error(
                f"Unregistered ReconciliationIssue DriftType {original_drift_type!r} — "
                f"add it to integrations/intuit/qbo/base/drift_types.py"
            )
        entity_type = _clamp_field("entity_type", entity_type, _FIELD_LIMITS["entity_type"])
        severity = _clamp_field("severity", severity, _FIELD_LIMITS["severity"])
        action = _clamp_field("action", action, _FIELD_LIMITS["action"])
        repo.create(
            drift_type=drift_type,
            severity=severity,
            action=action,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
            details=details,
        )
        logger.warning(details)
    except Exception as exc:
        logger.error(f"Failed to record reconciliation issue: {exc}. Details: {details}")
