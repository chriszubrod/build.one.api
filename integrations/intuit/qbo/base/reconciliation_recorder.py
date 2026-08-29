"""Failure-isolated reconciliation-issue recorder for QBO mapping drift.

Shared contract: (a) every recorder here NEVER raises — the caller (identity_fastpath
or the connector's guard) owns the raise; a raise here would abort the sync on the
exact record-and-hard-stop path; (b) callers must pass drift_type/entity_type as STRING
LITERALS at the call site — tests/test_qbo_reconciliation_recorder.py AST-scans them to
width-check against NVARCHAR(32); a variable silently drops that writer from the guard;
this file is in the guard's _SKIP_FILES because its own forwards are variable-typed.
"""
import logging
from typing import Any, Optional

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


def build_duplicate_qbo_identity_conflict_desc(
    *,
    existing_qbo_id: str,
    incoming_qbo_id,
    existing_realm_id,
    incoming_realm_id,
) -> str:
    """Shared same-QboId-vs-different-QboId phrasing for customer/project duplicates."""
    if existing_qbo_id == incoming_qbo_id:
        return (
            f"the SAME QboId {existing_qbo_id} but a DIFFERENT RealmId "
            f"({existing_realm_id!r} vs incoming {incoming_realm_id!r})"
        )
    return f"a DIFFERENT QboId {existing_qbo_id} (realm {existing_realm_id!r})"


def record_identity_mapping_conflict(
    repo: ReconciliationIssueRepository,
    *,
    drift_type: str,
    entity_type: str,
    mapping_label: str,
    qbo_label: str,
    dbo_id: int,
    qbo_row_id: int,
    realm_id: str,
    local_side_mapping: Any = None,
    qbo_side_mapping: Any = None,
    qbo_side_local_fk_attr: str,
    local_side_qbo_fk_attr: str,
    raw_qbo_id: Any = None,
    raw_realm_id: Any = None,
    raw_qbo_line_id: Any = None,
    qbo_side_note: Optional[str] = None,
    line_level: bool = False,
) -> None:
    """
    Shapes A + B: dbo-identity vs mapping-table split. Covers all three conflict shapes
    (qbo-side only, local-side only, or both) in ONE issue — the two independent `if`s
    are that invariant; do not merge to elif. Most plausibly left by an identity-theft
    event (Set<Entity>QboIdentity theft-clear nulls the losing row's QboId/RealmId without
    touching the mapping table). Per-connector wrappers were renamed _raise_* -> _record_*
    because none of them raise. Never raises.
    """
    try:
        realm_id = realm_id or ""
        if line_level:
            identity_fragment = f"QboLineId={raw_qbo_line_id}"
        else:
            identity_fragment = f"QboId={raw_qbo_id}, RealmId={raw_realm_id}"
        parts = [
            f"{mapping_label} identity conflict. dbo.{entity_type} {dbo_id} carries native QBO "
            f"identity for {qbo_label} {qbo_row_id} ({identity_fragment})."
        ]
        if qbo_side_mapping:
            other_local_id = getattr(qbo_side_mapping, qbo_side_local_fk_attr)
            base = (
                f"qbo-side: the mapping table still binds that same {qbo_label} to a "
                f"DIFFERENT {entity_type} {other_local_id} (mapping {qbo_side_mapping.id})"
            )
            parts.append(f"{base} — {qbo_side_note}." if qbo_side_note else f"{base}.")
        if local_side_mapping:
            other_qbo_row_id = getattr(local_side_mapping, local_side_qbo_fk_attr)
            parts.append(
                f"local-side: {entity_type} {dbo_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT {qbo_label} "
                f"{other_qbo_row_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        raw = raw_qbo_line_id if line_level else raw_qbo_id
        record_mapping_issue(
            repo,
            drift_type=drift_type,
            entity_type=entity_type,
            entity_public_id=None,
            qbo_id=str(raw) if raw else None,
            realm_id=realm_id,
            details=" ".join(parts),
        )
    except Exception as exc:
        logger.error(
            f"Failed to record identity mapping conflict: {exc}. drift_type={drift_type}"
        )


def record_duplicate_identity_conflict(
    repo: ReconciliationIssueRepository,
    *,
    drift_type: str,
    entity_type: str,
    qbo_id: Optional[str],
    realm_id: str,
    entity_public_id: Optional[str],
    details: str,
) -> None:
    """
    Shape C: name/number-match duplicate identity. Callers own their divergent details
    wording; this centralizes the never-raise record tail. severity/action stay defaults.
    Never raises.
    """
    try:
        record_mapping_issue(
            repo,
            drift_type=drift_type,
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
            details=details,
        )
    except Exception as exc:
        logger.error(
            f"Failed to record duplicate identity conflict: {exc}. drift_type={drift_type}"
        )
